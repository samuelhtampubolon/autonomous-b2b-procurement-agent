# Architecture Decision Records (ADR)
## Autonomous B2B Supply Chain & Procurement Agent

---

### ADR-001: Deterministic Hybrid Architecture for 3-Way Reconciliation
- **Status**: Accepted
- **Context**: B2B enterprises process thousands of supplier invoices monthly. Relying entirely on probabilistic Large Language Models (LLMs) for commercial math creates hallucination risk, computational latency, and compliance audit failure.
- **Decision**: We decoupled reconciliation into a deterministic mathematical engine (matching quantities, price tolerances, and delivery SLAs) paired with a semantic RAG module for supplier contract interpretation.
- **Consequences**:
  - P95 latency is sub-50ms for matching checks.
  - Zero numerical hallucination on invoice totals, penalty deductions, and tax math.
  - Strict audit trail with immutable `AgentAuditLog` records.

---

### ADR-002: Multi-Tenant Broken Object Level Authorization (BOLA) Defense
- **Status**: Accepted
- **Context**: OWASP API1 (BOLA) is the #1 vulnerability in enterprise procurement software, where authenticated users in Tenant B query or modify invoices of Tenant A by altering URL resource IDs.
- **Decision**: Every database lookup for a resource (invoices, purchase orders, contracts) must enforce tenant ownership checks via the `get_owned_resource(model, resource_id, current_user, db)` dependency. The SQL query explicitly includes `WHERE id = :id AND tenant_id = :tenant_id`. If ownership does not match, an HTTP 404 Not Found is returned immediately to prevent tenant enumeration.
- **Consequences**:
  - Total tenant isolation at the database layer.
  - Rejection verified in automated unit test suite (`tests/test_security_and_bola.py`).

---

### ADR-003: Local Deterministic Vector RAG for Supplier Contract Clauses
- **Status**: Accepted
- **Context**: Enterprise procurement contracts contain legal clauses (liquidated damages, early payment rebates, demurrage caps). Requiring third-party vector databases or cloud embedding APIs creates network dependencies and privacy concerns.
- **Decision**: Implemented an in-memory cosine similarity RAG engine with term-frequency vectorization, capable of zero-dependency fallback on standard SQLite/Python standard libraries.
- **Consequences**:
  - Operates 100% offline in isolated on-premise environments.
  - Zero recurring cloud vector DB subscription costs.

---

### ADR-004: Unit Economics & Human-in-the-Loop (HITL) Guardrails
- **Status**: Accepted
- **Context**: Enterprises struggle to quantify whether agentic automation yields net OpEx reduction when considering LLM token costs and human review time.
- **Decision**: We codified unit economics into governing equations:
  $$ \text{Net OpEx Savings} = \left(\frac{\text{Manual Mins}}{60} \times \text{Hourly Wage} \times \text{Count}\right) - (\text{AI Tokens Cost} + \text{Infra}) $$
  $$ \text{SLA Penalty Recovery} = \text{Subtotal} \times \min(\text{Cap}, \text{Days Late} \times \text{Daily Rate}) $$
  All invoices with price creep $> 2.0\%$, values $> \$10,000$, or quantity mismatches route automatically to an interactive Human-in-the-Loop review queue.
- **Consequences**:
  - Full transparency for Chief Procurement Officers (CPOs).
  - Straight-Through Processing (STP) rate tracked in real time.

---

### ADR-005: Dual-Surface Live Deployment & Zero-Friction Startup
- **Status**: Accepted
- **Context**: Developers and evaluators need instant execution without manual database configuration, container builds, or port conflict hunting.
- **Decision**: Created `run_local.py` with automated socket preflight checks, automatic fallback to the next available port, SQLite auto-seeding of 50 realistic B2B transactions, and combined FastAPI backend + Streamlit control tower spawning with graceful signal traps.
- **Consequences**:
  - Instant one-command launch via `python run_local.py`.
  - Zero configuration or environment variable tuning required.

---

### ADR-006: Security Hardening Baseline (v1.1)
- **Status**: Accepted
- **Context**: v1.0 shipped a hardcoded JWT secret, relied on passlib (which breaks with bcrypt 4.1 and newer), had no role model, allowed an invoice to be decided repeatedly, and returned validation errors that echoed submitted passwords.
- **Decision**: Generate per-install signing keys and require an explicit strong key in production; use bcrypt directly with constant-time login and per-account lockout; require `iss`/`aud`/`jti`/`nbf`/`typ` claims and support revocation; introduce RBAC (`procurement_specialist`, `cpo`, `auditor`) with CPO sign-off above $25k; make HITL decisions an atomic compare-and-set; reject duplicate invoice numbers; cap request bodies at 1 MB; add CSP and related headers, a Host allow-list, and non-reflective error bodies.
- **Consequences**: Production deployments fail fast on insecure configuration. 40 additional tests cover the controls. Throttling and revocation state is per process (see SECURITY.md).

---

### ADR-007: Hash-Chained, Append-Only Audit Ledger
- **Status**: Accepted
- **Context**: "Immutable audit log" in v1.0 was a convention only; any database user could edit history undetected.
- **Decision**: Each `AgentAuditLog` row stores `prev_hash` and `entry_hash = SHA-256(prev_hash || canonical_json(entry))`, chained per tenant. An ORM `before_update` guard and a SQLite trigger block edits; `/api/v1/audit/verify` recomputes the chain and reports the first invalid entry. Legacy rows are sealed once during upgrade.
- **Consequences**: Tampering is detectable even by someone who bypasses the application. Timestamps are stored as naive UTC so hashes are identical across SQLite and PostgreSQL.

---

### ADR-008: Prompt-Injection Firewall Forces Human Review
- **Status**: Accepted
- **Context**: Vendor remarks are attacker-controlled and are destined for contract analysis and LLM prompts.
- **Decision**: Normalize (NFKC), strip control, zero-width and bidi-override characters, then redact 14 rule families (instruction override, role spoofing, chat-template tokens, persona and approval override, secret exfiltration, markup). Any flag adds a security-hold reason, so the invoice can never be auto-approved.
- **Consequences**: A successful injection cannot move money without a human. Some benign text (for example "Contact our developer: ...") may be held for review; this is the intended fail-safe direction.

---

### ADR-009: Static Showcase Mirrors the Engine and Is Parity-Tested
- **Status**: Accepted
- **Context**: The GitHub Pages demo previously re-implemented rules by hand and drifted from the backend (for example a $28.5k "clean" preset that the API would route to HITL).
- **Decision**: `docs/assets/js/engine.js` ports `compute_match`, `assess_untrusted_text`, the TF-IDF RAG and the ledger hash. `tests/test_docs_parity.py` executes it under Node and compares outputs with Python. The page runs under a strict CSP with Trusted Types and builds all DOM without `innerHTML`.
- **Consequences**: Any rule change that is not mirrored in the site fails CI.
