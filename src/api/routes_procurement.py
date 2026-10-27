"""FastAPI endpoints for multi-tenant autonomous procurement operations.

Enforces OWASP API Top 10 controls: BOLA (API1), broken authentication (API2),
object property level authorization via strict schemas (API3), rate limits (API4),
function level authorization via RBAC (API5) and business-flow protection (API6)
through duplicate detection, segregation of duties, and race-free state transitions.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Request, status
from sqlalchemy.orm import Session

from src.config import settings
from src.database import get_db
from src.models import AgentAuditLog, Invoice, PurchaseOrder, User, Vendor
from src.schemas import (
    AuditLogItem,
    AuditVerifyResponse,
    ContractClauseResponse,
    ContractSearchRequest,
    HITLDecisionRequest,
    HITLDecisionResponse,
    InvoiceDetailResponse,
    InvoiceReconcileRequest,
    InvoiceReconciliationResponse,
    InvoiceSummaryItem,
    LogoutResponse,
    PrincipalResponse,
    PurchaseOrderItem,
    ROIMetricsResponse,
    RoutingStatus,
    TokenRequest,
    TokenResponse,
)
from src.security import (
    OPERATOR_ROLES,
    ROLE_AUDITOR,
    ROLE_CPO,
    create_access_token,
    get_current_user,
    get_owned_resource,
    limiter,
    login_tracker,
    require_roles,
    revoked_tokens,
    sanitize_untrusted_text,
    verify_password,
)
from src.services.audit_ledger import append_audit_log, verify_chain
from src.services.contract_rag import LocalContractRAG
from src.services.matching_engine import reconcile_invoice
from src.services.roi_calculator import calculate_tenant_roi

router = APIRouter()

MAX_DB_INT = 2_147_483_647

ROLE_PERMISSIONS: Dict[str, List[str]] = {
    "procurement_specialist": ["invoices:read", "invoices:reconcile", "invoices:decide", "contracts:search", "metrics:read"],
    "cpo": ["invoices:read", "invoices:reconcile", "invoices:decide", "invoices:decide_high_value", "contracts:search", "metrics:read", "audit:read"],
    "auditor": ["invoices:read", "contracts:search", "metrics:read", "audit:read"],
}


def _split_flags(raw: Optional[str]) -> List[str]:
    return [flag for flag in (raw or "").split(",") if flag]


# ---------------------------------------------------------------------------
# Authentication Endpoints (OWASP API2 Broken Auth Defense)
# ---------------------------------------------------------------------------

@router.post(
    "/auth/token",
    response_model=TokenResponse,
    summary="Issue JWT access token with tenant isolation claim",
)
@limiter.limit(settings.RATE_LIMIT_AUTH)
def login_for_access_token(
    request: Request,
    credentials: TokenRequest = Body(...),
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Issue a short-lived HS256 JWT.

    Every failure path (unknown user, wrong password, inactive or locked account) returns
    the same generic 401 after a full bcrypt comparison, preventing user enumeration via
    message or timing differences.
    """
    generic_failure = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid username or password",
        headers={"WWW-Authenticate": "Bearer"},
    )

    user = db.query(User).filter(User.username == credentials.username).first()
    password_ok = verify_password(credentials.password, user.hashed_password if user else None)

    if login_tracker.is_locked(credentials.username):
        raise generic_failure
    if not user or not password_ok or not user.is_active:
        login_tracker.register_failure(credentials.username)
        raise generic_failure

    login_tracker.reset(credentials.username)
    access_token = create_access_token(
        {"sub": user.username, "tenant_id": user.tenant_id, "role": user.role}
    )

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",  # nosec B106
        expires_in_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        tenant_id=user.tenant_id,
        role=user.role,
    )


@router.get("/auth/me", response_model=PrincipalResponse, summary="Describe the authenticated principal")
def read_current_principal(current_user: Dict[str, Any] = Depends(get_current_user)) -> PrincipalResponse:
    """Return identity and effective permissions derived solely from the verified token."""
    return PrincipalResponse(
        username=current_user["sub"],
        tenant_id=current_user["tenant_id"],
        role=current_user["role"],
        permissions=ROLE_PERMISSIONS.get(current_user["role"], []),
        token_expires_at=datetime.fromtimestamp(current_user["exp"], tz=timezone.utc),
    )


