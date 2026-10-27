/**
 * Autonomous B2B Supply Chain & Procurement Agent
 * Client-Side Interactive Simulation & Data Engine
 * Author: Samuel H. Tampubolon
 */

// ---------------------------------------------------------------------------
// 1. Data Store: Contract Clauses for Local Semantic Vector Search
// ---------------------------------------------------------------------------
const CONTRACT_CLAUSES = [
  {
    id: 1,
    vendor: "SiliconForge Semiconductor Ltd",
    category: "SLA_PENALTY",
    title: "Semiconductor Lead-Time SLA & Penalty Terms",
    content: "Deliveries delayed beyond the contracted delivery date incur an automated penalty of 0.5% per calendar day, capped at 15.0% of the gross invoice subtotal.",
    keywords: ["late", "delay", "penalty", "lead time", "sla", "demurrage", "calendar day", "cap"]
  },
  {
    id: 2,
    vendor: "SiliconForge Semiconductor Ltd",
    category: "PRICING_TIER",
    title: "Volume Tier Rebate Schedule",
    content: "For bulk procurement batches exceeding 500 units within a calendar quarter, a tiered commercial rebate of 4.5% is credited upon straight-through matching.",
    keywords: ["volume", "tier", "rebate", "discount", "bulk", "batch", "quarter"]
  },
  {
    id: 3,
    vendor: "Apex Industrial Robotics & CNC",
    category: "SLA_PENALTY",
    title: "Factory Acceptance Delay Liquidated Damages",
    content: "Unscheduled assembly delays trigger liquidated damages of 0.8% per business day up to a contractual ceiling of 20.0% of equipment commitment.",
    keywords: ["liquidated damages", "unscheduled", "delay", "assembly", "machinery", "cnc", "robotics", "ceiling"]
  },
  {
    id: 4,
    vendor: "Apex Industrial Robotics & CNC",
    category: "PRICING_TIER",
    title: "Capital Equipment Warranty & Calibration",
    content: "Machinery unit price includes 24 months of on-site recalibration and spindle servicing. Unauthorized price indexation is strictly forbidden.",
    keywords: ["warranty", "calibration", "spindle", "price indexation", "servicing"]
  },
  {
    id: 5,
    vendor: "Horizon Cross-Border Cold Chain",
    category: "SLA_PENALTY",
    title: "Temperature Excursion & Transit Demurrage",
    content: "Cold-chain transit delays exceeding 24 hours incur a daily demurrage penalty of 1.0% up to a 25.0% cap to offset refrigeration degradation risks.",
    keywords: ["cold chain", "transit", "demurrage", "temperature", "freight", "logistics", "refrigeration"]
  },
  {
    id: 6,
    vendor: "SynthBio High-Purity Precursors",
    category: "PRICING_TIER",
    title: "ICIS Chemical Index Pegging Clause",
    content: "Raw chemical precursors are billed strictly based on ICIS spot index rates published as of the Purchase Order issuance date. Mid-cycle surcharges are prohibited.",
    keywords: ["icis", "chemical", "spot index", "raw materials", "precursor", "surcharge"]
  },
  {
    id: 7,
    vendor: "DataCore Enterprise Hardware",
    category: "PAYMENT_TERMS",
    title: "Prompt Payment Discount (2/10 Net 30)",
    content: "Standard settlement terms are Net 30. A 2.0% cash discount applies if payment is processed and cleared within 10 calendar days of automated reconciliation.",
    keywords: ["prompt payment", "discount", "net 30", "cash discount", "settlement", "terms"]
  }
];

// ---------------------------------------------------------------------------
// 2. Data Store: 50 Seed B2B Transactions
// ---------------------------------------------------------------------------
const VENDORS = [
  "SiliconForge Semiconductor Ltd",
  "Apex Industrial Robotics & CNC",
  "Horizon Cross-Border Cold Chain",
  "SynthBio High-Purity Precursors",
  "DataCore Enterprise Hardware"
];

