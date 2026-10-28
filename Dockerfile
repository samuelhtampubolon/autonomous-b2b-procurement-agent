# syntax=docker/dockerfile:1
FROM python:3.11-slim AS runtime

# Secure by default: production mode refuses to start without a strong SECRET_KEY,
# disables /docs, and creates no demo accounts unless explicitly configured.
ENV ENV=production \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Unprivileged runtime user (no shell, no home directory contents)
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app src ./src
COPY --chown=app:app run_local.py ./
RUN mkdir -p /app/data && chown app:app /app/data

USER app

EXPOSE 8000 8501

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"

# Secrets (SECRET_KEY, DEMO_* passwords) are injected at runtime, never baked into the image.
CMD ["python", "-m", "uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
