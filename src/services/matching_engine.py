"""Deterministic 3-Way Invoice Matching & SLA Penalty Engine.

Implements automated reconciliation across Purchase Orders, Goods Receipts,
and Invoices with SLA penalty calculations, fraud / prompt-injection risk
signals, and HITL routing guardrails.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.config import settings
from src.models import Invoice, PurchaseOrder, Vendor
from src.schemas import (
    InvoiceReconcileRequest,
    InvoiceReconciliationResponse,
    MatchingBreakdown,
    RiskAssessment,
)
from src.security import assess_untrusted_text
from src.services.audit_ledger import append_audit_log

SETTLED_STATUSES = ("AUTO_APPROVED", "APPROVED")


@dataclass(frozen=True)
class MatchResult:
    """Pure deterministic outcome of a 3-way match (no I/O, fully unit-testable)."""

    qty_matched: bool
    price_variance_pct: float
    price_matched: bool
    subtotal_usd: float
    days_late: int
    applied_penalty_pct: float
    sla_penalty_usd: float
    final_payable_usd: float
    hitl_reasons: List[str]

    @property
    def routing_status(self) -> str:
        return "HITL_PENDING" if self.hitl_reasons else "AUTO_APPROVED"

    @property
    def routing_reason(self) -> str:
        if not self.hitl_reasons:
            return "Deterministic 3-way match passed: price variance, quantity, and contract terms verified"
        return "; ".join(self.hitl_reasons)


def compute_match(
    *,
    ordered_qty: int,
    billed_qty: int,
    agreed_unit_price: float,
    billed_unit_price: float,
    expected_delivery: datetime,
    actual_delivery: datetime,
    daily_penalty_rate: float,
    max_penalty_cap: float,
    risk_flags: Optional[List[str]] = None,
) -> MatchResult:
    """Governing equations for variance, SLA penalty and HITL routing (FR-02, FR-04, FR-05)."""
    qty_matched = ordered_qty == billed_qty
    price_variance_pct = round(((billed_unit_price - agreed_unit_price) / agreed_unit_price) * 100.0, 4)
    price_matched = abs(price_variance_pct) <= 0.001
    subtotal_usd = round(billed_qty * billed_unit_price, 2)

    days_late = max(0, (actual_delivery.date() - expected_delivery.date()).days)
    applied_penalty_pct = min(max_penalty_cap, days_late * daily_penalty_rate)
    sla_penalty_usd = round(subtotal_usd * applied_penalty_pct, 2)
    final_payable_usd = round(max(0.0, subtotal_usd - sla_penalty_usd), 2)

    reasons: List[str] = []
    flags = risk_flags or []
    injection_flags = [f for f in flags if f not in ("duplicate_billing_suspected", "inactive_vendor")]
    if injection_flags:
        reasons.append(
            "Security hold: untrusted vendor notes triggered prompt-injection firewall "
            f"({', '.join(injection_flags)})"
        )
    if abs(price_variance_pct) > settings.VARIANCE_TOLERANCE_PCT:
        reasons.append(
            f"Price variance {price_variance_pct:+.2f}% exceeds threshold "
            f"of ±{settings.VARIANCE_TOLERANCE_PCT}%"
        )
    if subtotal_usd > settings.HIGH_VALUE_THRESHOLD_USD:
        reasons.append(
            f"High-value invoice (${subtotal_usd:,.2f}) exceeds human approval "
            f"threshold of ${settings.HIGH_VALUE_THRESHOLD_USD:,.2f}"
        )
    if not qty_matched:
        reasons.append(f"Quantity mismatch: Billed {billed_qty} vs Ordered {ordered_qty}")
    if "duplicate_billing_suspected" in flags:
        reasons.append("Possible duplicate billing: this PO already has a settled invoice")
    if "inactive_vendor" in flags:
        reasons.append("Vendor master record is inactive")

    return MatchResult(
        qty_matched=qty_matched,
        price_variance_pct=price_variance_pct,
        price_matched=price_matched,
        subtotal_usd=subtotal_usd,
        days_late=days_late,
        applied_penalty_pct=applied_penalty_pct,
        sla_penalty_usd=sla_penalty_usd,
        final_payable_usd=final_payable_usd,
        hitl_reasons=reasons,
    )


def reconcile_invoice(
    request: InvoiceReconcileRequest,
    current_user_tenant_id: str,
    db: Session,
    actor: str = "AI_AGENT_ENGINE",
) -> InvoiceReconciliationResponse:
    """Execute deterministic 3-way reconciliation with contract SLA penalty deduction.

    Enforces:
    1. Tenant boundary validation on Purchase Order (BOLA).
    2. Duplicate invoice rejection (HTTP 409) and duplicate-billing detection.
    3. Zero-variance quantity matching check.
    4. Price variance calculation against contracted rate.
    5. Delivery timestamp SLA penalty calculation using vendor terms.
    6. Prompt-injection firewall on untrusted vendor notes.
    7. HITL routing guardrails (variance > 2%, value > $10,000, qty mismatch, risk flags).
    """
    start_time = time.perf_counter()

    actual_date = request.actual_delivery_date
    now_utc = datetime.now(timezone.utc)
    actual_cmp = (
        actual_date if actual_date.tzinfo else actual_date.replace(tzinfo=timezone.utc)
    ).astimezone(timezone.utc)
    if actual_cmp > now_utc + timedelta(days=settings.MAX_FUTURE_DELIVERY_DAYS):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="actual_delivery_date cannot be in the future",
        )

    po: Optional[PurchaseOrder] = (
        db.query(PurchaseOrder)
        .filter(
            PurchaseOrder.po_number == request.po_number,
            PurchaseOrder.tenant_id == current_user_tenant_id,
        )
        .first()
    )
    if not po:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Purchase Order not found or inaccessible for tenant",
        )

    vendor: Optional[Vendor] = (
        db.query(Vendor)
        .filter(Vendor.id == po.vendor_id, Vendor.tenant_id == current_user_tenant_id)
        .first()
    )
    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vendor contract for Purchase Order not found",
        )

    duplicate = (
        db.query(Invoice.id)
        .filter(
            Invoice.tenant_id == current_user_tenant_id,
            Invoice.invoice_number == request.invoice_number,
        )
        .first()
    )
    if duplicate:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Duplicate invoice number: this invoice was already submitted for reconciliation",
        )

    sanitized_notes, risk_flags = assess_untrusted_text(request.vendor_notes)
    prompt_injection_detected = bool(risk_flags)

    already_settled = (
        db.query(Invoice.id)
        .filter(
            Invoice.tenant_id == current_user_tenant_id,
            Invoice.po_id == po.id,
            Invoice.routing_status.in_(SETTLED_STATUSES),
        )
        .first()
    )
    if already_settled:
        risk_flags.append("duplicate_billing_suspected")
    if not vendor.is_active:
        risk_flags.append("inactive_vendor")

    match = compute_match(
        ordered_qty=po.ordered_qty,
        billed_qty=request.billed_qty,
        agreed_unit_price=po.agreed_unit_price,
        billed_unit_price=request.billed_unit_price,
        expected_delivery=po.expected_delivery_date,
        actual_delivery=actual_cmp,
        daily_penalty_rate=vendor.daily_late_penalty_pct,
        max_penalty_cap=vendor.max_penalty_cap_pct,
        risk_flags=risk_flags,
    )
    routing_status = match.routing_status
    routing_reason = match.routing_reason

    if routing_status == "AUTO_APPROVED":
        po.status = "MATCHED"

    invoice = Invoice(
        tenant_id=current_user_tenant_id,
        invoice_number=request.invoice_number,
        po_id=po.id,
        billed_qty=request.billed_qty,
        billed_unit_price=request.billed_unit_price,
        actual_delivery_date=actual_cmp.replace(tzinfo=None),
        subtotal_usd=match.subtotal_usd,
        variance_pct=match.price_variance_pct,
        sla_penalty_usd=match.sla_penalty_usd,
        final_payable_usd=match.final_payable_usd,
        routing_status=routing_status,
        approved_by="AI_AGENT_ENGINE" if routing_status == "AUTO_APPROVED" else None,
        vendor_notes=sanitized_notes or None,
        risk_flags=",".join(sorted(set(risk_flags)))[:255] or None,
    )
    db.add(invoice)
    try:
        db.flush()  # Populate invoice.id; the unique index closes the duplicate race window
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Duplicate invoice number: this invoice was already submitted for reconciliation",
        )

    latency_ms = round((time.perf_counter() - start_time) * 1000.0, 2)

    # Token economics model based on prompt payload size
    tokens_consumed = 350 + int(len(sanitized_notes or "") * 0.75)
    compute_cost = (tokens_consumed / 1000.0) * settings.TOKEN_COST_PER_1K_USD
    labor_saved = (settings.MANUAL_MINUTES_PER_INVOICE / 60.0) * settings.HOURLY_ANALYST_WAGE_USD

    append_audit_log(
        db,
        tenant_id=current_user_tenant_id,
        invoice_id=invoice.id,
        action_taken=f"RECONCILE_{routing_status}",
        details=(
            f"{routing_reason} | SLA Penalty: ${match.sla_penalty_usd:,.2f} | "
            f"Latency: {latency_ms}ms | Submitted by: {actor}"
        ),
        tokens_used=tokens_consumed,
        compute_cost_usd=compute_cost,
        labor_saved_usd=labor_saved,
        actor=actor,
    )
    db.commit()
    db.refresh(invoice)

    breakdown = MatchingBreakdown(
        ordered_qty=po.ordered_qty,
        billed_qty=request.billed_qty,
        qty_matched=match.qty_matched,
        agreed_unit_price=po.agreed_unit_price,
        billed_unit_price=request.billed_unit_price,
        price_variance_pct=match.price_variance_pct,
        price_matched=match.price_matched,
        days_late=match.days_late,
        sla_daily_penalty_rate_pct=vendor.daily_late_penalty_pct * 100.0,
        sla_penalty_applied_pct=match.applied_penalty_pct * 100.0,
        sla_penalty_usd=match.sla_penalty_usd,
    )

    return InvoiceReconciliationResponse(
        invoice_id=invoice.id,
        po_number=po.po_number,
        invoice_number=invoice.invoice_number,
        subtotal_usd=invoice.subtotal_usd,
        variance_pct=invoice.variance_pct,
        sla_penalty_usd=invoice.sla_penalty_usd,
        final_payable_usd=invoice.final_payable_usd,
        routing_status=invoice.routing_status,  # type: ignore[arg-type]
        routing_reason=routing_reason,
        matching_breakdown=breakdown,
        risk_assessment=RiskAssessment(
            risk_flags=sorted(set(risk_flags)),
            prompt_injection_detected=prompt_injection_detected,
            duplicate_invoice_suspected="duplicate_billing_suspected" in risk_flags,
            sanitized_vendor_notes=sanitized_notes or None,
        ),
        tokens_consumed=tokens_consumed,
        inference_latency_ms=latency_ms,
        created_at=invoice.created_at,
    )
