"""Streamlit Executive Control Tower & HITL Review Dashboard.

Provides real-time visibility into BPMN reconciliation states,
Human-in-the-Loop exception approvals, and Unit Economics ROI ledgers.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
import requests
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px

# Streamlit Page Setup
st.set_page_config(
    page_title="Procurement AI Control Tower",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom High-End Enterprise CSS
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    
    .stApp {
        background: radial-gradient(circle at 10% 20%, rgba(15, 23, 42, 0.98), rgba(2, 6, 23, 1));
        color: #F8FAFC;
    }
    
    .metric-card {
        background: rgba(30, 41, 59, 0.6);
        border: 1px solid rgba(148, 163, 184, 0.15);
        border-radius: 12px;
        padding: 20px;
        backdrop-filter: blur(12px);
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
        transition: transform 0.2s ease, border-color 0.2s ease;
    }
    .metric-card:hover {
        transform: translateY(-2px);
        border-color: rgba(56, 189, 248, 0.4);
    }
    .metric-title {
        font-size: 0.85rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: #94A3B8;
        font-weight: 600;
        margin-bottom: 6px;
    }
    .metric-value {
        font-size: 1.85rem;
        font-weight: 700;
        color: #F8FAFC;
    }
    .metric-sub {
        font-size: 0.8rem;
        color: #38BDF8;
        margin-top: 4px;
    }
    
    .badge-auto {
        background-color: rgba(16, 185, 129, 0.18);
        color: #34D399;
        border: 1px solid rgba(52, 211, 153, 0.3);
        padding: 4px 10px;
        border-radius: 9999px;
        font-weight: 600;
        font-size: 0.75rem;
    }
    .badge-hitl {
        background-color: rgba(245, 158, 11, 0.18);
        color: #FBBF24;
        border: 1px solid rgba(251, 191, 36, 0.3);
        padding: 4px 10px;
        border-radius: 9999px;
        font-weight: 600;
        font-size: 0.75rem;
    }
    .badge-rejected {
        background-color: rgba(239, 68, 68, 0.18);
        color: #F87171;
        border: 1px solid rgba(248, 113, 113, 0.3);
        padding: 4px 10px;
        border-radius: 9999px;
        font-weight: 600;
        font-size: 0.75rem;
    }
</style>
""", unsafe_allow_html=True)

# API Base URL
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")

# ---------------------------------------------------------------------------
# Sidebar Authentication & Tenant Switching
# ---------------------------------------------------------------------------

st.sidebar.image("https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?auto=format&fit=crop&w=600&q=80", use_container_width=True)
st.sidebar.title("Procurement AI")
st.sidebar.caption("Autonomous B2B Supply Chain Agent")

# Preset Accounts for easy testing
PRESETS = {
    "Analyst (Tenant Alpha)": ("analyst_alpha", "password123"),
    "CPO (Tenant Alpha)": ("cpo_alpha", "cposecurepass"),
    "Attacker (Tenant Beta)": ("hacker_beta", "evilpass123"),
}

user_choice = st.sidebar.selectbox("Active Persona / Tenant", list(PRESETS.keys()))
username, password = PRESETS[user_choice]

@st.cache_data(ttl=600, show_spinner=False)
def get_auth_token(user: str, pwd: str):
    try:
        res = requests.post(
            f"{API_URL}/api/v1/auth/token",
            json={"username": user, "password": pwd},
            timeout=5,
        )
        if res.status_code == 200:
            return res.json()
        return None
    except Exception:
        return None

token_data = get_auth_token(username, password)

if token_data:
    st.sidebar.success(f"Tenant: `{token_data['tenant_id']}`\nRole: `{token_data['role']}`")
    headers = {"Authorization": f"Bearer {token_data['access_token']}"}
else:
    st.sidebar.error("Could not authenticate with local API server. Ensure API is running at 127.0.0.1:8000.")
    headers = {}

navigation = st.sidebar.radio(
    "Navigation",
    ["Executive ROI Control Tower", "Interactive 3-Way Match", "HITL Review Queue", "Contract RAG Explorer", "Security & BOLA Defense"],
)

# ---------------------------------------------------------------------------
# Helper API Fetchers
# ---------------------------------------------------------------------------

def fetch_roi_metrics():
    try:
        r = requests.get(f"{API_URL}/api/v1/metrics/roi", headers=headers, timeout=5)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None

def fetch_invoices(status_filter=None):
    try:
        params = {"limit": 100}
        if status_filter:
            params["routing_status"] = status_filter
        r = requests.get(f"{API_URL}/api/v1/invoices", headers=headers, params=params, timeout=5)
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []

