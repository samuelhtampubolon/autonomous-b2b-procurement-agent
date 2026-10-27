"""Automated test suite for 3-way matching engine, SLA penalties, and ROI unit economics."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.database import SessionLocal, init_db
from src.models import PurchaseOrder, Vendor
from src.security import create_access_token
from src.seed_data import seed_database

client = TestClient(app)


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    init_db()
    db = SessionLocal()
    try:
        seed_database(db, force=False)
    finally:
        db.close()


@pytest.fixture
def alpha_auth_headers():
    token = create_access_token({
        "sub": "analyst_alpha",
        "tenant_id": "tenant_alpha",
        "role": "procurement_specialist",
    })
    return {"Authorization": f"Bearer {token}"}


def test_clean_three_way_match_auto_approved(alpha_auth_headers):
    """Test scenario: Clean match (0% variance, <= $10k, on-time) -> AUTO_APPROVED."""
    uid = uuid.uuid4().hex[:6]
    po_num = f"PO-TEST-CLEAN-{uid}"
    inv_num = f"INV-TEST-CLEAN-{uid}"

    db = SessionLocal()
    try:
        vendor = db.query(Vendor).filter(Vendor.tenant_id == "tenant_alpha").first()
        po = PurchaseOrder(
            tenant_id="tenant_alpha",
            po_number=po_num,
            vendor_id=vendor.id,
            ordered_qty=10,
            agreed_unit_price=250.0,
            expected_delivery_date=datetime.now(timezone.utc),
            item_description="Clean Matching Item",
            status="OPEN",
        )
        db.add(po)
        db.commit()
    finally:
        db.close()

    payload = {
        "po_number": po_num,
        "invoice_number": inv_num,
        "billed_qty": 10,
        "billed_unit_price": 250.0,
        "actual_delivery_date": datetime.now(timezone.utc).isoformat(),
        "vendor_notes": "Prompt clean delivery",
    }

    res = client.post("/api/v1/invoices/reconcile", json=payload, headers=alpha_auth_headers)
    assert res.status_code == 201
    data = res.json()
    assert data["routing_status"] == "AUTO_APPROVED"
    assert data["subtotal_usd"] == 2500.0
    assert data["variance_pct"] == 0.0
    assert data["sla_penalty_usd"] == 0.0
    assert data["final_payable_usd"] == 2500.0


def test_price_creep_triggers_hitl_pending(alpha_auth_headers):
    """Test scenario: Price variance +4.0% (>2.0% threshold) routes to HITL_PENDING."""
    uid = uuid.uuid4().hex[:6]
    po_num = f"PO-TEST-CREEP-{uid}"
    inv_num = f"INV-TEST-CREEP-{uid}"

    db = SessionLocal()
    try:
        vendor = db.query(Vendor).filter(Vendor.tenant_id == "tenant_alpha").first()
        po = PurchaseOrder(
            tenant_id="tenant_alpha",
            po_number=po_num,
            vendor_id=vendor.id,
            ordered_qty=10,
            agreed_unit_price=200.0,
            expected_delivery_date=datetime.now(timezone.utc),
            item_description="Price Creep Item",
            status="OPEN",
        )
        db.add(po)
        db.commit()
    finally:
        db.close()

    payload = {
        "po_number": po_num,
        "invoice_number": inv_num,
        "billed_qty": 10,
        "billed_unit_price": 208.0,  # +4% price creep
        "actual_delivery_date": datetime.now(timezone.utc).isoformat(),
        "vendor_notes": "Added inflation adjustment",
    }

    res = client.post("/api/v1/invoices/reconcile", json=payload, headers=alpha_auth_headers)
    assert res.status_code == 201
    data = res.json()
    assert data["routing_status"] == "HITL_PENDING"
    assert data["variance_pct"] == 4.0
    assert "exceeds threshold" in data["routing_reason"].lower()


def test_high_value_order_triggers_hitl_pending(alpha_auth_headers):
    """Test scenario: Subtotal > $10,000 threshold routes to HITL_PENDING."""
    uid = uuid.uuid4().hex[:6]
    po_num = f"PO-TEST-HIGHVAL-{uid}"
    inv_num = f"INV-TEST-HIGHVAL-{uid}"

    db = SessionLocal()
    try:
        vendor = db.query(Vendor).filter(Vendor.tenant_id == "tenant_alpha").first()
        po = PurchaseOrder(
            tenant_id="tenant_alpha",
            po_number=po_num,
            vendor_id=vendor.id,
            ordered_qty=5,
            agreed_unit_price=3000.0,  # Total $15,000
            expected_delivery_date=datetime.now(timezone.utc),
            item_description="High-Value Robotics Assembly",
            status="OPEN",
        )
        db.add(po)
        db.commit()
    finally:
        db.close()

    payload = {
        "po_number": po_num,
        "invoice_number": inv_num,
        "billed_qty": 5,
        "billed_unit_price": 3000.0,
        "actual_delivery_date": datetime.now(timezone.utc).isoformat(),
        "vendor_notes": "High-value shipment",
    }

    res = client.post("/api/v1/invoices/reconcile", json=payload, headers=alpha_auth_headers)
    assert res.status_code == 201
    data = res.json()
    assert data["routing_status"] == "HITL_PENDING"
    assert data["subtotal_usd"] == 15000.0
    assert "high-value" in data["routing_reason"].lower()


def test_sla_late_delivery_penalty_calculation(alpha_auth_headers):
    """Test scenario: 10 days late delivery incurs SLA penalty deduction."""
    uid = uuid.uuid4().hex[:6]
    po_num = f"PO-TEST-SLA-{uid}"
    inv_num = f"INV-TEST-SLA-{uid}"

    db = SessionLocal()
    try:
        vendor = db.query(Vendor).filter(Vendor.tenant_id == "tenant_alpha", Vendor.vendor_code == "VEND-MICRO-01").first()
        expected = datetime.now(timezone.utc) - timedelta(days=10)
        po = PurchaseOrder(
            tenant_id="tenant_alpha",
            po_number=po_num,
            vendor_id=vendor.id,
            ordered_qty=10,
            agreed_unit_price=100.0,  # $1,000 subtotal
            expected_delivery_date=expected,
            item_description="SLA Delayed Item",
            status="OPEN",
        )
        db.add(po)
        db.commit()
    finally:
        db.close()

    # Delivered 10 days late -> 10 days * 0.5% daily = 5.0% penalty = $50.00
    actual = expected + timedelta(days=10)
    payload = {
        "po_number": po_num,
        "invoice_number": inv_num,
        "billed_qty": 10,
        "billed_unit_price": 100.0,
        "actual_delivery_date": actual.isoformat(),
        "vendor_notes": "Logistics backlog delay",
    }

    res = client.post("/api/v1/invoices/reconcile", json=payload, headers=alpha_auth_headers)
    assert res.status_code == 201
    data = res.json()
    assert data["subtotal_usd"] == 1000.0
    assert data["sla_penalty_usd"] == 50.0
    assert data["final_payable_usd"] == 950.0
    assert data["matching_breakdown"]["days_late"] == 10


def test_contract_rag_similarity_search(alpha_auth_headers):
    """Test scenario: Local vector RAG retrieves relevant clauses."""
    res = client.post(
        "/api/v1/contracts/search",
        json={"query": "delay late penalty lead time", "top_k": 3},
        headers=alpha_auth_headers,
    )
    assert res.status_code == 200
    clauses = res.json()
    assert len(clauses) > 0
    top_clause = clauses[0]
    assert top_clause["similarity_score"] > 0.0
    assert "penalty" in top_clause["title"].lower() or "sla" in top_clause["title"].lower()


def test_roi_metrics_governing_equations(alpha_auth_headers):
    """Test scenario: ROI ledger returns positive OpEx savings and ROI multiple."""
    res = client.get("/api/v1/metrics/roi", headers=alpha_auth_headers)
    assert res.status_code == 200
    roi = res.json()
    assert roi["total_invoices_processed"] >= 50
    assert roi["net_opex_savings_usd"] > 0
    assert roi["total_sla_penalties_recovered_usd"] > 0
    assert roi["roi_multiple"] > 1.0
    assert roi["straight_through_processing_pct"] > 0
