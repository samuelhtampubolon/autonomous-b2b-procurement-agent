"""Security and cyber threat mitigation utilities.

Implements OWASP API Top 10 (2023) controls:
- API1 BOLA: `get_owned_resource` tenant-scoped lookups.
- API2 Broken Authentication: bcrypt hashing, constant-time login, per-user lockout,
  HS256-pinned JWTs with issuer/audience/jti claims and server-side revocation.
- API4 Unrestricted Resource Consumption: slowapi rate limiting.
- API5 Broken Function Level Authorization: `require_roles` RBAC dependency.

Plus LLM prompt-injection neutralization and PII / credential redaction for logs.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Type, TypeVar

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from src.config import settings

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Roles (RBAC)
# ---------------------------------------------------------------------------

ROLE_SPECIALIST = "procurement_specialist"
ROLE_CPO = "cpo"
ROLE_AUDITOR = "auditor"
ALL_ROLES = frozenset({ROLE_SPECIALIST, ROLE_CPO, ROLE_AUDITOR})

# Roles allowed to mutate procurement state (submit invoices, record HITL decisions).
OPERATOR_ROLES = (ROLE_SPECIALIST, ROLE_CPO)


# ---------------------------------------------------------------------------
# Password hashing (bcrypt, cost 12)
# ---------------------------------------------------------------------------

BCRYPT_ROUNDS = 12
BCRYPT_MAX_BYTES = 72

_dummy_hash_lock = threading.Lock()
_dummy_hash: Optional[bytes] = None


def _get_dummy_hash() -> bytes:
    """Hash used to burn equal CPU time when a username does not exist."""
    global _dummy_hash
    with _dummy_hash_lock:
        if _dummy_hash is None:
            _dummy_hash = bcrypt.hashpw(b"timing-equalizer", bcrypt.gensalt(BCRYPT_ROUNDS))
        return _dummy_hash


def get_password_hash(password: str) -> str:
    """Compute a bcrypt hash of a password (rejects inputs bcrypt would silently truncate)."""
    raw = password.encode("utf-8")
    if len(raw) > BCRYPT_MAX_BYTES:
        raise ValueError(f"Password exceeds bcrypt's {BCRYPT_MAX_BYTES}-byte limit")
    return bcrypt.hashpw(raw, bcrypt.gensalt(BCRYPT_ROUNDS)).decode("ascii")


def verify_password(plain_password: str, hashed_password: Optional[str]) -> bool:
    """Verify a password in constant time relative to whether the account exists.

    When `hashed_password` is None (unknown user) a dummy bcrypt check still runs so the
    response time does not reveal which usernames are registered.
    """
    raw = plain_password.encode("utf-8")
    if hashed_password is None or len(raw) > BCRYPT_MAX_BYTES:
        bcrypt.checkpw(raw[:BCRYPT_MAX_BYTES], _get_dummy_hash())
        return False
    try:
        return bcrypt.checkpw(raw, hashed_password.encode("ascii"))
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Brute-force protection: per-username lockout
# ---------------------------------------------------------------------------


class LoginAttemptTracker:
    """Sliding-window failed-login counter keyed by normalized username.

    Complements the per-IP slowapi limit so distributed credential stuffing against one
    account is also throttled. In-memory by design (single API process); swap for Redis
    when running multiple workers.
    """

    def __init__(self, max_attempts: int, lockout_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self._failures: Dict[str, List[float]] = {}
        self._locked_until: Dict[str, float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(username: str) -> str:
        return username.strip().lower()

    def is_locked(self, username: str) -> bool:
        key = self._key(username)
        with self._lock:
            until = self._locked_until.get(key)
            if until is None:
                return False
            if until <= time.monotonic():
                self._locked_until.pop(key, None)
                self._failures.pop(key, None)
                return False
            return True

    def register_failure(self, username: str) -> None:
        key = self._key(username)
        now = time.monotonic()
        with self._lock:
            window_start = now - self.lockout_seconds
            attempts = [t for t in self._failures.get(key, []) if t >= window_start]
            attempts.append(now)
            self._failures[key] = attempts
            if len(attempts) >= self.max_attempts:
                self._locked_until[key] = now + self.lockout_seconds

    def reset(self, username: Optional[str] = None) -> None:
        with self._lock:
            if username is None:
                self._failures.clear()
                self._locked_until.clear()
            else:
                key = self._key(username)
                self._failures.pop(key, None)
                self._locked_until.pop(key, None)


login_tracker = LoginAttemptTracker(
    max_attempts=settings.LOGIN_MAX_FAILED_ATTEMPTS,
    lockout_seconds=settings.LOGIN_LOCKOUT_MINUTES * 60,
)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.RATE_LIMIT_DEFAULT],
    enabled=settings.RATE_LIMIT_ENABLED,
    headers_enabled=False,
)


# ---------------------------------------------------------------------------
# JWT access tokens
# ---------------------------------------------------------------------------

security_bearer = HTTPBearer(auto_error=False)

REQUIRED_CLAIMS = ["exp", "iat", "nbf", "iss", "aud", "sub", "jti", "tenant_id", "role", "typ"]


class TokenRevocationList:
    """In-memory denylist of revoked token IDs (jti), pruned once tokens expire."""

    def __init__(self) -> None:
        self._revoked: Dict[str, float] = {}
        self._lock = threading.Lock()

    def revoke(self, jti: str, expires_at: float) -> None:
        with self._lock:
            self._prune()
            self._revoked[jti] = expires_at

    def is_revoked(self, jti: str) -> bool:
        with self._lock:
            return jti in self._revoked

    def _prune(self) -> None:
        now = time.time()
        for key in [k for k, exp in self._revoked.items() if exp < now]:
            del self._revoked[key]


revoked_tokens = TokenRevocationList()


def _credentials_exception(detail: str = "Invalid or expired authentication credentials") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def create_access_token(
    data: Dict[str, Any], expires_delta: Optional[timedelta] = None
) -> str:
    """Create a short-lived JWT pinned to HS256 with tenant, role, issuer, audience and jti."""
    now = datetime.now(timezone.utc)
    lifetime = expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode = dict(data)
    to_encode.update(
        {
            "iat": now,
            "nbf": now,
            "exp": now + lifetime,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "jti": uuid.uuid4().hex,
            "typ": "access",
        }
    )
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    """Decode and validate a JWT strictly: HS256 only, all claims required, not revoked."""
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != settings.ALGORITHM:
            raise _credentials_exception()
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
            leeway=settings.JWT_LEEWAY_SECONDS,
            options={"require": REQUIRED_CLAIMS},
        )
    except jwt.PyJWTError:
        raise _credentials_exception()

    if payload.get("typ") != "access":
        raise _credentials_exception()
    for claim in ("sub", "tenant_id", "role", "jti"):
        value = payload.get(claim)
        if not isinstance(value, str) or not value or len(value) > 128:
            raise _credentials_exception()
    if payload["role"] not in ALL_ROLES:
        raise _credentials_exception()
    if revoked_tokens.is_revoked(payload["jti"]):
        raise _credentials_exception("Token has been revoked")
    return payload


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
) -> Dict[str, Any]:
    """Dependency returning the authenticated principal (sub, tenant_id, role, jti, exp)."""
    if not credentials or not credentials.credentials:
        raise _credentials_exception("Authentication token required")
    if len(credentials.credentials) > 4096:
        raise _credentials_exception()
    return decode_access_token(credentials.credentials)


def require_roles(*allowed_roles: str) -> Callable[..., Dict[str, Any]]:
    """OWASP API5: Build a dependency that only admits principals holding one of the roles."""
    allowed = frozenset(allowed_roles)

    def _dependency(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
        if current_user.get("role") not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role for this operation",
            )
        return current_user

    return _dependency


def get_owned_resource(
    model: Type[T],
    resource_id: int,
    current_user: Dict[str, Any],
    db: Session,
) -> T:
    """Enforce OWASP API1: Broken Object Level Authorization (BOLA) control.

    Guarantees strict tenant isolation by querying `WHERE id = :id AND tenant_id = :tenant_id`.
    Returns HTTP 404 if the resource does not exist *or* belongs to another tenant, so an
    attacker cannot distinguish the two cases (no cross-tenant enumeration).
    """
    tenant_id = current_user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Missing tenant context",
        )

    resource = (
        db.query(model)
        .filter(model.id == resource_id, model.tenant_id == tenant_id)  # type: ignore[attr-defined]
        .first()
    )

    if resource is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{model.__name__} not found or inaccessible for tenant",
        )

    return resource


# ---------------------------------------------------------------------------
# AI & Cyber Threat Mitigations: prompt injection firewall
# ---------------------------------------------------------------------------

INJECTION_REDACTION = "[REDACTED_PROMPT_INJECTION_ATTEMPT]"
MARKUP_REDACTION = "[REDACTED_MARKUP]"

PROMPT_INJECTION_RULES: Tuple[Tuple[str, str], ...] = (
    ("instruction_override", r"(?i)\b(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier|preceding)\s+(instructions?|rules|prompts?|context|directions)"),
    ("instruction_override", r"(?i)\bdisregard\s+the\s+above"),
    ("role_spoofing", r"(?i)\b(system|developer|assistant)\s*:"),
    ("chat_template_tokens", r"(?i)<\|\s*(im_start|im_end|system|user|assistant|endoftext)\s*\|>"),
    ("chat_template_tokens", r"(?i)\[/?INST\]|<</?SYS>>|###\s*(system|instruction)s?\b"),
    ("persona_override", r"(?i)\byou\s+are\s+now\s+(in\s+)?(developer\s+mode|dan\b|jailbroken|unrestricted|an?\s+admin)"),
    ("persona_override", r"(?i)\b(act|pretend|behave)\s+as\s+(an?\s+|the\s+)?(admin(istrator)?|system|approver|cpo|root)\b"),
    ("guardrail_bypass", r"(?i)\bbypass\s+(all\s+|the\s+|any\s+)?(rules|guidelines|checks|controls|validation|approvals?|guardrails)"),
    ("approval_override", r"(?i)\bauto[- ]?approve\b(\s+always)?"),
    ("approval_override", r"(?i)\boverride\s+(the\s+)?(decision|policy|approval|routing|threshold)"),
    ("approval_override", r"(?i)\b(skip|avoid)\s+(the\s+)?(human|hitl|manual)\s+(review|approval)"),
    ("approval_override", r"(?i)\bmark\s+(this\s+invoice\s+|it\s+)?as\s+approved"),
    ("new_instructions", r"(?i)\b(new|updated|revised)\s+instructions?\s*:"),
    ("secret_exfiltration", r"(?i)\b(reveal|print|show|output|leak|send)\s+(me\s+)?(the\s+|your\s+)?(system\s+prompt|secret|api[\s_-]*key|password|credentials?|token)"),
)

MARKUP_RULES: Tuple[Tuple[str, str], ...] = (
    ("html_markup", r"(?i)<\s*/?\s*(script|iframe|object|embed|svg|img|style|link|meta|form)\b[^>]*>?"),
    ("script_uri", r"(?i)\b(javascript|vbscript)\s*:|\bdata\s*:\s*text/html"),
    ("event_handler", r"(?i)\bon[a-z]{3,}\s*="),
)

COMPILED_INJECTION_RULES = [(name, re.compile(p)) for name, p in PROMPT_INJECTION_RULES]
COMPILED_MARKUP_RULES = [(name, re.compile(p)) for name, p in MARKUP_RULES]

# Back-compat export used by earlier revisions.
PROMPT_INJECTION_PATTERNS = [p for _, p in PROMPT_INJECTION_RULES]
COMPILED_INJECTION_RE = [rx for _, rx in COMPILED_INJECTION_RULES]

# C0/C1 control characters except tab/newline/carriage return.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]")
# Zero-width characters and bidi overrides ("Trojan Source", CVE-2021-42574) used to
# hide instructions from human reviewers.
_INVISIBLE_CHARS_RE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")

MAX_UNTRUSTED_TEXT_CHARS = 4000


def _normalize_untrusted(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text[:MAX_UNTRUSTED_TEXT_CHARS])
    normalized = _CONTROL_CHARS_RE.sub("", normalized)
    return _INVISIBLE_CHARS_RE.sub("", normalized)


def assess_untrusted_text(text: Optional[str]) -> Tuple[str, List[str]]:
    """Sanitize untrusted vendor text and report which threat rules fired.

    Returns `(sanitized_text, risk_flags)`. Any non-empty `risk_flags` means the text
    contained instruction-override, role-spoofing, or markup payloads; callers must
    route such invoices to human review rather than trusting automated decisions.
    """
    if not text:
        return "", []

    cleaned = _normalize_untrusted(text)
    flags: List[str] = []
    if len(cleaned) != len(unicodedata.normalize("NFKC", text[:MAX_UNTRUSTED_TEXT_CHARS])):
        flags.append("hidden_characters")

    for name, pattern in COMPILED_INJECTION_RULES:
        if pattern.search(cleaned):
            flags.append(name)
            cleaned = pattern.sub(INJECTION_REDACTION, cleaned)

    for name, pattern in COMPILED_MARKUP_RULES:
        if pattern.search(cleaned):
            flags.append(name)
            cleaned = pattern.sub(MARKUP_REDACTION, cleaned)

    # Neutralize remaining delimiter sequences used to break out of prompt templates.
    cleaned = cleaned.replace("```", "'''").replace("<|", "").replace("|>", "")

    return cleaned.strip(), sorted(set(flags))


def sanitize_untrusted_text(text: Optional[str]) -> str:
    """Neutralize prompt injection vectors and control characters from untrusted vendor text.

    Strips control and invisible characters, delimiter injection, markup payloads, and
    instruction overrides before text reaches the contract RAG or any LLM prompt.
    """
    return assess_untrusted_text(text)[0]


def detect_prompt_injection(text: Optional[str]) -> List[str]:
    """Return the names of the threat rules triggered by `text` (empty when clean)."""
    return assess_untrusted_text(text)[1]


# ---------------------------------------------------------------------------
# PII & secret redaction
# ---------------------------------------------------------------------------

_IBAN_RE = re.compile(r"\b([A-Z]{2}\d{2})((?:[ ]?[A-Z0-9]{4}){2,7})((?:[ ]?[A-Z0-9]{1,4})?)\b")
_SWIFT_RE = re.compile(r"(?i)\b(SWIFT|BIC)(\s*(?:code)?\s*[:#]?\s*)([A-Z]{4})[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b")
_EIN_RE = re.compile(r"\b\d{2}-\d{7}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CARD_RE = re.compile(r"\b(?:\d{4}[ -]?){3}(\d{4})\b")
_EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_JWT_RE = re.compile(r"\beyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]{5,}\b")
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{16,}")


def _mask_iban(match: "re.Match[str]") -> str:
    compact = (match.group(2) + match.group(3)).replace(" ", "")
    return f"{match.group(1)} **** **** **** {compact[-4:]}"


def redact_pii(text: str) -> str:
    """Mask IBAN, SWIFT/BIC, tax IDs, card numbers, emails and bearer tokens in log strings."""
    if not text:
        return ""

    text = _JWT_RE.sub("[REDACTED_JWT]", text)
    text = _BEARER_RE.sub(r"\1 [REDACTED_TOKEN]", text)
    text = _IBAN_RE.sub(_mask_iban, text)
    text = _SWIFT_RE.sub(r"\1\2\3****", text)
    text = _EIN_RE.sub("**-*******", text)
    text = _SSN_RE.sub("***-**-****", text)
    text = _CARD_RE.sub(r"****-****-****-\1", text)
    text = _EMAIL_RE.sub(r"\1***@\2", text)
    return text


class PIIRedactingFilter(logging.Filter):
    """Structured logging filter that scrubs PII and credentials from every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - malformed record args
            return True
        record.msg = redact_pii(message)
        record.args = ()
        return True


def install_pii_redaction(logger_names: Iterable[str] = ("", "uvicorn", "uvicorn.access", "uvicorn.error", "procurement")) -> None:
    """Attach the redaction filter to the root and server loggers (idempotent)."""
    for name in logger_names:
        target = logging.getLogger(name)
        if not any(isinstance(f, PIIRedactingFilter) for f in target.filters):
            target.addFilter(PIIRedactingFilter())
        for handler in target.handlers:
            if not any(isinstance(f, PIIRedactingFilter) for f in handler.filters):
                handler.addFilter(PIIRedactingFilter())
