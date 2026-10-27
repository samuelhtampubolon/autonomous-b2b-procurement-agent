"""Deterministic synthetic data generator seeding 50 realistic B2B transactions.

Creates multi-tenant environments (tenant_alpha and tenant_beta), vendors,
purchase orders, contracts, and realistic invoice discrepancies for testing.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy.orm import Session

from src.database import SessionLocal, init_db
from src.models import (
    AgentAuditLog,
    ContractClause,
    Invoice,
    PurchaseOrder,
    User,
    Vendor,
)
from src.security import get_password_hash


def seed_database(db: Session, force: bool = False) -> int:
    """Populate database with 50 realistic enterprise B2B transactions."""
    init_db()

    existing_invoices = db.query(Invoice).count()
    if existing_invoices >= 50 and not force:
        return existing_invoices

    if force:
        db.query(AgentAuditLog).delete()
        db.query(Invoice).delete()
        db.query(PurchaseOrder).delete()
        db.query(ContractClause).delete()
        db.query(Vendor).delete()
        db.query(User).delete()
        db.commit()

    # 1. Seed Users for Auth and BOLA testing
    users = [
        User(
            tenant_id="tenant_alpha",
            username="analyst_alpha",
            hashed_password=get_password_hash("password123"),
            role="procurement_specialist",
        ),
        User(
            tenant_id="tenant_alpha",
            username="cpo_alpha",
            hashed_password=get_password_hash("cposecurepass"),
            role="cpo",
        ),
        User(
            tenant_id="tenant_beta",
            username="hacker_beta",
            hashed_password=get_password_hash("evilpass123"),
            role="procurement_specialist",
        ),
    ]
    db.add_all(users)
    db.flush()

    # 2. Seed Vendors
    vendors_data = [
        {
            "tenant_id": "tenant_alpha",
            "vendor_code": "VEND-MICRO-01",
            "name": "SiliconForge Semiconductor Ltd",
            "daily_late_penalty_pct": 0.005,  # 0.5% per day
            "max_penalty_cap_pct": 0.15,      # 15% cap
            "clauses": [
                ("PRICING_TIER", "Volume Discount Tier 1", "For order quantities exceeding 500 units, a tier rebate of 4.5% applies."),
                ("SLA_PENALTY", "Delivery Lead Time SLA", "Deliveries delayed beyond contracted date incur a 0.5% per calendar day penalty, capped at 15.0%."),
                ("PAYMENT_TERMS", "Prompt Payment Terms", "Net 30 days. 2% cash discount applicable if paid within 10 business days."),
            ],
        },
        {
            "tenant_id": "tenant_alpha",
            "vendor_code": "VEND-INDUS-02",
            "name": "Apex Industrial Robotics & CNC",
            "daily_late_penalty_pct": 0.008,  # 0.8% per day
            "max_penalty_cap_pct": 0.20,      # 20% cap
            "clauses": [
                ("PRICING_TIER", "Capital Equipment Warranty", "Machinery unit price includes 24 months on-site recalibration and spindle servicing."),
                ("SLA_PENALTY", "Factory Acceptance SLA", "Unscheduled assembly delays trigger liquidated damages of 0.8% per business day up to 20%."),
            ],
        },
        {
            "tenant_id": "tenant_alpha",
            "vendor_code": "VEND-LOGIS-03",
            "name": "Horizon Cross-Border Cold Chain",
            "daily_late_penalty_pct": 0.010,  # 1.0% per day
            "max_penalty_cap_pct": 0.25,      # 25% cap
            "clauses": [
                ("SLA_PENALTY", "Temperature Breach & Delay SLA", "Time-critical cargo incurs 1.0% daily demurrage penalty for transit delays exceeding 24 hours."),
            ],
        },
        {
            "tenant_id": "tenant_alpha",
            "vendor_code": "VEND-CHEM-04",
            "name": "SynthBio High-Purity Precursors",
            "daily_late_penalty_pct": 0.004,  # 0.4% per day
            "max_penalty_cap_pct": 0.12,      # 12% cap
            "clauses": [
                ("PRICING_TIER", "Chemical Batch Indexing", "Raw material surcharges strictly governed by ICIS spot index as of the PO issuance date."),
            ],
        },
        {
            "tenant_id": "tenant_alpha",
            "vendor_code": "VEND-CLOUD-05",
            "name": "DataCore Enterprise Hardware",
            "daily_late_penalty_pct": 0.006,  # 0.6% per day
            "max_penalty_cap_pct": 0.18,      # 18% cap
            "clauses": [
                ("PRICING_TIER", "Enterprise Server Rack Bundles", "Pre-configured dual-socket nodes billed at contracted master agreement schedule B."),
            ],
        },
        # Tenant Beta Vendor (for BOLA cross-tenant testing)
        {
            "tenant_id": "tenant_beta",
            "vendor_code": "VEND-BETA-01",
            "name": "Beta Exclusive Technologies",
            "daily_late_penalty_pct": 0.005,
            "max_penalty_cap_pct": 0.15,
            "clauses": [
                ("SLA_PENALTY", "Beta Confidential Agreement", "Penalty clauses strictly private to tenant beta operations."),
            ],
        },
    ]

    vendor_objs: List[Vendor] = []
    for v_info in vendors_data:
        v = Vendor(
            tenant_id=v_info["tenant_id"],
            vendor_code=v_info["vendor_code"],
            name=v_info["name"],
            daily_late_penalty_pct=v_info["daily_late_penalty_pct"],
            max_penalty_cap_pct=v_info["max_penalty_cap_pct"],
        )
        db.add(v)
        db.flush()
        vendor_objs.append(v)

        for cat, title, content in v_info["clauses"]:
            clause = ContractClause(
                tenant_id=v_info["tenant_id"],
                vendor_id=v.id,
                clause_category=cat,
                title=title,
                content=content,
            )
            db.add(clause)

    db.flush()

    alpha_vendors = [v for v in vendor_objs if v.tenant_id == "tenant_alpha"]
    beta_vendor = [v for v in vendor_objs if v.tenant_id == "tenant_beta"][0]

    # Item catalog for realistic PO generation
    items = [
        ("FPGA Accelerator PCIe Cards", 450.0),
        ("5-Axis High-Torque CNC Spindle", 4800.0),
        ("Cryogenic Reefer Container 40ft", 3200.0),
        ("Pharmaceutical Grade Solvent 200L", 850.0),
        ("2U 128-Core High-Density Node", 6500.0),
        ("Precision Ceramic Ball Bearings (Box)", 120.0),
        ("High-Bandwidth Memory HBM3 Modules", 920.0),
        ("Industrial Coolant Pump Assembly", 1450.0),
    ]

    now = datetime.now(timezone.utc)
    rng = random.Random(42)  # Deterministic seed for reproducible results

    # Generate 50 realistic invoices for tenant_alpha
    for i in range(1, 51):
        vendor = alpha_vendors[i % len(alpha_vendors)]
        item_desc, base_price = items[i % len(items)]
        qty = rng.choice([2, 5, 8, 10, 15, 20, 25])
        po_number = f"PO-2026-{1000 + i}"
        inv_number = f"INV-2026-{8000 + i}"

        expected_date = now - timedelta(days=rng.randint(5, 45))

        po = PurchaseOrder(
            tenant_id="tenant_alpha",
            po_number=po_number,
            vendor_id=vendor.id,
            ordered_qty=qty,
            agreed_unit_price=base_price,
            expected_delivery_date=expected_date,
            item_description=item_desc,
            status="OPEN",
        )
        db.add(po)
        db.flush()

        # Categorize invoice scenarios:
        # Case 1: Clean Match (<$10k, 0% variance, on time) -> AUTO_APPROVED
        # Case 2: SLA Delayed (on-time missed, late penalty calculated)
        # Case 3: Price Creep (>2% price increase) -> HITL_PENDING
        # Case 4: High Value (>$10,000 subtotal) -> HITL_PENDING
        # Case 5: Quantity Mismatch -> HITL_PENDING
        scenario = i % 5

        billed_qty = qty
        billed_price = base_price
        days_late = 0

        if scenario == 0:  # Clean Match
            billed_qty = qty
            billed_price = base_price
            actual_date = expected_date
        elif scenario == 1:  # Late Delivery with SLA Penalty
            days_late = rng.randint(3, 14)
            actual_date = expected_date + timedelta(days=days_late)
        elif scenario == 2:  # Price Creep (e.g. +3.5% to +8.0%)
            price_creep_pct = rng.uniform(0.03, 0.08)
            billed_price = round(base_price * (1.0 + price_creep_pct), 2)
            actual_date = expected_date
        elif scenario == 3:  # High Value Contract (force total > $10,000)
            billed_qty = max(qty, int(12000.0 / base_price) + 1)
            po.ordered_qty = billed_qty
            actual_date = expected_date
        else:  # Quantity Mismatch
            billed_qty = qty + rng.choice([1, -1, 2])
            actual_date = expected_date

        subtotal = round(billed_qty * billed_price, 2)
        variance_pct = round(((billed_price - base_price) / base_price) * 100.0, 4)

        # SLA penalty math
        penalty_pct = min(vendor.max_penalty_cap_pct, days_late * vendor.daily_late_penalty_pct)
        sla_penalty_usd = round(subtotal * penalty_pct, 2)
        final_payable_usd = round(max(0.0, subtotal - sla_penalty_usd), 2)

        # Routing status
        if abs(variance_pct) > 2.0:
            routing_status = "HITL_PENDING"
            routing_reason = f"Price variance {variance_pct:+.2f}% exceeds threshold"
        elif subtotal > 10000.0:
            routing_status = "HITL_PENDING"
            routing_reason = f"High-value order (${subtotal:,.2f}) requires HITL sign-off"
        elif billed_qty != po.ordered_qty:
            routing_status = "HITL_PENDING"
            routing_reason = f"Quantity discrepancy (Billed {billed_qty} vs Ordered {po.ordered_qty})"
        else:
            routing_status = "AUTO_APPROVED"
            routing_reason = "Deterministic 3-way match passed"

        # Pre-resolve some older HITL invoices to reflect realistic operations
        approved_by = None
        rejection_reason = None
        if routing_status == "AUTO_APPROVED":
            approved_by = "AI_AGENT_ENGINE"
            po.status = "MATCHED"
        elif i in (3, 7, 13):
            routing_status = "APPROVED"
            approved_by = "analyst_alpha"
            po.status = "MATCHED"
        elif i in (8, 18):
            routing_status = "REJECTED"
            rejection_reason = "Vendor refused to credit uncontracted surcharge"
            po.status = "DISCREPANCY"

        invoice = Invoice(
            tenant_id="tenant_alpha",
            invoice_number=inv_number,
            po_id=po.id,
            billed_qty=billed_qty,
            billed_unit_price=billed_price,
            actual_delivery_date=actual_date,
            subtotal_usd=subtotal,
            variance_pct=variance_pct,
            sla_penalty_usd=sla_penalty_usd,
            final_payable_usd=final_payable_usd,
            routing_status=routing_status,
            approved_by=approved_by,
            rejection_reason=rejection_reason,
            vendor_notes=f"Standard commercial delivery for {item_desc}.",
            created_at=now - timedelta(days=rng.randint(1, 30)),
        )
        db.add(invoice)
        db.flush()

        audit = AgentAuditLog(
            tenant_id="tenant_alpha",
            invoice_id=invoice.id,
            action_taken=f"RECONCILE_{routing_status}",
            details=f"{routing_reason} | SLA Penalty: ${sla_penalty_usd:,.2f}",
            tokens_used=rng.randint(320, 580),
            compute_cost_usd=0.0008,
            labor_saved_usd=13.50,
            created_at=invoice.created_at,
        )
        db.add(audit)

    # Generate 5 isolated invoices for tenant_beta (for BOLA testing)
    for b_idx in range(1, 6):
        po_b = PurchaseOrder(
            tenant_id="tenant_beta",
            po_number=f"PO-BETA-{2000 + b_idx}",
            vendor_id=beta_vendor.id,
            ordered_qty=10,
            agreed_unit_price=150.0,
            expected_delivery_date=now - timedelta(days=10),
            item_description="Confidential Beta Test Module",
            status="OPEN",
        )
        db.add(po_b)
        db.flush()

        inv_b = Invoice(
            tenant_id="tenant_beta",
            invoice_number=f"INV-BETA-{9000 + b_idx}",
            po_id=po_b.id,
            billed_qty=10,
            billed_unit_price=150.0,
            actual_delivery_date=now - timedelta(days=10),
            subtotal_usd=1500.0,
            variance_pct=0.0,
            sla_penalty_usd=0.0,
            final_payable_usd=1500.0,
            routing_status="AUTO_APPROVED",
            approved_by="AI_AGENT_ENGINE",
            vendor_notes="Confidential beta invoice",
        )
        db.add(inv_b)
        db.flush()

        audit_b = AgentAuditLog(
            tenant_id="tenant_beta",
            invoice_id=inv_b.id,
            action_taken="RECONCILE_AUTO_APPROVED",
            details="Beta automated reconciliation passed",
            tokens_used=350,
            compute_cost_usd=0.0007,
            labor_saved_usd=13.50,
        )
        db.add(audit_b)

    db.commit()
    total_count = db.query(Invoice).count()
    return total_count


if __name__ == "__main__":
    db = SessionLocal()
    try:
        count = seed_database(db, force=True)
        print(f"Successfully seeded {count} B2B invoices across tenant_alpha and tenant_beta.")
    finally:
        db.close()