const ITEMS = [
  { desc: "H100 Tensor Core GPU Server Blade", price: 28500.0 },
  { desc: "5-Axis High-Torque CNC Spindle", price: 4800.0 },
  { desc: "Cryogenic Reefer Freight Container 40ft", price: 3200.0 },
  { desc: "Industrial Ethylene Glycol 200L Drum", price: 850.0 },
  { desc: "FPGA PCIe Acceleration Card", price: 650.0 },
  { desc: "Precision Ceramic Ball Bearing Kit", price: 120.0 }
];

let INVOICES_DATA = [];

function generateSeedInvoices() {
  const list = [];
  for (let i = 1; i <= 50; i++) {
    const vendor = VENDORS[i % VENDORS.length];
    const item = ITEMS[i % ITEMS.length];
    const poNum = `PO-2026-${1000 + i}`;
    const invNum = `INV-2026-${8000 + i}`;
    const orderedQty = [5, 10, 15, 20, 25][i % 5];
    
    let billedQty = orderedQty;
    let billedPrice = item.price;
    let daysLate = 0;
    let status = "AUTO_APPROVED";
    let trigger = "Deterministic 3-way match passed";

    const scenario = i % 5;
    if (scenario === 1) { // Late Delivery with SLA Penalty
      daysLate = (i % 7) + 3;
    } else if (scenario === 2) { // Price Creep > 2%
      billedPrice = Math.round(item.price * 1.048 * 100) / 100;
      status = "HITL_PENDING";
      trigger = "Price variance +4.80% exceeds ±2.0% tolerance limit";
    } else if (scenario === 3) { // High Value Order
      if (item.price * orderedQty > 10000) {
        status = "HITL_PENDING";
        trigger = `High-value order ($${(item.price * orderedQty).toLocaleString()}) requires specialist sign-off`;
      }
    } else if (scenario === 4) { // Quantity Discrepancy
      billedQty = orderedQty + 2;
      status = "HITL_PENDING";
      trigger = `Quantity mismatch: Billed ${billedQty} vs PO ${orderedQty}`;
    }

    const subtotal = Math.round(billedQty * billedPrice * 100) / 100;
    const variancePct = Math.round(((billedPrice - item.price) / item.price) * 10000) / 100;

    // SLA penalty math
    const dailyRate = vendor.includes("Horizon") ? 0.01 : (vendor.includes("Apex") ? 0.008 : 0.005);
    const maxCap = vendor.includes("Horizon") ? 0.25 : 0.15;
    const appliedPenaltyPct = Math.min(maxCap, daysLate * dailyRate);
    const slaPenalty = Math.round(subtotal * appliedPenaltyPct * 100) / 100;
    const finalPayable = Math.round((subtotal - slaPenalty) * 100) / 100;

    // Simulate pre-resolved items
    if (i === 3 || i === 8 || i === 13) {
      status = "APPROVED";
      trigger = "Approved by senior analyst after contract clarification";
    } else if (i === 4 || i === 14) {
      status = "REJECTED";
      trigger = "Rejected: Vendor price creep exceeds contract cap";
    }

    list.push({
      id: i,
      poNumber: poNum,
      invoiceNumber: invNum,
      vendor: vendor,
      itemDescription: item.desc,
      orderedQty: orderedQty,
      billedQty: billedQty,
      agreedPrice: item.price,
      billedPrice: billedPrice,
      subtotal: subtotal,
      variancePct: variancePct,
      daysLate: daysLate,
      slaPenalty: slaPenalty,
      finalPayable: finalPayable,
      status: status,
      triggerReason: trigger,
      date: `2026-10-${String((i % 25) + 1).padStart(2, '0')}`
    });
  }
  return list;
}

INVOICES_DATA = generateSeedInvoices();