@router.post("/auth/logout", response_model=LogoutResponse, summary="Revoke the presented access token")
def logout(current_user: Dict[str, Any] = Depends(get_current_user)) -> LogoutResponse:
    """Add the token's jti to the server-side denylist until it would have expired."""
    revoked_tokens.revoke(current_user["jti"], float(current_user["exp"]))
    return LogoutResponse(revoked=True, message="Access token revoked")


# ---------------------------------------------------------------------------
# Invoice Reconciliation & HITL Routing (FR-01 to FR-05)
# ---------------------------------------------------------------------------

@router.post(
    "/invoices/reconcile",
    response_model=InvoiceReconciliationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Deterministic 3-Way Match & Contract SLA Audit",
    responses={409: {"description": "Duplicate invoice number"}},
)
@limiter.limit(settings.RATE_LIMIT_RECONCILE)
def reconcile_vendor_invoice(
    request: Request,
    payload: InvoiceReconcileRequest = Body(...),
    current_user: Dict[str, Any] = Depends(require_roles(*OPERATOR_ROLES)),
    db: Session = Depends(get_db),
) -> InvoiceReconciliationResponse:
    """Perform deterministic 3-way match, SLA late penalty audit, risk screening and HITL routing."""
    return reconcile_invoice(payload, current_user["tenant_id"], db, actor=current_user["sub"])


