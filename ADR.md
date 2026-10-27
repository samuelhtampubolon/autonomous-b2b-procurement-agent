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