// ---------------------------------------------------------------------------
// 3. Simulator Presets
// ---------------------------------------------------------------------------
const PRESETS = {
  clean: {
    title: "Clean Match (SiliconForge GPUs)",
    orderedQty: 10,
    billedQty: 10,
    agreedPrice: 2850.0,
    billedPrice: 2850.0,
    daysLate: 0,
    dailyRate: 0.5,
    maxCap: 15.0
  },
  priceCreep: {
    title: "Price Creep +4.8% (Apex CNC Spindle)",
    orderedQty: 5,
    billedQty: 5,
    agreedPrice: 4800.0,
    billedPrice: 5030.40,
    daysLate: 0,
    dailyRate: 0.8,
    maxCap: 20.0
  },
  lateDelivery: {
    title: "Delayed 10 Days (Horizon Cold Chain)",
    orderedQty: 4,
    billedQty: 4,
    agreedPrice: 3200.0,
    billedPrice: 3200.0,
    daysLate: 10,
    dailyRate: 1.0,
    maxCap: 25.0
  },
  highValue: {
    title: "High-Value Order $28,500 (Server Cluster)",
    orderedQty: 10,
    billedQty: 10,
    agreedPrice: 2850.0,
    billedPrice: 2850.0,
    daysLate: 0,
    dailyRate: 0.5,
    maxCap: 15.0
  },
  qtyMismatch: {
    title: "Quantity Mismatch (SynthBio Solvents)",
    orderedQty: 10,
    billedQty: 12,
    agreedPrice: 850.0,
    billedPrice: 850.0,
    daysLate: 0,
    dailyRate: 0.4,
    maxCap: 12.0
  }
};

// ---------------------------------------------------------------------------
// 4. Interactive Simulator Engine
// ---------------------------------------------------------------------------
function updateSimulator() {
  const orderedQty = parseInt(document.getElementById("sim-ordered-qty").value) || 1;
  const billedQty = parseInt(document.getElementById("sim-billed-qty").value) || 1;
  const agreedPrice = parseFloat(document.getElementById("sim-agreed-price").value) || 0;
  const billedPrice = parseFloat(document.getElementById("sim-billed-price").value) || 0;
  const daysLate = parseInt(document.getElementById("sim-days-late").value) || 0;
  const dailyRatePct = parseFloat(document.getElementById("sim-penalty-rate").value) || 0.5;
  const maxCapPct = parseFloat(document.getElementById("sim-penalty-cap").value) || 15.0;

  // Sync label
  document.getElementById("sim-days-late-val").innerText = `${daysLate} days`;

  // Calculations
  const subtotal = Math.round(billedQty * billedPrice * 100) / 100;
  const variancePct = agreedPrice > 0 ? Math.round(((billedPrice - agreedPrice) / agreedPrice) * 10000) / 100 : 0;
  const appliedPenaltyPct = Math.min(maxCapPct, daysLate * dailyRatePct);
  const slaPenalty = Math.round(subtotal * (appliedPenaltyPct / 100) * 100) / 100;
  const finalPayable = Math.max(0, Math.round((subtotal - slaPenalty) * 100) / 100);

  // Update UI values
  document.getElementById("sim-out-ordered-qty").innerText = `${orderedQty} units`;
  document.getElementById("sim-out-billed-qty").innerText = `${billedQty} units`;
  document.getElementById("sim-out-agreed-price").innerText = `$${agreedPrice.toLocaleString('en-US', {minimumFractionDigits: 2})}`;
  document.getElementById("sim-out-billed-price").innerText = `$${billedPrice.toLocaleString('en-US', {minimumFractionDigits: 2})}`;
  document.getElementById("sim-out-subtotal").innerText = `$${subtotal.toLocaleString('en-US', {minimumFractionDigits: 2})}`;
  
  const varEl = document.getElementById("sim-out-variance");
  varEl.innerText = `${variancePct > 0 ? '+' : ''}${variancePct.toFixed(2)}%`;
  varEl.style.color = Math.abs(variancePct) > 2.0 ? 'var(--accent-rose)' : 'var(--accent-emerald)';

  document.getElementById("sim-out-days-late").innerText = `${daysLate} days late`;
  document.getElementById("sim-out-penalty-pct").innerText = `${appliedPenaltyPct.toFixed(2)}%`;
  document.getElementById("sim-out-penalty-usd").innerText = `-$${slaPenalty.toLocaleString('en-US', {minimumFractionDigits: 2})}`;
  document.getElementById("sim-out-final-payable").innerText = `$${finalPayable.toLocaleString('en-US', {minimumFractionDigits: 2})}`;

  // Routing Decision
  const statusBanner = document.getElementById("sim-status-banner");
  const statusMain = document.getElementById("sim-status-main");
  const statusReason = document.getElementById("sim-status-reason");

  if (Math.abs(variancePct) > 2.0) {
    statusBanner.className = "telemetry-status-banner status-hitl";
    statusMain.innerText = "HITL PENDING: PRICE CREEP EXCEPTION";
    statusReason.innerText = `Price variance (${variancePct > 0 ? '+' : ''}${variancePct}%) exceeds contractual ±2.0% tolerance limit. Routing to specialist workbench.`;
  } else if (subtotal > 10000.0) {
    statusBanner.className = "telemetry-status-banner status-hitl";
    statusMain.innerText = "HITL PENDING: HIGH-VALUE ORDER";
    statusReason.innerText = `Invoice gross total ($${subtotal.toLocaleString()}) exceeds the $10,000 automated approval threshold. Requires CPO/analyst sign-off.`;
  } else if (orderedQty !== billedQty) {
    statusBanner.className = "telemetry-status-banner status-hitl";
    statusMain.innerText = "HITL PENDING: QUANTITY MISMATCH";
    statusReason.innerText = `Physical goods receipt verification failed: Billed ${billedQty} vs Purchase Order ${orderedQty}.`;
  } else {
    statusBanner.className = "telemetry-status-banner status-auto";
    statusMain.innerText = "AUTO APPROVED: STRAIGHT-THROUGH (STP)";
    statusReason.innerText = "Deterministic 3-way match passed: price variance, quantity, and lead-time SLAs verified within policy thresholds.";
  }
}

