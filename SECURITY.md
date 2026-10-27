# Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately through
[GitHub Security Advisories](https://github.com/samuelhtampubolon/autonomous-b2b-procurement-agent/security/advisories/new)
rather than a public issue. Include the affected endpoint or file, reproduction steps, and the impact you observed.
You can expect an acknowledgement within 5 business days.

## Supported versions

| Version | Supported |
| :--- | :--- |
| 1.1.x | Yes |
| 1.0.x | No: it shipped a hardcoded JWT signing key and documented default passwords. Upgrade; the key is refused at startup and old demo passwords are rotated automatically. |

## Threat model (summary)

| Asset | Threat | Mitigation |
| :--- | :--- | :--- |
| Tenant invoices and contracts | Cross-tenant access by an authenticated user (BOLA) | Every query scoped by `tenant_id`; foreign IDs return the same 404 as missing IDs |
| Payment approvals | Unauthorized or duplicate approval, approval by the wrong role | RBAC, CPO sign-off above `CPO_APPROVAL_THRESHOLD_USD`, atomic compare-and-set, 409 on re-decision |
| Vendor payments | Duplicate-invoice fraud, double billing on a settled PO | Unique `(tenant_id, invoice_number)`, `duplicate_billing_suspected` flag forces review |
| Automated decisions | Prompt injection in vendor-supplied text | Injection firewall (NFKC, invisible/bidi stripping, 14 rules); any flag forces HITL |
| Audit trail | Insider edits or deletions of history | Per-tenant SHA-256 hash chain, append-only ORM guard, SQLite trigger, `/audit/verify` |
| Accounts | Credential stuffing, user enumeration, token theft | bcrypt, constant-time login, per-account lockout, per-IP rate limit, short-lived revocable JWTs |
| Availability | Oversized or flooding requests | 1 MB body cap (chunked included), slowapi limits, bounded numeric inputs, pagination caps |
| Logs | Leakage of IBANs, card numbers, emails, tokens | `PIIRedactingFilter` on root and server loggers |

## Secret handling

- `SECRET_KEY` is never hardcoded. In development a random key is generated in `data/.jwt_secret` (mode 600, git-ignored).
  In production (`ENV=production`) the API refuses to start unless a key of at least 32 bytes is provided, and it rejects
  the key published in v1.0.
- No password is stored in the repository. Demo persona passwords are generated per installation in
  `data/.demo_credentials.json` (mode 600, git-ignored), or taken from `DEMO_*_PASSWORD`. Existing demo accounts whose
  hash does not match the configured password (for example the retired v1.0 defaults) are re-hashed on startup.
- Demo accounts are created only outside production, or in production when `DEMO_MODE=true` and every
  `DEMO_*_PASSWORD` is set explicitly.
- The retired v1.0 signing key is blocklisted by its SHA-256 digest only, so the value itself no longer appears in
  the source. A regression test fails if any tracked file contains it or the retired default passwords.
- Docker images contain no secrets; inject them at runtime.

## Deployment checklist

1. Set `ENV=production`, a strong `SECRET_KEY`, `TRUSTED_HOSTS`, and `CORS_ORIGINS` (https only for non-local origins).
2. Terminate TLS in front of the API (HSTS is already sent).
3. Run behind a single worker, or move rate-limit, lockout and revocation state to Redis before scaling out.
4. Use PostgreSQL with a least-privilege role; restrict `UPDATE`/`DELETE` on `agent_audit_logs` at the database level.
5. Keep CI green: Bandit, pip-audit and CodeQL run on every push; Dependabot proposes dependency updates weekly.

## Known limitations

- Rate-limit counters, account lockouts and the token revocation list are in-memory (single process).
- Concurrent inserts for the same tenant could fork the audit chain under heavy parallel load on databases without
  serialized writes; verification would report it.
- The GitHub Pages site is a static simulation. GitHub Pages cannot send HTTP security headers, so the page uses a
  `<meta>` CSP (frame-ancestors cannot be set this way).
