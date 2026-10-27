"""FastAPI Enterprise Application Entrypoint.

Configured with OWASP API security controls:
- Security headers middleware (nosniff, DENY frame options)
- Slowapi rate limit exception handling
- CORS lockdown strictly to local Streamlit ports
- Production docs disabling
- Liveness (/healthz) and readiness (/readyz) probes
"""
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from src.api.routes_procurement import router as procurement_router
from src.config import settings
from src.database import SessionLocal, engine, init_db
from src.models import Invoice
from src.schemas import HealthResponse, ReadyResponse
from src.security import limiter
from src.seed_data import seed_database


STARTUP_TIME = datetime.now(timezone.utc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle: ensures database schema and initial seed data."""
    init_db()
    db = SessionLocal()
    try:
        # Seed 50 realistic B2B invoices on first launch if empty
        seed_database(db, force=False)
    finally:
        db.close()
    yield


# OWASP API8: Disable API docs in production mode
docs_url = "/docs" if settings.ENV != "production" else None
redoc_url = "/redoc" if settings.ENV != "production" else None
openapi_url = "/openapi.json" if settings.ENV != "production" else None

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Production-grade Autonomous B2B Supply Chain and Procurement Agent API. "
        "Provides deterministic 3-way matching, SLA late delivery penalties, "
        "local contract RAG clause analysis, and human-in-the-loop review queues."
    ),
    docs_url=docs_url,
    redoc_url=redoc_url,
    openapi_url=openapi_url,
    lifespan=lifespan,
)

# Attach slowapi rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS Lockdown (allow local Streamlit UI and local client)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Inject strict enterprise security headers and measure response time."""
    start_time = time.perf_counter()
    response: Response = await call_next(request)
    process_time = time.perf_counter() - start_time

    # OWASP API8: Prevent MIME-sniffing, clickjacking, and strip server disclosure
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Process-Time-Ms"] = str(round(process_time * 1000, 2))

    # Strip default server information
    if "Server" in response.headers:
        del response.headers["Server"]

    return response


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
)
def readiness_check() -> ReadyResponse:
    """Readiness probe checking database connectivity and seed integrity."""
    db = SessionLocal()
    try:
        # Check database connectivity
        db.execute(text("SELECT 1"))
        invoice_count = db.query(Invoice).count()
        return ReadyResponse(
            status="ready",
            database_connected=True,
            seeded_invoices_count=invoice_count,
            timestamp=datetime.now(timezone.utc),
        )
    except Exception as exc:
        return ReadyResponse(
            status="not_ready",
            database_connected=False,
            seeded_invoices_count=0,
            timestamp=datetime.now(timezone.utc),
        )
    finally:
        db.close()


# Mount main API router
app.include_router(procurement_router, prefix=settings.API_V1_STR, tags=["Procurement & Reconciliation"])