function loadPreset(key) {
  const p = PRESETS[key];
  if (!p) return;

  document.getElementById("sim-ordered-qty").value = p.orderedQty;
  document.getElementById("sim-billed-qty").value = p.billedQty;
  document.getElementById("sim-agreed-price").value = p.agreedPrice;
  document.getElementById("sim-billed-price").value = p.billedPrice;
  document.getElementById("sim-days-late").value = p.daysLate;
  document.getElementById("sim-penalty-rate").value = p.dailyRate;
  document.getElementById("sim-penalty-cap").value = p.maxCap;

  document.querySelectorAll(".preset-btn").forEach(btn => btn.classList.remove("active"));
  const clicked = document.getElementById(`btn-preset-${key}`);
  if (clicked) clicked.classList.add("active");

  updateSimulator();
}

// ---------------------------------------------------------------------------
// 5. HITL Workbench & Modal Drawer
// ---------------------------------------------------------------------------
let currentFilter = "ALL";

function renderHITLTable() {
  const tbody = document.getElementById("hitl-table-body");
  if (!tbody) return;
  tbody.innerHTML = "";

  const query = (document.getElementById("hitl-search-input")?.value || "").toLowerCase();

  const filtered = INVOICES_DATA.filter(inv => {
    const matchesFilter = 
      currentFilter === "ALL" ||
      (currentFilter === "HITL_PENDING" && inv.status === "HITL_PENDING") ||
      (currentFilter === "AUTO_APPROVED" && inv.status === "AUTO_APPROVED") ||
      (currentFilter === "RESOLVED" && (inv.status === "APPROVED" || inv.status === "REJECTED"));

    const matchesSearch = 
      inv.invoiceNumber.toLowerCase().includes(query) ||
      inv.poNumber.toLowerCase().includes(query) ||
      inv.vendor.toLowerCase().includes(query) ||
      inv.itemDescription.toLowerCase().includes(query);

    return matchesFilter && matchesSearch;
  });

  document.getElementById("hitl-count-badge").innerText = `${filtered.length} Items`;

  filtered.forEach(inv => {
    const tr = document.createElement("tr");

    let badgeClass = "badge-green";
    if (inv.status === "HITL_PENDING") badgeClass = "badge-yellow";
    if (inv.status === "REJECTED") badgeClass = "badge-red";

    tr.innerHTML = `
      <td><strong>${inv.invoiceNumber}</strong><br><span style="font-size:0.75rem; color:var(--text-dim);">${inv.poNumber}</span></td>
      <td><strong>${inv.vendor}</strong><br><span style="font-size:0.78rem; color:var(--text-muted);">${inv.itemDescription}</span></td>
      <td>$${inv.subtotal.toLocaleString('en-US', {minimumFractionDigits: 2})}</td>
      <td style="color:${Math.abs(inv.variancePct) > 2 ? 'var(--accent-rose)' : 'var(--text-muted)'}; font-weight:600;">
        ${inv.variancePct > 0 ? '+' : ''}${inv.variancePct.toFixed(2)}%
      </td>
      <td style="color:${inv.slaPenalty > 0 ? 'var(--accent-amber)' : 'var(--text-dim)'};">
        ${inv.slaPenalty > 0 ? `-$${inv.slaPenalty.toLocaleString('en-US', {minimumFractionDigits: 2})}` : '$0.00'}
      </td>
      <td><strong>$${inv.finalPayable.toLocaleString('en-US', {minimumFractionDigits: 2})}</strong></td>
      <td><span class="badge ${badgeClass}">${inv.status}</span></td>
      <td>
        <div class="action-btn-group">
          <button class="btn-sm-approve" onclick="openInvoiceModal(${inv.id})">Inspect</button>
          ${inv.status === 'HITL_PENDING' ? `
            <button class="btn-sm-approve" style="background:rgba(16,185,129,0.3);" onclick="quickApprove(${inv.id})">✓</button>
            <button class="btn-sm-reject" onclick="quickReject(${inv.id})">✕</button>
          ` : ''}
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function setHITLFilter(filter, el) {
  currentFilter = filter;
  document.querySelectorAll(".filter-tab").forEach(tab => tab.classList.remove("active"));
  el.classList.add("active");
  renderHITLTable();
}

let activeModalInvoice = null;

function openInvoiceModal(id) {
  const inv = INVOICES_DATA.find(i => i.id === id);
  if (!inv) return;
  activeModalInvoice = inv;

  document.getElementById("modal-title").innerText = `Reconciliation Audit: ${inv.invoiceNumber}`;
  document.getElementById("modal-po").innerText = inv.poNumber;
  document.getElementById("modal-vendor").innerText = inv.vendor;
  document.getElementById("modal-item").innerText = inv.itemDescription;
  document.getElementById("modal-ordered-qty").innerText = `${inv.orderedQty} units`;
  document.getElementById("modal-billed-qty").innerText = `${inv.billedQty} units`;
  document.getElementById("modal-agreed-price").innerText = `$${inv.agreedPrice.toLocaleString()}`;
  document.getElementById("modal-billed-price").innerText = `$${inv.billedPrice.toLocaleString()}`;
  document.getElementById("modal-subtotal").innerText = `$${inv.subtotal.toLocaleString()}`;
  document.getElementById("modal-variance").innerText = `${inv.variancePct > 0 ? '+' : ''}${inv.variancePct}%`;
  document.getElementById("modal-days-late").innerText = `${inv.daysLate} days`;
  document.getElementById("modal-sla-deduction").innerText = `-$${inv.slaPenalty.toLocaleString()}`;
  document.getElementById("modal-payable").innerText = `$${inv.finalPayable.toLocaleString()}`;
  document.getElementById("modal-reason").innerText = inv.triggerReason;

  // Retrieve relevant contract clause snippet for this vendor
  const clause = CONTRACT_CLAUSES.find(c => c.vendor === inv.vendor) || CONTRACT_CLAUSES[0];
  document.getElementById("modal-clause-title").innerText = `${clause.title} (${clause.category})`;
  document.getElementById("modal-clause-body").innerText = clause.content;

  document.getElementById("invoice-modal").classList.add("active");
}

function closeModal() {
  document.getElementById("invoice-modal").classList.remove("active");
  activeModalInvoice = null;
}

function decideFromModal(decision) {
  if (!activeModalInvoice) return;
  const notes = document.getElementById("modal-notes").value;
  if (decision === 'APPROVE') {
    activeModalInvoice.status = "APPROVED";
    activeModalInvoice.triggerReason = `Approved by Human Specialist: ${notes || 'Verified against contract terms.'}`;
  } else {
    activeModalInvoice.status = "REJECTED";
    activeModalInvoice.triggerReason = `Rejected: ${notes || 'Discrepancy exceeds permissible commercial tolerances.'}`;
  }
  closeModal();
  renderHITLTable();
  updateROIMetrics();
}

function quickApprove(id) {
  const inv = INVOICES_DATA.find(i => i.id === id);
  if (inv) {
    inv.status = "APPROVED";
    inv.triggerReason = "Approved by specialist via quick action";
    renderHITLTable();
    updateROIMetrics();
  }
}

function quickReject(id) {
  const inv = INVOICES_DATA.find(i => i.id === id);
  if (inv) {
    inv.status = "REJECTED";
    inv.triggerReason = "Rejected: Variance rejected by specialist";
    renderHITLTable();
    updateROIMetrics();
  }
}

// ---------------------------------------------------------------------------
// 6. Contract RAG Semantic Search
// ---------------------------------------------------------------------------
function searchContracts() {
  const query = (document.getElementById("rag-search-input")?.value || "").toLowerCase().trim();
  const container = document.getElementById("rag-results-container");
  if (!container) return;

  if (!query) {
    renderAllClauses();
    return;
  }

  const queryTokens = query.split(/\s+/).filter(t => t.length > 2);

  const scored = CONTRACT_CLAUSES.map(clause => {
    let score = 0;
    const clauseText = `${clause.title} ${clause.content} ${clause.keywords.join(" ")}`.toLowerCase();

    queryTokens.forEach(token => {
      if (clauseText.includes(token)) score += 1.5;
    });

    if (clauseText.includes(query)) score += 3.0;

    const normalized = Math.min(0.99, Math.max(0.12, (score / (queryTokens.length * 2.5))));
    return { ...clause, score: normalized };
  });

  scored.sort((a, b) => b.score - a.score);

  container.innerHTML = "";
  scored.forEach(c => {
    const card = document.createElement("div");
    card.className = "glass-panel rag-card";
    card.innerHTML = `
      <div style="margin-bottom: 12px;">
        <span class="rag-score">${(c.score * 100).toFixed(1)}% Match</span>
        <div class="rag-category">${c.category}</div>
        <h4 style="margin-top: 4px;">${c.title}</h4>
        <div style="font-size:0.8rem; color:var(--text-dim); margin-bottom: 8px;">Vendor: ${c.vendor}</div>
      </div>
      <p style="font-size: 0.9rem; line-height: 1.5;">${c.content}</p>
    `;
    container.appendChild(card);
  });
}

function renderAllClauses() {
  const container = document.getElementById("rag-results-container");
  if (!container) return;
  container.innerHTML = "";
  CONTRACT_CLAUSES.forEach(c => {
    const card = document.createElement("div");
    card.className = "glass-panel rag-card";
    card.innerHTML = `
      <div style="margin-bottom: 12px;">
        <span class="rag-score">Contract Master</span>
        <div class="rag-category">${c.category}</div>
        <h4 style="margin-top: 4px;">${c.title}</h4>
        <div style="font-size:0.8rem; color:var(--text-dim); margin-bottom: 8px;">Vendor: ${c.vendor}</div>
      </div>
      <p style="font-size: 0.9rem; line-height: 1.5;">${c.content}</p>
    `;
    container.appendChild(card);
  });
}

// ---------------------------------------------------------------------------
// 7. Dynamic ROI & Unit Economics Modeler
// ---------------------------------------------------------------------------
function updateROIMetrics() {
  const monthlyInvoices = parseInt(document.getElementById("roi-volume-slider").value) || 2500;
  const manualMinutes = parseFloat(document.getElementById("roi-minutes-slider").value) || 18.0;
  const hourlyWage = parseFloat(document.getElementById("roi-wage-slider").value) || 45.0;

  // Sync labels
  document.getElementById("roi-volume-val").innerText = `${monthlyInvoices.toLocaleString()} invoices / mo`;
  document.getElementById("roi-minutes-val").innerText = `${manualMinutes} mins / invoice`;
  document.getElementById("roi-wage-val").innerText = `$${hourlyWage} / hr`;

  // Governing Equations
  // Gross labor hours saved (82% STP saves 100% time, 18% HITL saves 65% time)
  const blendedSavingsPct = 0.82 + (0.18 * 0.65); // ~0.937
  const laborHoursSaved = (monthlyInvoices * manualMinutes * blendedSavingsPct) / 60.0;
  const grossLaborSavedUSD = laborHoursSaved * hourlyWage;

  // AI compute and infrastructure cost
  const avgTokensPerInvoice = 450;
  const tokenCostPer1k = 0.002;
  const aiTokensCost = (monthlyInvoices * avgTokensPerInvoice / 1000.0) * tokenCostPer1k;
  const fixedInfraCost = 15.00;
  const totalAICostUSD = aiTokensCost + fixedInfraCost;

  // Estimated SLA penalties recovered (approx 8.5% of total volume has late penalties averaging $140)
  const slaRecoveryUSD = monthlyInvoices * 0.085 * 140.0;

  // Net OpEx Reduction
  const netOpExSavingsUSD = grossLaborSavedUSD - totalAICostUSD;
  const totalBenefitUSD = netOpExSavingsUSD + slaRecoveryUSD;
  const roiMultiple = Math.round((totalBenefitUSD / Math.max(totalAICostUSD, 1.0)) * 10) / 10;

  // Render to UI
  document.getElementById("roi-gross-saved").innerText = `$${Math.round(grossLaborSavedUSD).toLocaleString()}`;
  document.getElementById("roi-ai-cost").innerText = `$${Math.round(totalAICostUSD).toLocaleString()}`;
  document.getElementById("roi-sla-recovered").innerText = `$${Math.round(slaRecoveryUSD).toLocaleString()}`;
  document.getElementById("roi-net-monthly").innerText = `$${Math.round(totalBenefitUSD).toLocaleString()}`;
  document.getElementById("roi-net-annual").innerText = `$${Math.round(totalBenefitUSD * 12).toLocaleString()} / year`;
  document.getElementById("roi-multiple-display").innerText = `${roiMultiple}x ROI`;
}

// ---------------------------------------------------------------------------
// 8. BPMN Workflow Visualizer Step Details
// ---------------------------------------------------------------------------
const BPMN_STEPS = {
  1: {
    title: "1. REST API Ingestion & Rate Limiting",
    desc: "Ingests semi-structured vendor invoices via `POST /api/v1/invoices/reconcile`. Enforces slowapi rate limits (60 req/min) and caps payload size to prevent DoS.",
    code: "@router.post('/invoices/reconcile')\n@limiter.limit('60/minute')\ndef reconcile_vendor_invoice(...):"
  },
  2: {
    title: "2. Security & BOLA Isolation Defense",
    desc: "Enforces OWASP API1: Broken Object Level Authorization. Injects `get_owned_resource()` ensuring all PO and invoice queries are scoped strictly to the authenticated tenant_id.",
    code: "resource = db.query(model).filter(\n    model.id == resource_id,\n    model.tenant_id == current_user_tenant_id\n).first()"
  },
  3: {
    title: "3. Deterministic 3-Way Math Engine",
    desc: "Calculates quantity discrepancies and unit price variance against the contracted purchase order in sub-50ms with zero numerical hallucination.",
    code: "price_variance_pct = ((billed_price - agreed_price) / agreed_price) * 100.0\nsubtotal_usd = billed_qty * billed_price"
  },
  4: {
    title: "4. Contract SLA Penalty Deduction",
    desc: "Calculates delivery lead-time slippage against contracted dates and applies daily penalty rates up to vendor contract ceilings.",
    code: "days_late = max(0, (actual_date - expected_date).days)\napplied_pct = min(max_cap, days_late * daily_rate)\nsla_penalty_usd = subtotal_usd * applied_pct"
  },
  5: {
    title: "5. Risk Routing: STP vs HITL Guardrail",
    desc: "Invoices with price variance > 2.0% or total value > $10,000 route automatically to the Human-in-the-Loop review queue. Clean invoices straight-through process instantly.",
    code: "if abs(price_variance_pct) > 2.0 or subtotal_usd > 10000.0:\n    routing_status = 'HITL_PENDING'\nelse:\n    routing_status = 'AUTO_APPROVED'"
  },
  6: {
    title: "6. Immutable Audit Trail & ROI Ledger",
    desc: "Logs every action, token compute cost, latency, and labor time saved in an immutable audit ledger accessible to Chief Procurement Officers.",
    code: "audit_log = AgentAuditLog(\n    action_taken=f'RECONCILE_{routing_status}',\n    labor_saved_usd=13.50,\n    compute_cost_usd=compute_cost\n)"
  }
};

function selectBPMNStep(stepNum) {
  document.querySelectorAll(".bpmn-node").forEach(node => node.classList.remove("active"));
  const activeNode = document.getElementById(`bpmn-node-${stepNum}`);
  if (activeNode) activeNode.classList.add("active");

  const step = BPMN_STEPS[stepNum];
  if (!step) return;

  document.getElementById("bpmn-step-title").innerText = step.title;
  document.getElementById("bpmn-step-desc").innerText = step.desc;
  document.getElementById("bpmn-step-code").innerText = step.code;
}

// ---------------------------------------------------------------------------
// 9. OWASP BOLA Interactive Simulator
// ---------------------------------------------------------------------------
function simulateBOLACheck(persona) {
  const outBox = document.getElementById("bola-output-box");
  if (!outBox) return;

  if (persona === 'legit') {
    outBox.innerHTML = `
      <div style="color:var(--accent-emerald); font-weight:700; margin-bottom:6px;">✓ HTTP 200 OK - Access Granted</div>
      <div style="font-size:0.85rem; color:#CBD5E1;">
        <strong>Authenticated Subject</strong>: <code>analyst_alpha</code><br>
        <strong>Tenant ID</strong>: <code>tenant_alpha</code><br>
        <strong>Requested Resource</strong>: Invoice #INV-2026-8001 (Owner: <code>tenant_alpha</code>)<br>
        <strong>Result</strong>: Resource verified. Returning complete 3-way match details.
      </div>
    `;
  } else {
    outBox.innerHTML = `
      <div style="color:var(--accent-rose); font-weight:700; margin-bottom:6px;">🛡️ HTTP 404 Not Found - BOLA Defense Triggered</div>
      <div style="font-size:0.85rem; color:#CBD5E1;">
        <strong>Authenticated Subject</strong>: <code>hacker_beta</code> (Attacker)<br>
        <strong>Tenant ID</strong>: <code>tenant_beta</code><br>
        <strong>Attempted Target</strong>: Invoice #INV-2026-8001 (Belongs to <code>tenant_alpha</code>)<br>
        <strong>SQL Constraint</strong>: <code>WHERE id = 1 AND tenant_id = 'tenant_beta'</code> returned 0 rows.<br>
        <strong>Security Policy</strong>: Generic 404 returned to prevent resource enumeration and cross-tenant data leakage.
      </div>
    `;
  }
}

// ---------------------------------------------------------------------------
// Initialization
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  // Simulator listeners
  const simInputs = [
    "sim-ordered-qty", "sim-billed-qty", "sim-agreed-price",
    "sim-billed-price", "sim-days-late", "sim-penalty-rate", "sim-penalty-cap"
  ];
  simInputs.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("input", updateSimulator);
  });

  // ROI listeners
  ["roi-volume-slider", "roi-minutes-slider", "roi-wage-slider"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("input", updateROIMetrics);
  });

  // Search listeners
  const hitlSearch = document.getElementById("hitl-search-input");
  if (hitlSearch) hitlSearch.addEventListener("input", renderHITLTable);

  const ragSearch = document.getElementById("rag-search-input");
  if (ragSearch) ragSearch.addEventListener("input", searchContracts);

  // Initial runs
  loadPreset("clean");
  renderHITLTable();
  renderAllClauses();
  updateROIMetrics();
  selectBPMNStep(3);
  simulateBOLACheck('legit');
});
