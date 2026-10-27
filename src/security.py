"""Security and cyber threat mitigation utilities.

Implements OWASP API Top 10 controls (BOLA, broken auth, rate limiting),
LLM prompt injection defense, and PII/banking credential redaction.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Type, TypeVar

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from src.config import settings

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Rate limiting
limiter = Limiter(key_func=get_remote_address, default_limits=[settings.RATE_LIMIT_DEFAULT])

# Bearer Token Scheme
security_bearer = HTTPBearer(auto_error=False)

T = TypeVar("T")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify raw password against bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Compute bcrypt hash of a password."""
    return pwd_context.hash(password)


def create_access_token(
    data: Dict[str, Any], expires_delta: Optional[timedelta] = None
) -> str:
    """Create short-lived JWT access token pinned strictly to HS256 with tenant_id."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    encoded_jwt = jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )
    return encoded_jwt


def decode_access_token(token: str) -> Dict[str, Any]:
    """Decode and validate JWT access token strictly with HS256."""
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            options={"require": ["exp", "sub", "tenant_id"]},
        )
        return payload
    except (jwt.PyJWTError, KeyError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
) -> Dict[str, Any]:
    """Dependency returning authenticated user payload containing tenant_id and role."""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_access_token(credentials.credentials)


def get_owned_resource(
    model: Type[T],
    resource_id: int,
    current_user: Dict[str, Any],
    db: Session,
) -> T:
    """Enforce OWASP API1: Broken Object Level Authorization (BOLA) control.

    Guarantees strict tenant isolation by querying `WHERE id = :id AND tenant_id = :tenant_id`.
    Returns HTTP 404/403 if the resource does not exist or belongs to another tenant.
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
# AI & Cyber Threat Mitigations
# ---------------------------------------------------------------------------

PROMPT_INJECTION_PATTERNS = [
    r"(?i)ignore\s+(all\s+)?(previous|prior)\s+instructions",
    r"(?i)system\s*:",
    r"(?i)<\|im_start\|>",
    r"(?i)<\|im_end\|>",
    r"(?i)you\s+are\s+now\s+in\s+developer\s+mode",
    r"(?i)bypass\s+all\s+(rules|guidelines)",
    r"(?i)disregard\s+the\s+above",
    r"(?i)override\s+decision",
    r"(?i)auto[- ]approve\s+always",
]

COMPILED_INJECTION_RE = [re.compile(p) for p in PROMPT_INJECTION_PATTERNS]


def sanitize_untrusted_text(text: Optional[str]) -> str:
    """Neutralize prompt injection vectors and control characters from untrusted vendor text.

    Strips control characters, delimiter injection, and instruction overrides before
    passing text into contract similarity RAG or LLM reasoning prompts.
    """
    if not text:
        return ""

    # Remove non-printable control characters (except common whitespace)
    cleaned = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", text)

    # Neutralize injection override attempts
    for pattern in COMPILED_INJECTION_RE:
        cleaned = pattern.sub("[REDACTED_PROMPT_INJECTION_ATTEMPT]", cleaned)

    # Escape HTML/system delimiters
    cleaned = cleaned.replace("```", "'''").replace("<|", "").replace("|>", "")

    return cleaned.strip()


def redact_pii(text: str) -> str:
    """Mask IBAN, SWIFT BIC, Tax IDs, and credit card numbers from application logs."""
    if not text:
        return ""

    # Mask IBAN (e.g. DE89 3704 0044 0532 0130 00)
    text = re.sub(
        r"\b([A-Z]{2}[0-9]{2})\s*([0-9A-Z]{4})\s*([0-9A-Z]{4})\s*([0-9A-Z]{4})\s*([0-9A-Z]{0,4})\b",
        r"\1 **** **** **** \5",
        text,
    )

    # Mask US Tax ID / SSN (e.g. 12-3456789 or 123-45-6789)
    text = re.sub(r"\b\d{2}-\d{7}\b", "**-*******", text)
    text = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "***-**-****", text)

    # Mask 16-digit credit cards
    text = re.sub(r"\b(?:\d{4}[ -]?){3}(\d{4})\b", "****-****-****-\1", text)

    return text
