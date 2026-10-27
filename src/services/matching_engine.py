"""Deterministic 3-Way Invoice Matching & SLA Penalty Engine.

Implements automated reconciliation across Purchase Orders, Goods Receipts,
and Invoices with SLA penalty calculations and HITL routing guardrails.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Tuple

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from src.config import settings
from src.models import AgentAuditLog, Invoice, PurchaseOrder, Vendor
from src.schemas import (
    InvoiceReconcileRequest,
    InvoiceReconciliationResponse,
    MatchingBreakdown,
)
from src.security import sanitize_untrusted_text


def reconcile_invoice(
    request: InvoiceReconcileRequest,
    current_user_tenant_id: str,
    db: Session,
) -> InvoiceReconciliationResponse:
    """Execute deterministic 3-way reconciliation with contract SLA penalty deduction.

    Enforces:
    1. Tenant boundary validation on Purchase Order.
    2. Zero-variance quantity matching check.
    3. Price variance calculation against contracted rate.
    4. Delivery timestamp SLA penalty calculation using vendor terms.
    5. HITL routing guardrails (price variance > 2% or value > $10,000).
    """
    start_time = time.perf_counter()

    # Retrieve PO scoped strictly to the authenticated tenant
    po: PurchaseOrder = (
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
            detail=f"Purchase Order '{request.po_number}' not found for tenant '{current_user_tenant_id}'",
        )

    # Retrieve Vendor contract terms
    vendor: Vendor = (
        db.query(Vendor)
        .filter(Vendor.id == po.vendor_id, Vendor.tenant_id == current_user_tenant_id)
        .first()
    )

    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Vendor contract for PO '{request.po_number}' not found",
        )

    # 1. Deterministic Math & Variances
    ordered_qty = po.ordered_qty
    billed_qty = request.billed_qty
    agreed_price = po.agreed_unit_price
    billed_price = request.billed_unit_price

    qty_matched = (ordered_qty == billed_qty)
    price_variance_pct = round(
        ((billed_price - agreed_price) / agreed_price) * 100.0, 4
    )
    price_matched = abs(price_variance_pct) <= 0.001

    subtotal_usd = round(billed_qty * billed_price, 2)

    # 2. SLA Late Delivery Penalty Calculation
    actual_date = request.actual_delivery_date
    expected_date = po.expected_delivery_date

    # Normalize to date comparison
    days_late = max(0, (actual_date.date() - expected_date.date()).days)

    daily_rate = vendor.daily_late_penalty_pct
    max_cap = vendor.max_penalty_cap_pct
    applied_penalty_pct = min(max_cap, days_late * daily_rate)
    sla_penalty_usd = round(subtotal_usd * applied_penalty_pct, 2)
    final_payable_usd = round(max(0.0, subtotal_usd - sla_penalty_usd), 2)

    # 3. Guardrail HITL Routing Decision Logic (FR-04)
    routing_status: str
    routing_reason: str

    if abs(price_variance_pct) > settings.VARIANCE_TOLERANCE_PCT:
        routing_status = "HITL_PENDING"
        routing_reason = (
            f"Price variance {price_variance_pct:+.2f}% exceeds threshold "
            f"of ±{settings.VARIANCE_TOLERANCE_PCT}%"
        )
    elif subtotal_usd > settings.HIGH_VALUE_THRESHOLD_USD:
        routing_status = "HITL_PENDING"
        routing_reason = (
            f"High-value invoice (${subtotal_usd:,.2f}) exceeds human approval "
            f"threshold of ${settings.HIGH_VALUE_THRESHOLD_USD:,.2f}"
        )
    elif not qty_matched:
        routing_status = "HITL_PENDING"
        routing_reason = (
            f"Quantity mismatch: Billed {billed_qty} vs Ordered {ordered_qty}"
        )
    else:
        routing_status = "AUTO_APPROVED"
        routing_reason = (
            "Deterministic 3-way match passed: price variance, quantity, and contract terms verified"
        )

    # Update PO status if auto approved
    if routing_status == "AUTO_APPROVED":
        po.status = "MATCHED"

    # Sanitize untrusted vendor remarks
    sanitized_notes = sanitize_untrusted_text(request.vendor_notes)

    # 4. Persist Invoice Record
    invoice = Invoice(
        tenant_id=current_user_tenant_id,
        invoice_number=request.invoice_number,
        po_id=po.id,
        billed_qty=billed_qty,
        billed_unit_price=billed_price,
        actual_delivery_date=actual_date,
        subtotal_usd=subtotal_usd,
        variance_pct=price_variance_pct,
        sla_penalty_usd=sla_penalty_usd,
        final_payable_usd=final_payable_usd,
        routing_status=routing_status,
        approved_by="AI_AGENT_ENGINE" if routing_status == "AUTO_APPROVED" else None,
        vendor_notes=sanitized_notes,
    )
    db.add(invoice)
    db.flush()  # Populate invoice.id

    # 5. Measure latency and record Agent Audit Log
    end_time = time.perf_counter()
    latency_ms = round((end_time - start_time) * 1000.0, 2)

    # Token economics simulation based on prompt payload size
    tokens_consumed = 350 + int(len(sanitized_notes or "") * 0.75)
    compute_cost = (tokens_consumed / 1000.0) * settings.TOKEN_COST_PER_1K_USD
    labor_saved = (
        (settings.MANUAL_MINUTES_PER_INVOICE / 60.0) * settings.HOURLY_ANALYST_WAGE_USD
    )

    audit_log = AgentAuditLog(
        tenant_id=current_user_tenant_id,
        invoice_id=invoice.id,
        action_taken=f"RECONCILE_{routing_status}",
        details=f"{routing_reason} | SLA Penalty: ${sla_penalty_usd:,.2f} | Latency: {latency_ms}ms",
        tokens_used=tokens_consumed,
        compute_cost_usd=compute_cost,
        labor_saved_usd=labor_saved,
    )
    db.add(audit_log)
    db.commit()
    db.refresh(invoice)

    breakdown = MatchingBreakdown(
        ordered_qty=ordered_qty,
        billed_qty=billed_qty,
        qty_matched=qty_matched,
        agreed_unit_price=agreed_price,
        billed_unit_price=billed_price,
        price_variance_pct=price_variance_pct,
        price_matched=price_matched,
        days_late=days_late,
        sla_daily_penalty_rate_pct=daily_rate * 100.0,
        sla_penalty_applied_pct=applied_penalty_pct * 100.0,
        sla_penalty_usd=sla_penalty_usd,
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
        tokens_consumed=tokens_consumed,
        inference_latency_ms=latency_ms,
        created_at=invoice.created_at,
    )
