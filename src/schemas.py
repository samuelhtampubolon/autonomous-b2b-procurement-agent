"""Pydantic v2 schemas enforcing strict validation and OWASP API3 protection.

Every request model is configured with `extra='forbid'` and `str_strip_whitespace=True`
to block mass-assignment and unexpected payload pollution.
"""
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Base Schema Configuration
# ---------------------------------------------------------------------------

class StrictBaseModel(BaseModel):
    """Base model forbidding undeclared fields to block mass-assignment attacks."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


# ---------------------------------------------------------------------------
# Authentication Schemas
# ---------------------------------------------------------------------------

class TokenRequest(StrictBaseModel):
    """Credentials request schema for issuing JWT access tokens."""

    username: str = Field(..., min_length=3, max_length=128, description="Username")
    password: str = Field(..., min_length=6, max_length=128, description="Account password")


class TokenResponse(BaseModel):
    """JWT Bearer token return payload."""

    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    tenant_id: str
    role: str


class UserResponse(BaseModel):
    """Public user identity schema."""

    id: int
    username: str
    tenant_id: str
    role: str
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Invoice Reconciliation Schemas
# ---------------------------------------------------------------------------

class InvoiceReconcileRequest(StrictBaseModel):
    """Payload for submitting a vendor invoice for 3-way matching and SLA audit."""

    po_number: str = Field(..., min_length=3, max_length=64, description="Contracted Purchase Order Number")
    invoice_number: str = Field(..., min_length=3, max_length=64, description="Vendor invoice identifier")
    billed_qty: int = Field(..., gt=0, description="Quantity billed by vendor")
    billed_unit_price: float = Field(..., gt=0.0, description="Unit price billed by vendor")
    actual_delivery_date: datetime = Field(..., description="Timestamp of physical goods delivery")
    vendor_notes: Optional[str] = Field(None, max_length=2000, description="Optional vendor invoice remarks")


class MatchingBreakdown(BaseModel):
    """Detailed deterministic mathematical reconciliation audit."""

    ordered_qty: int
    billed_qty: int
    qty_matched: bool
    agreed_unit_price: float
    billed_unit_price: float
    price_variance_pct: float
    price_matched: bool
    days_late: int
    sla_daily_penalty_rate_pct: float
    sla_penalty_applied_pct: float
    sla_penalty_usd: float


class InvoiceReconciliationResponse(BaseModel):
    """Comprehensive reconciliation output and HITL routing decision."""

    invoice_id: int
    po_number: str
    invoice_number: str
    subtotal_usd: float
    variance_pct: float
    sla_penalty_usd: float
    final_payable_usd: float
    routing_status: Literal["AUTO_APPROVED", "HITL_PENDING", "APPROVED", "REJECTED"]
    routing_reason: str
    matching_breakdown: MatchingBreakdown
    tokens_consumed: int
    inference_latency_ms: float
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class HITLDecisionRequest(StrictBaseModel):
    """Payload for Human-in-the-Loop approval or rejection."""

    decision: Literal["APPROVE", "REJECT"] = Field(..., description="Human decision: APPROVE or REJECT")
    reviewer_notes: Optional[str] = Field(None, max_length=1000, description="Audit justification notes")


class HITLDecisionResponse(BaseModel):
    """Confirmation of Human-in-the-Loop decision."""

    invoice_id: int
    routing_status: Literal["APPROVED", "REJECTED"]
    decided_by: str
    reviewer_notes: Optional[str]
    timestamp: datetime
    message: str


class InvoiceSummaryItem(BaseModel):
    """Summary item for paginated invoice listings."""

    id: int
    tenant_id: str
    invoice_number: str
    po_number: str
    vendor_name: str
    subtotal_usd: float
    variance_pct: float
    sla_penalty_usd: float
    final_payable_usd: float
    routing_status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InvoiceDetailResponse(BaseModel):
    """Complete invoice view including vendor details and audit history."""

    id: int
    tenant_id: str
    invoice_number: str
    po_number: str
    vendor_code: str
    vendor_name: str
    ordered_qty: int
    billed_qty: int
    agreed_unit_price: float
    billed_unit_price: float
    expected_delivery_date: datetime
    actual_delivery_date: datetime
    subtotal_usd: float
    variance_pct: float
    sla_penalty_usd: float
    final_payable_usd: float
    routing_status: str
    approved_by: Optional[str]
    rejection_reason: Optional[str]
    vendor_notes: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Contract RAG Schemas
# ---------------------------------------------------------------------------

class ContractClauseResponse(BaseModel):
    """Contract clause with similarity scoring."""

    id: int
    vendor_id: int
    vendor_name: str
    clause_category: str
    title: str
    content: str
    similarity_score: float

    model_config = ConfigDict(from_attributes=True)


class ContractSearchRequest(StrictBaseModel):
    """Query schema for supplier contract RAG similarity search."""

    query: str = Field(..., min_length=2, max_length=500, description="Natural language contract clause query")
    vendor_id: Optional[int] = Field(None, description="Optional vendor filter")
    top_k: int = Field(3, ge=1, le=10, description="Max clauses to return")


# ---------------------------------------------------------------------------
# Unit Economics & ROI Schemas
# ---------------------------------------------------------------------------

class ROIMetricsResponse(BaseModel):
    """Autonomous procurement agent unit economics and operational metrics."""

    total_invoices_processed: int
    auto_approved_count: int
    hitl_pending_count: int
    hitl_resolved_count: int
    straight_through_processing_pct: float
    gross_labor_hours_saved: float
    gross_labor_cost_saved_usd: float
    total_tokens_consumed: int
    ai_compute_cost_usd: float
    fixed_infra_cost_usd: float
    total_ai_opex_cost_usd: float
    net_opex_savings_usd: float
    total_sla_penalties_recovered_usd: float
    total_commercial_benefit_usd: float
    roi_multiple: float
    avg_reconciliation_latency_ms: float


# ---------------------------------------------------------------------------
# Health & Status Schemas
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    """Liveness probe status schema."""

    status: str = "ok"
    app: str
    version: str
    environment: str
    timestamp: datetime


class ReadyResponse(BaseModel):
    """Readiness probe status schema."""

    status: str = "ready"
    database_connected: bool
    seeded_invoices_count: int
    timestamp: datetime