@router.get(
    "/invoices",
    response_model=List[InvoiceSummaryItem],
    summary="List tenant invoices with pagination and status filter",
)
def list_invoices(
    routing_status: Optional[RoutingStatus] = Query(None, description="Filter by routing status"),
    skip: int = Query(0, ge=0, le=100_000),
    limit: int = Query(50, ge=1, le=100),
    current_user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[InvoiceSummaryItem]:
    """Retrieve paginated invoices strictly scoped to authenticated tenant."""
    tenant_id = current_user["tenant_id"]
    query = (
        db.query(
            Invoice.id,
            Invoice.tenant_id,
            Invoice.invoice_number,
            PurchaseOrder.po_number,
            Vendor.name.label("vendor_name"),
            Invoice.subtotal_usd,
            Invoice.variance_pct,
            Invoice.sla_penalty_usd,
            Invoice.final_payable_usd,
            Invoice.routing_status,
            Invoice.risk_flags,
            Invoice.created_at,
        )
        .join(PurchaseOrder, Invoice.po_id == PurchaseOrder.id)
        .join(Vendor, PurchaseOrder.vendor_id == Vendor.id)
        .filter(Invoice.tenant_id == tenant_id)
    )

    if routing_status:
        query = query.filter(Invoice.routing_status == routing_status)

    results = query.order_by(Invoice.id.desc()).offset(skip).limit(limit).all()

    return [
        InvoiceSummaryItem(
            id=r.id,
            tenant_id=r.tenant_id,
            invoice_number=r.invoice_number,
            po_number=r.po_number,
            vendor_name=r.vendor_name,
            subtotal_usd=r.subtotal_usd,
            variance_pct=r.variance_pct,
            sla_penalty_usd=r.sla_penalty_usd,
            final_payable_usd=r.final_payable_usd,
            routing_status=r.routing_status,
            risk_flags=_split_flags(r.risk_flags),
            created_at=r.created_at,
        )
        for r in results
    ]


@router.get(
    "/invoices/{invoice_id}",
    response_model=InvoiceDetailResponse,
    summary="Fetch invoice details with strict BOLA tenant ownership enforcement",
)
def get_invoice_detail(
    invoice_id: int = Path(..., ge=1, le=MAX_DB_INT, description="Invoice identifier"),
    current_user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InvoiceDetailResponse:
    """OWASP API1 BOLA Control: Guarantees user can only access invoices within their tenant."""
    invoice: Invoice = get_owned_resource(Invoice, invoice_id, current_user, db)
    po: PurchaseOrder = invoice.purchase_order
    vendor: Vendor = po.vendor

    return InvoiceDetailResponse(
        id=invoice.id,
        tenant_id=invoice.tenant_id,
        invoice_number=invoice.invoice_number,
        po_number=po.po_number,
        vendor_code=vendor.vendor_code,
        vendor_name=vendor.name,
        ordered_qty=po.ordered_qty,
        billed_qty=invoice.billed_qty,
        agreed_unit_price=po.agreed_unit_price,
        billed_unit_price=invoice.billed_unit_price,
        expected_delivery_date=po.expected_delivery_date,
        actual_delivery_date=invoice.actual_delivery_date,
        subtotal_usd=invoice.subtotal_usd,
        variance_pct=invoice.variance_pct,
        sla_penalty_usd=invoice.sla_penalty_usd,
        final_payable_usd=invoice.final_payable_usd,
        routing_status=invoice.routing_status,
        approved_by=invoice.approved_by,
        rejection_reason=invoice.rejection_reason,
        vendor_notes=invoice.vendor_notes,
        risk_flags=_split_flags(invoice.risk_flags),
        decided_at=invoice.decided_at,
        created_at=invoice.created_at,
    )


@router.post(
    "/invoices/{invoice_id}/decision",
    response_model=HITLDecisionResponse,
    summary="Record Human-in-the-Loop approval or rejection",
    responses={403: {"description": "Role not allowed or CPO sign-off required"}, 409: {"description": "Invoice already decided"}},
)
@limiter.limit(settings.RATE_LIMIT_DECISION)
def record_hitl_decision(
    request: Request,
    invoice_id: int = Path(..., ge=1, le=MAX_DB_INT, description="Invoice identifier"),
    payload: HITLDecisionRequest = Body(...),
    current_user: Dict[str, Any] = Depends(require_roles(*OPERATOR_ROLES)),
    db: Session = Depends(get_db),
) -> HITLDecisionResponse:
    """Record a human decision on a pending exception.

    Controls: BOLA ownership check, segregation of duties (CPO sign-off above the
    high-value threshold), and an atomic compare-and-set on `routing_status` so two
    concurrent reviewers cannot both decide (or re-decide) the same invoice.
    """
    invoice: Invoice = get_owned_resource(Invoice, invoice_id, current_user, db)
    reviewer = current_user["sub"]
    tenant_id = current_user["tenant_id"]

    if invoice.routing_status != "HITL_PENDING":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Invoice is not pending review (current state '{invoice.routing_status}')",
        )

    if (
        payload.decision == "APPROVE"
        and invoice.subtotal_usd > settings.CPO_APPROVAL_THRESHOLD_USD
        and current_user["role"] != ROLE_CPO
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Segregation of duties: invoices above ${settings.CPO_APPROVAL_THRESHOLD_USD:,.0f} "
                "require CPO approval"
            ),
        )

    notes = sanitize_untrusted_text(payload.reviewer_notes) or None
    now = datetime.now(timezone.utc)
    if payload.decision == "APPROVE":
        new_values = {
            "routing_status": "APPROVED",
            "approved_by": reviewer,
            "rejection_reason": None,
            "decided_at": now.replace(tzinfo=None),
        }
        po_status = "MATCHED"
        msg = f"Invoice #{invoice.invoice_number} approved by {reviewer}"
    else:
        reason = (notes or "Rejected by human reviewer")[:255]
        new_values = {
            "routing_status": "REJECTED",
            "approved_by": None,
            "rejection_reason": reason,
            "decided_at": now.replace(tzinfo=None),
        }
        po_status = "DISCREPANCY"
        msg = f"Invoice #{invoice.invoice_number} rejected by {reviewer}: {reason}"

    updated = (
        db.query(Invoice)
        .filter(
            Invoice.id == invoice.id,
            Invoice.tenant_id == tenant_id,
            Invoice.routing_status == "HITL_PENDING",
        )
        .update(new_values, synchronize_session=False)
    )
    if updated != 1:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Invoice was decided concurrently by another reviewer",
        )

    db.query(PurchaseOrder).filter(
        PurchaseOrder.id == invoice.po_id, PurchaseOrder.tenant_id == tenant_id
    ).update({"status": po_status}, synchronize_session=False)

    append_audit_log(
        db,
        tenant_id=tenant_id,
        invoice_id=invoice.id,
        action_taken=f"HITL_{payload.decision}",
        details=f"{msg} | Notes: {notes or 'N/A'}",
        actor=reviewer,
    )
    db.commit()

    return HITLDecisionResponse(
        invoice_id=invoice.id,
        routing_status=new_values["routing_status"],  # type: ignore[arg-type]
        decided_by=reviewer,
        reviewer_notes=notes,
        timestamp=now,
        message=msg,
    )


