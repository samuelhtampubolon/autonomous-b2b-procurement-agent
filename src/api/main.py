"""FastAPI Enterprise Application Entrypoint.

Configured with OWASP API security controls:
- Strict security headers (CSP, nosniff, DENY framing, COOP/CORP, no-store caching)
- 1 MB request body cap and slowapi rate limiting (API4)
- Host header allow-list and CORS lockdown to the local Streamlit origin (API8)
- Production docs disabling and generic error bodies that never echo input (API8)
- Request correlation IDs and PII-redacting structured logs
- Liveness (/healthz) and readiness (/readyz) probes
"""
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.api.routes_procurement import router as procurement_router
from src.config import settings
from src.database import SessionLocal, init_db
from src.models import Invoice
from src.schemas import HealthResponse, ReadyResponse
from src.security import install_pii_redaction, limiter
from src.seed_data import seed_database

logger = logging.getLogger("procurement.api")

STARTUP_TIME = datetime.now(timezone.utc)
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")

API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
DOCS_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle: logging hardening, schema upgrade, and demo seed data."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    install_pii_redaction()
    init_db()
    if settings.DEMO_MODE:
        db = SessionLocal()
        try:
            seed_database(db, force=False)
        finally:
            db.close()
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Production-grade Autonomous B2B Supply Chain and Procurement Agent API. "
        "Provides deterministic 3-way matching, SLA late delivery penalties, "
        "local contract RAG clause analysis, prompt-injection screening, a tamper-evident "
        "audit ledger, and human-in-the-loop review queues."
    ),
    # OWASP API8: Disable API docs in production mode
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
    lifespan=lifespan,
)

app.state.limiter = limiter


# ---------------------------------------------------------------------------
# Error handlers: consistent JSON, no stack traces, no reflected input
# ---------------------------------------------------------------------------

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": "Rate limit exceeded. Please retry later."},
        headers={"Retry-After": "60"},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return field locations and messages but never echo submitted values (e.g. passwords)."""
    errors: List[Dict[str, Any]] = [
        {"type": err.get("type"), "loc": list(err.get("loc", ())), "msg": err.get("msg")}
        for err in exc.errors()
    ]
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": errors})


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    logger.error("Unhandled error request_id=%s path=%s", request_id, request.url.path, exc_info=exc)
    response = JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error", "request_id": request_id},
    )
    for name, value in _security_headers(request.url.path).items():
        response.headers[name] = value
    return response


# ---------------------------------------------------------------------------
# Middleware (pure ASGI so limits and headers also cover error responses)
# ---------------------------------------------------------------------------

def _security_headers(path: str) -> Dict[str, str]:
    is_docs = path.startswith(DOCS_PATHS)
    return {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        # Legacy XSS auditors are disabled per OWASP guidance; CSP supersedes them.
        "X-XSS-Protection": "0",
        "Content-Security-Policy": DOCS_CSP if is_docs else API_CSP,
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "Cache-Control": "no-cache" if is_docs else "no-store",
    }


class SecurityHeadersMiddleware:
    """Inject security headers, a correlation ID, and timing on every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        start = time.perf_counter()
        extra = _security_headers(scope.get("path", ""))

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() not in {b"server", b"x-powered-by"}
                ]
                existing = {k.lower() for k, _ in headers}
                for name, value in extra.items():
                    if name.lower().encode() not in existing:
                        headers.append((name.encode(), value.encode()))
                headers.append((b"x-request-id", request_id.encode()))
                elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
                headers.append((b"x-process-time-ms", str(elapsed_ms).encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


class BodySizeLimitMiddleware:
    """OWASP API4: Reject request bodies larger than MAX_REQUEST_BODY_BYTES with HTTP 413.

    Checks the declared Content-Length and also counts streamed (chunked) bytes, so a
    client cannot bypass the cap by omitting the header.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def _reject(self, send: Send) -> None:
        body = b'{"detail":"Request body too large"}'
        await send(
            {
                "type": "http.response.start",
                "status": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    await self._reject(send)
                    return
            except ValueError:
                await self._reject(send)
                return

        # Buffer the body (bounded by max_bytes) and replay it to the application.
        chunks: List[bytes] = []
        received = 0
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            received += len(chunk)
            if received > self.max_bytes:
                await self._reject(send)
                return
            chunks.append(chunk)
            more_body = message.get("more_body", False)

        body = b"".join(chunks)
        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)


# Order matters: the last middleware added is the outermost layer.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
    max_age=600,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.TRUSTED_HOSTS)
app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.MAX_REQUEST_BODY_BYTES)
app.add_middleware(SecurityHeadersMiddleware)


# ---------------------------------------------------------------------------
# Health Probes
# ---------------------------------------------------------------------------

@app.get(
    "/healthz",
    response_model=HealthResponse,
    tags=["Health Probes"],
    summary="Liveness check",
)
def liveness_check() -> HealthResponse:
    """Liveness probe verifying server process health and uptime."""
    return HealthResponse(
        status="ok",
        app=settings.APP_NAME,
        version=settings.APP_VERSION,
        environment=settings.ENV,
        timestamp=datetime.now(timezone.utc),
    )


@app.get(
    "/readyz",
    response_model=ReadyResponse,
    tags=["Health Probes"],
    summary="Readiness check verifying database and seed data",
    responses={503: {"description": "Database unavailable"}},
)
def readiness_check():
    """Readiness probe checking database connectivity and seed integrity (503 when not ready)."""
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        invoice_count = db.query(Invoice).count()
        return ReadyResponse(
            status="ready",
            database_connected=True,
            seeded_invoices_count=invoice_count,
            timestamp=datetime.now(timezone.utc),
        )
    except Exception:
        logger.exception("Readiness probe failed")
        body = ReadyResponse(
            status="not_ready",
            database_connected=False,
            seeded_invoices_count=0,
            timestamp=datetime.now(timezone.utc),
        )
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=body.model_dump(mode="json"))
    finally:
        db.close()


# Mount main API router
app.include_router(procurement_router, prefix=settings.API_V1_STR, tags=["Procurement & Reconciliation"])
