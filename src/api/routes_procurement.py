"""FastAPI endpoints for multi-tenant autonomous procurement operations.

Enforces OWASP API Top 10 controls (BOLA, broken auth, rate limits, strict schema validation).
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from src.config import settings
from src.database import get_db
from src.models import AgentAuditLog, Invoice, PurchaseOrder, User, Vendor
from src.schemas import (
    ContractClauseResponse,
    ContractSearchRequest,
    HITLDecisionRequest,
    HITLDecisionResponse,
    InvoiceDetailResponse,
    InvoiceReconcileRequest,
    InvoiceReconciliationResponse,
    InvoiceSummaryItem,
    ROIMetricsResponse,
    TokenRequest,
    TokenResponse,
)
from src.security import (
    create_access_token,
    get_current_user,
    get_owned_resource,
    limiter,
    verify_password,
)
from src.services.contract_rag import LocalContractRAG
from src.services.matching_engine import reconcile_invoice
from src.services.roi_calculator import calculate_tenant_roi

router = APIRouter()


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
    """Issue short-lived HS256 JWT access token. Generic 401 response prevents user enumeration."""
    user = db.query(User).filter(User.username == credentials.username).first()
    if not user or not verify_password(credentials.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated",
        )

    token_data = {
        "sub": user.username,
        "tenant_id": user.tenant_id,
        "role": user.role,
    }
    access_token = create_access_token(token_data)

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        tenant_id=user.tenant_id,
        role=user.role,
    )


# ---------------------------------------------------------------------------
# Invoice Reconciliation & HITL Routing (FR-01 to FR-05)
# ---------------------------------------------------------------------------

@router.post(
    "/invoices/reconcile",
    response_model=InvoiceReconciliationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Deterministic 3-Way Match & Contract SLA Audit",
)
@limiter.limit(settings.RATE_LIMIT_RECONCILE)
def reconcile_vendor_invoice(
    request: Request,
    payload: InvoiceReconcileRequest = Body(...),
    current_user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InvoiceReconciliationResponse:
    """Perform deterministic 3-way match, SLA late penalty audit, and HITL routing."""
    tenant_id = current_user["tenant_id"]
    return reconcile_invoice(payload, tenant_id, db)


@router.get(
    "/invoices",
    response_model=List[InvoiceSummaryItem],
    summary="List tenant invoices with pagination and status filter",
)
def list_invoices(
    routing_status: Optional[str] = Query(None, description="Filter by status: AUTO_APPROVED, HITL_PENDING, APPROVED, REJECTED"),
    skip: int = Query(0, ge=0),
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
    invoice_id: int,
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
        created_at=invoice.created_at,
    )


@router.post(
    "/invoices/{invoice_id}/decision",
    response_model=HITLDecisionResponse,
    summary="Record Human-in-the-Loop approval or rejection",
)
def record_hitl_decision(
    invoice_id: int,
    payload: HITLDecisionRequest = Body(...),
    current_user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HITLDecisionResponse:
    """Record human specialist approval or rejection for discrepancy invoice with BOLA verification."""
    invoice: Invoice = get_owned_resource(Invoice, invoice_id, current_user, db)
    reviewer = current_user["sub"]

    if invoice.routing_status not in ("HITL_PENDING", "AUTO_APPROVED"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invoice #{invoice_id} is already in finalized state '{invoice.routing_status}'",
        )

    now = datetime.now(timezone.utc)
    if payload.decision == "APPROVE":
        invoice.routing_status = "APPROVED"
        invoice.approved_by = reviewer
        invoice.rejection_reason = None
        invoice.purchase_order.status = "MATCHED"
        msg = f"Invoice #{invoice.invoice_number} approved by {reviewer}"
    else:
        invoice.routing_status = "REJECTED"
        invoice.rejection_reason = payload.reviewer_notes or "Rejected by human reviewer"
        invoice.purchase_order.status = "DISCREPANCY"
        msg = f"Invoice #{invoice.invoice_number} rejected by {reviewer}: {invoice.rejection_reason}"

    audit_log = AgentAuditLog(
        tenant_id=current_user["tenant_id"],
        invoice_id=invoice.id,
        action_taken=f"HITL_{payload.decision}",
        details=f"{msg} | Notes: {payload.reviewer_notes or 'N/A'}",
        tokens_used=0,
        compute_cost_usd=0.0,
        labor_saved_usd=0.0,
    )
    db.add(audit_log)
    db.commit()

    return HITLDecisionResponse(
        invoice_id=invoice.id,
        routing_status=invoice.routing_status,  # type: ignore[arg-type]
        decided_by=reviewer,
        reviewer_notes=payload.reviewer_notes,
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
def search_contract_clauses(
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
    summary="List available Purchase Orders for testing reconciliation",
)
def list_purchase_orders(
    current_user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """List purchase orders within the tenant."""
    tenant_id = current_user["tenant_id"]
    pos = (
        db.query(PurchaseOrder)
        .filter(PurchaseOrder.tenant_id == tenant_id)
        .order_by(PurchaseOrder.id.desc())
        .limit(20)
        .all()
    )
    return [
        {
            "id": p.id,
            "po_number": p.po_number,
            "vendor_name": p.vendor.name,
            "item_description": p.item_description,
            "ordered_qty": p.ordered_qty,
            "agreed_unit_price": p.agreed_unit_price,
            "expected_delivery_date": p.expected_delivery_date.isoformat(),
            "status": p.status,
        }
        for p in pos
    ]