# ---------------------------------------------------------------------------
# Unit Economics & Contract RAG Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/metrics/roi",
    response_model=ROIMetricsResponse,
    summary="Autonomous procurement agent unit economics ledger",
)
def get_roi_metrics(
    current_user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ROIMetricsResponse:
    """Return aggregated OpEx savings, SLA recoveries, and AI unit economics."""
    return calculate_tenant_roi(current_user["tenant_id"], db)


@router.post(
    "/contracts/search",
    response_model=List[ContractClauseResponse],
    summary="Search supplier contract clauses with local vector similarity",
)
@limiter.limit(settings.RATE_LIMIT_SEARCH)
def search_contract_clauses(
    request: Request,
    payload: ContractSearchRequest = Body(...),
    current_user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[ContractClauseResponse]:
    """Retrieve relevant contract clauses using local deterministic vector search."""
    return LocalContractRAG.search_clauses(
        db=db,
        tenant_id=current_user["tenant_id"],
        query=payload.query,
        vendor_id=payload.vendor_id,
        top_k=payload.top_k,
    )


@router.get(
    "/purchase-orders",
    response_model=List[PurchaseOrderItem],
    summary="List tenant purchase orders",
)
def list_purchase_orders(
    limit: int = Query(20, ge=1, le=100),
    current_user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[PurchaseOrderItem]:
    """List purchase orders within the tenant."""
    tenant_id = current_user["tenant_id"]
    pos = (
        db.query(PurchaseOrder)
        .filter(PurchaseOrder.tenant_id == tenant_id)
        .order_by(PurchaseOrder.id.desc())
        .limit(limit)
        .all()
    )
    return [
        PurchaseOrderItem(
            id=p.id,
            po_number=p.po_number,
            vendor_name=p.vendor.name,
            item_description=p.item_description,
            ordered_qty=p.ordered_qty,
            agreed_unit_price=p.agreed_unit_price,
            expected_delivery_date=p.expected_delivery_date,
            status=p.status,
        )
        for p in pos
    ]


# ---------------------------------------------------------------------------
# Tamper-Evident Audit Ledger
# ---------------------------------------------------------------------------

@router.get(
    "/audit/logs",
    response_model=List[AuditLogItem],
    summary="Read the tenant's hash-chained audit ledger (CPO / auditor)",
)
def list_audit_logs(
    skip: int = Query(0, ge=0, le=1_000_000),
    limit: int = Query(50, ge=1, le=100),
    current_user: Dict[str, Any] = Depends(require_roles(ROLE_CPO, ROLE_AUDITOR)),
    db: Session = Depends(get_db),
) -> List[AuditLogItem]:
    """Newest-first page of sealed audit entries for the caller's tenant only."""
    rows = (
        db.query(AgentAuditLog)
        .filter(AgentAuditLog.tenant_id == current_user["tenant_id"])
        .order_by(AgentAuditLog.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [AuditLogItem.model_validate(row) for row in rows]


@router.get(
    "/audit/verify",
    response_model=AuditVerifyResponse,
    summary="Recompute and verify the tenant's audit hash chain (CPO / auditor)",
)
def verify_audit_ledger(
    current_user: Dict[str, Any] = Depends(require_roles(ROLE_CPO, ROLE_AUDITOR)),
    db: Session = Depends(get_db),
) -> AuditVerifyResponse:
    """Detects any modified, removed or reordered audit entry."""
    result = verify_chain(db, current_user["tenant_id"])
    return AuditVerifyResponse(
        tenant_id=current_user["tenant_id"],
        verified_at=datetime.now(timezone.utc),
        **result,
    )