def fetch_pos():
    try:
        r = requests.get(f"{API_URL}/api/v1/purchase-orders", headers=headers, timeout=5)
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# View 1: Executive ROI Control Tower
# ---------------------------------------------------------------------------

if navigation == "Executive ROI Control Tower":
    st.title("🛡️ Executive Procurement Control Tower")
    st.markdown("Real-time agentic automation telemetry, BPMN state metrics, and autonomous ROI unit economics.")

    metrics = fetch_roi_metrics()
    if not metrics:
        st.warning("Connecting to Procurement API... If the server was just launched, please wait a moment.")
    else:
        # KPI Row
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-title">Net OpEx Savings</div>
                <div class="metric-value">${metrics['net_opex_savings_usd']:,.2f}</div>
                <div class="metric-sub">↑ {metrics['gross_labor_hours_saved']} Analyst Hours Saved</div>
            </div>
            """, unsafe_allow_html=True)

        with c2:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-title">Straight-Through Rate</div>
                <div class="metric-value">{metrics['straight_through_processing_pct']}%</div>
                <div class="metric-sub">{metrics['auto_approved_count']} of {metrics['total_invoices_processed']} Invoices STP</div>
            </div>
            """, unsafe_allow_html=True)

        with c3:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-title">SLA Penalties Recovered</div>
                <div class="metric-value">${metrics['total_sla_penalties_recovered_usd']:,.2f}</div>
                <div class="metric-sub">Automated contract enforcement</div>
            </div>
            """, unsafe_allow_html=True)

        with c4:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-title">AI Automation ROI</div>
                <div class="metric-value">{metrics['roi_multiple']}x</div>
                <div class="metric-sub">Total Benefit: ${metrics['total_commercial_benefit_usd']:,.2f}</div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # Charts Row
        col_chart1, col_chart2 = st.columns(2)

        with col_chart1:
            st.subheader("BPMN Ingestion & Routing Breakdown")
            donut_fig = go.Figure(data=[go.Pie(
                labels=["Auto Approved (STP)", "HITL Pending", "HITL Resolved"],
                values=[metrics["auto_approved_count"], metrics["hitl_pending_count"], metrics["hitl_resolved_count"]],
                hole=0.6,
                marker=dict(colors=["#10B981", "#F59E0B", "#38BDF8"]),
            )])
            donut_fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#94A3B8"),
                showlegend=True,
                margin=dict(t=20, b=20, l=20, r=20),
            )
            st.plotly_chart(donut_fig, use_container_width=True)

        with col_chart2:
            st.subheader("Unit Economics: Value Generated vs AI Cost")
            bar_fig = go.Figure(data=[
                go.Bar(
                    name="Financial Impact",
                    x=["Gross Labor Saved", "SLA Penalties Recovered", "AI Compute & Infra", "Net Commercial Gain"],
                    y=[
                        metrics["gross_labor_cost_saved_usd"],
                        metrics["total_sla_penalties_recovered_usd"],
                        metrics["total_ai_opex_cost_usd"],
                        metrics["total_commercial_benefit_usd"],
                    ],
                    marker_color=["#38BDF8", "#F59E0B", "#EF4444", "#10B981"],
                )
            ])
            bar_fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#94A3B8"),
                yaxis=dict(gridcolor="rgba(148, 163, 184, 0.1)"),
                margin=dict(t=20, b=20, l=20, r=20),
            )
            st.plotly_chart(bar_fig, use_container_width=True)


# ---------------------------------------------------------------------------
# View 2: Interactive 3-Way Reconciliation
# ---------------------------------------------------------------------------

elif navigation == "Interactive 3-Way Match":
    st.title("⚡ Interactive 3-Way Invoice Matching")
    st.markdown("Submit live invoices against purchase orders with deterministic variance verification and contract SLA penalty audits.")

    pos = fetch_pos()
    if not pos:
        st.info("Loading purchase orders for active tenant...")
    else:
        po_options = {f"{p['po_number']} - {p['vendor_name']} ({p['item_description']})": p for p in pos}
        selected_po_label = st.selectbox("Select Target Purchase Order", list(po_options.keys()))
        selected_po = po_options[selected_po_label]

        st.markdown(f"**Contract Commitment**: {selected_po['ordered_qty']} units @ ${selected_po['agreed_unit_price']:,.2f} each | Expected Delivery: `{selected_po['expected_delivery_date'][:10]}`")

        col1, col2, col3 = st.columns(3)
        with col1:
            inv_number = st.text_input("Vendor Invoice #", f"INV-LIVE-{datetime.now().strftime('%M%S')}")
            billed_qty = st.number_input("Billed Quantity", min_value=1, value=int(selected_po["ordered_qty"]))
        with col2:
            price_mult = st.selectbox("Simulate Price Scenario", ["Exact Contract Price (0% variance)", "Minor Price Creep (+3.5%)", "Severe Price Creep (+8.0%)", "Discounted Price (-2.0%)"])
            if "Exact" in price_mult:
                sim_price = selected_po["agreed_unit_price"]
            elif "+3.5%" in price_mult:
                sim_price = round(selected_po["agreed_unit_price"] * 1.035, 2)
            elif "+8.0%" in price_mult:
                sim_price = round(selected_po["agreed_unit_price"] * 1.080, 2)
            else:
                sim_price = round(selected_po["agreed_unit_price"] * 0.98, 2)
            billed_price = st.number_input("Billed Unit Price ($)", min_value=0.01, value=float(sim_price))

        with col3:
            days_delay = st.slider("Delivery Delay (Days late)", min_value=0, max_value=25, value=0)
            vendor_notes = st.text_area("Vendor Invoice Remarks", "Delivery fulfilled in accordance with master procurement terms.")

        if st.button("🚀 Run Deterministic 3-Way Reconciliation", type="primary"):
            exp_date = datetime.fromisoformat(selected_po["expected_delivery_date"].replace("Z", "+00:00"))
            from datetime import timedelta
            actual_delivery_date = exp_date + timedelta(days=days_delay)

            payload = {
                "po_number": selected_po["po_number"],
                "invoice_number": inv_number,
                "billed_qty": int(billed_qty),
                "billed_unit_price": float(billed_price),
                "actual_delivery_date": actual_delivery_date.isoformat(),
                "vendor_notes": vendor_notes,
            }

            try:
                res = requests.post(f"{API_URL}/api/v1/invoices/reconcile", json=payload, headers=headers, timeout=5)
                if res.status_code == 201:
                    data = res.json()
                    st.success(f"Reconciliation Complete in {data['inference_latency_ms']} ms! Tokens Consumed: {data['tokens_consumed']}")

                    rc1, rc2, rc3, rc4 = st.columns(4)
                    with rc1:
                        st.metric("Subtotal", f"${data['subtotal_usd']:,.2f}")
                    with rc2:
                        st.metric("Price Variance", f"{data['variance_pct']:+.2f}%")
                    with rc3:
                        st.metric("SLA Penalty Deduction", f"-${data['sla_penalty_usd']:,.2f}")
                    with rc4:
                        st.metric("Final Payable Amount", f"${data['final_payable_usd']:,.2f}")

                    if data["routing_status"] == "AUTO_APPROVED":
                        st.markdown(f'<div class="badge-auto" style="display:inline-block; margin-top:10px;">STATUS: AUTO APPROVED (STP)</div>', unsafe_allow_html=True)
                    else:
                        st.markdown(f'<div class="badge-hitl" style="display:inline-block; margin-top:10px;">STATUS: ROUTED TO HITL APPROVAL QUEUE</div>', unsafe_allow_html=True)
                    st.info(f"**Decision Reason**: {data['routing_reason']}")
                else:
                    st.error(f"Reconciliation error ({res.status_code}): {res.text}")
            except Exception as e:
                st.error(f"Failed to connect to API: {e}")


# ---------------------------------------------------------------------------
# View 3: HITL Review Queue
# ---------------------------------------------------------------------------

elif navigation == "HITL Review Queue":
    st.title("🧑‍💻 Human-In-The-Loop Exception Queue")
    st.markdown("Procurement Specialist review workbench for commercial discrepancies, price creep, and high-value orders.")

    invoices = fetch_invoices(status_filter="HITL_PENDING")
    if not invoices:
        st.success("🎉 All invoices cleared! No discrepancies pending human review.")
    else:
        st.write(f"Pending Exception Items: **{len(invoices)}**")
        for inv in invoices:
            with st.expander(f"⚠️ #{inv['invoice_number']} | {inv['vendor_name']} | PO: {inv['po_number']} | Subtotal: ${inv['subtotal_usd']:,.2f} | Variance: {inv['variance_pct']:+.2f}%"):
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.write(f"**Invoice ID**: `{inv['id']}`")
                    st.write(f"**Vendor**: {inv['vendor_name']}")
                    st.write(f"**PO Ref**: {inv['po_number']}")
                with c2:
                    st.write(f"**Subtotal**: ${inv['subtotal_usd']:,.2f}")
                    st.write(f"**Price Variance**: {inv['variance_pct']:+.2f}%")
                    st.write(f"**SLA Penalty**: -${inv['sla_penalty_usd']:,.2f}")
                with c3:
                    st.write(f"**Final Payable**: **${inv['final_payable_usd']:,.2f}**")
                    st.write(f"**Created At**: {inv['created_at'][:19]}")

                notes = st.text_input(f"Decision Justification Notes (Invoice #{inv['id']})", key=f"notes_{inv['id']}")

                b_col1, b_col2, _ = st.columns([1, 1, 4])
                with b_col1:
                    if st.button("✅ Approve", key=f"app_{inv['id']}", type="primary"):
                        res = requests.post(
                            f"{API_URL}/api/v1/invoices/{inv['id']}/decision",
                            json={"decision": "APPROVE", "reviewer_notes": notes},
                            headers=headers,
                        )
                        if res.status_code == 200:
                            st.success("Invoice Approved!")
                            st.rerun()
                        else:
                            st.error(res.text)

                with b_col2:
                    if st.button("❌ Reject", key=f"rej_{inv['id']}"):
                        res = requests.post(
                            f"{API_URL}/api/v1/invoices/{inv['id']}/decision",
                            json={"decision": "REJECT", "reviewer_notes": notes or "Discrepancy rejected by specialist"},
                            headers=headers,
                        )
                        if res.status_code == 200:
                            st.warning("Invoice Rejected & Sent Back to Vendor")
                            st.rerun()
                        else:
                            st.error(res.text)


# ---------------------------------------------------------------------------
# View 4: Contract RAG Explorer
# ---------------------------------------------------------------------------

elif navigation == "Contract RAG Explorer":
    st.title("📜 Supplier Contract RAG Explorer")
    st.markdown("Semantic search across supplier master service agreements, liquidated damages clauses, and payment terms.")

    query = st.text_input("Enter natural language clause query", "What is the penalty for late delivery lead times?")
    top_k = st.slider("Top clauses to retrieve", 1, 5, 3)

    if st.button("🔍 Search Contract Clauses"):
        try:
            res = requests.post(
                f"{API_URL}/api/v1/contracts/search",
                json={"query": query, "top_k": top_k},
                headers=headers,
            )
            if res.status_code == 200:
                clauses = res.json()
                if not clauses:
                    st.info("No matching contractual clauses found.")
                for cl in clauses:
                    st.markdown(f"""
                    <div class="metric-card" style="margin-bottom: 15px;">
                        <div style="display:flex; justify-content:space-between;">
                            <span class="badge-auto">{cl['clause_category']}</span>
                            <span style="color:#38BDF8; font-size:0.85rem;">Similarity: {cl['similarity_score'] * 100:.1f}%</span>
                        </div>
                        <h4 style="margin: 10px 0 5px 0;">{cl['title']} ({cl['vendor_name']})</h4>
                        <p style="color: #CBD5E1; font-size: 0.95rem; line-height: 1.5;">{cl['content']}</p>
                    </div>
                    """, unsafe_allow_html=True)
            else:
                st.error(res.text)
        except Exception as e:
            st.error(f"Search request failed: {e}")


# ---------------------------------------------------------------------------
# View 5: Security & BOLA Defense
# ---------------------------------------------------------------------------

elif navigation == "Security & BOLA Defense":
    st.title("🛡️ OWASP API Security & BOLA Defense Verification")
    st.markdown("Demonstration of Broken Object Level Authorization (BOLA) isolation across enterprise tenants.")

    st.markdown("""
    **Current Authenticated User**:
    - Username: `{}`
    - Active Tenant ID: `{}`
    - Role: `{}`
    """.format(token_data.get("sub") if token_data else "None", token_data.get("tenant_id") if token_data else "None", token_data.get("role") if token_data else "None"))

    st.subheader("Test Cross-Tenant Access Attempt")
    target_id = st.number_input("Target Invoice ID to Probe", min_value=1, value=1)

    if st.button("⚔️ Probe Resource Ownership"):
        try:
            res = requests.get(f"{API_URL}/api/v1/invoices/{target_id}", headers=headers)
            if res.status_code == 200:
                inv = res.json()
                st.success(f"Access Allowed: Invoice #{inv['invoice_number']} belongs to your tenant (`{inv['tenant_id']}`).")
            elif res.status_code == 404:
                st.error(f"BOLA Defense Active: HTTP 404 Not Found. Invoice #{target_id} belongs to a different tenant or does not exist. Cross-tenant leakage blocked!")
            elif res.status_code == 401:
                st.error("Authentication expired or invalid.")
            else:
                st.error(f"Response ({res.status_code}): {res.text}")
        except Exception as e:
            st.error(f"Probe failed: {e}")
