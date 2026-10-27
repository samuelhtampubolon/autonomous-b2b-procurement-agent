"""SQLAlchemy models for multi-tenant autonomous procurement operations."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from src.database import Base


def utc_now() -> datetime:
    """Naive UTC timestamp factory.

    Timestamps are stored as naive UTC so SQLite and PostgreSQL round-trip identical
    values, which keeps the audit ledger's hash chain verifiable across backends.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    """User account model partitioned by tenant_id."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), index=True, nullable=False)
    username = Column(String(128), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(32), default="procurement_specialist", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    __table_args__ = (
        Index("ix_users_tenant_username", "tenant_id", "username"),
    )


class Vendor(Base):
    """Vendor master with contractual SLA penalty parameters."""

    __tablename__ = "vendors"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), index=True, nullable=False)
    vendor_code = Column(String(64), nullable=False)
    name = Column(String(255), nullable=False)
    daily_late_penalty_pct = Column(Float, default=0.005, nullable=False)  # 0.5% per day
    max_penalty_cap_pct = Column(Float, default=0.15, nullable=False)  # 15% max cap
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    # Relationships
    purchase_orders = relationship("PurchaseOrder", back_populates="vendor", cascade="all, delete-orphan")
    contract_clauses = relationship("ContractClause", back_populates="vendor", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_vendors_tenant_code", "tenant_id", "vendor_code"),
    )


class PurchaseOrder(Base):
    """Purchase Order (PO) representing the contracted commercial commitment."""

    __tablename__ = "purchase_orders"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), index=True, nullable=False)
    po_number = Column(String(64), unique=True, index=True, nullable=False)
    vendor_id = Column(Integer, ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False)
    ordered_qty = Column(Integer, nullable=False)
    agreed_unit_price = Column(Float, nullable=False)
    expected_delivery_date = Column(DateTime, nullable=False)
    item_description = Column(String(255), default="Standard Procurement Goods", nullable=False)
    status = Column(String(32), default="OPEN", nullable=False)  # OPEN, MATCHED, DISCREPANCY, CLOSED
    created_at = Column(DateTime, default=utc_now, nullable=False)

    # Relationships
    vendor = relationship("Vendor", back_populates="purchase_orders")
    invoices = relationship("Invoice", back_populates="purchase_order", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_pos_tenant_status", "tenant_id", "status"),
    )


class Invoice(Base):
    """Vendor billed invoice subject to 3-way matching and SLA audit."""

    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), index=True, nullable=False)
    invoice_number = Column(String(64), index=True, nullable=False)
    po_id = Column(Integer, ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False)
    billed_qty = Column(Integer, nullable=False)
    billed_unit_price = Column(Float, nullable=False)
    actual_delivery_date = Column(DateTime, nullable=False)
    subtotal_usd = Column(Float, default=0.0, nullable=False)
    variance_pct = Column(Float, default=0.0, nullable=False)
    sla_penalty_usd = Column(Float, default=0.0, nullable=False)
    final_payable_usd = Column(Float, default=0.0, nullable=False)
    routing_status = Column(String(32), default="HITL_PENDING", nullable=False)  # AUTO_APPROVED, HITL_PENDING, APPROVED, REJECTED
    approved_by = Column(String(128), nullable=True)
    rejection_reason = Column(String(255), nullable=True)
    vendor_notes = Column(Text, nullable=True)
    # Comma-separated threat rules raised by the prompt-injection firewall / fraud checks
    risk_flags = Column(String(255), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    # Relationships
    purchase_order = relationship("PurchaseOrder", back_populates="invoices")
    audit_logs = relationship("AgentAuditLog", back_populates="invoice", passive_deletes=True)

    __table_args__ = (
        Index("ix_invoices_tenant_routing", "tenant_id", "routing_status"),
        # Duplicate-invoice fraud control: one invoice number per tenant.
        Index("uq_invoices_tenant_number", "tenant_id", "invoice_number", unique=True),
    )


class AgentAuditLog(Base):
    """Append-only, hash-chained audit trail of agent actions and unit economics.

    Each entry stores `prev_hash` (the previous entry of the same tenant) and
    `entry_hash = SHA-256(prev_hash || canonical_json(entry))`, so editing or deleting
    any historic row breaks the chain and is detected by `/api/v1/audit/verify`.
    """

    __tablename__ = "agent_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), index=True, nullable=False)
    invoice_id = Column(Integer, ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True)
    action_taken = Column(String(64), nullable=False)  # RECONCILE, AUTO_APPROVE, ROUTE_HITL, HITL_APPROVE, HITL_REJECT
    details = Column(Text, nullable=False)
    tokens_used = Column(Integer, default=0, nullable=False)
    compute_cost_usd = Column(Float, default=0.0, nullable=False)
    labor_saved_usd = Column(Float, default=0.0, nullable=False)
    actor = Column(String(128), nullable=True)
    prev_hash = Column(String(64), nullable=True)
    entry_hash = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    # Relationships
    invoice = relationship("Invoice", back_populates="audit_logs")

    __table_args__ = (
        Index("ix_audit_tenant_action", "tenant_id", "action_taken"),
    )


class ContractClause(Base):
    """Local vector-searchable contract clauses for RAG matching."""

    __tablename__ = "contract_clauses"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), index=True, nullable=False)
    vendor_id = Column(Integer, ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False)
    clause_category = Column(String(64), nullable=False)  # PRICING_TIER, SLA_PENALTY, PAYMENT_TERMS, DISPUTE
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    # Relationships
    vendor = relationship("Vendor", back_populates="contract_clauses")

    __table_args__ = (
        Index("ix_contract_tenant_vendor", "tenant_id", "vendor_id"),
    )
