"""Unified local runner with preflight socket checks and graceful shutdown.

Manages both FastAPI backend and Streamlit dashboard with cross-OS compatibility.
Supports '--check' mode for automated preflight validation.
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
# subprocess only launches our own uvicorn/streamlit with a fixed argv (no shell).
import subprocess  # nosec B404
import sys
import time
import webbrowser
from pathlib import Path
from typing import List, Optional

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (OSError, ValueError):  # pragma: no cover - non-reconfigurable stream
        sys.stdout.write("")

PROJECT_ROOT: Path = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.database import SessionLocal, init_db  # noqa: E402
from src.seed_data import seed_database  # noqa: E402
from src.services.audit_ledger import verify_chain  # noqa: E402


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Check whether a given port is occupied on host."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def find_free_port(start_port: int, max_attempts: int = 20, host: str = "127.0.0.1") -> int:
    """Find next open socket port starting from start_port."""
    port = start_port
    while port < start_port + max_attempts:
        if not is_port_in_use(port, host):
            return port
        port += 1
    raise RuntimeError(f"Could not find an available port in range {start_port}-{start_port + max_attempts}")


class PreflightError(RuntimeError):
    """Raised when a preflight verification step fails."""


def _require(condition: bool, message: str) -> None:
    # Explicit check instead of `assert`, which is stripped under `python -O`.
    if not condition:
        raise PreflightError(message)


def run_preflight_check() -> bool:
    """Verify database initialization, 50 invoices seed, and internal health."""
    print("=" * 70)
    print("Running Autonomous B2B Procurement Agent Preflight Verification...")
    print("=" * 70)

    try:
        init_db()
        print("[OK] Database schema verified / initialized.")

        db = SessionLocal()
        try:
            count = seed_database(db, force=False)
            print(f"[OK] Seed verification: {count} enterprise invoices available in database.")
            _require(count >= 50, f"Expected at least 50 invoices, found {count}")
        finally:
            db.close()

        # Import FastAPI app and test healthz and readyz with TestClient
        from fastapi.testclient import TestClient
        from src.api.main import app

        client = TestClient(app)
        h_res = client.get("/healthz")
        _require(h_res.status_code == 200, f"/healthz failed with {h_res.status_code}")
        _require(h_res.json()["status"] == "ok", "/healthz did not report ok")
        print("[OK] /healthz probe passed: HTTP 200 OK.")

        r_res = client.get("/readyz")
        _require(r_res.status_code == 200, f"/readyz failed with {r_res.status_code}")
        _require(r_res.json()["status"] == "ready", "/readyz did not report ready")
        print("[OK] /readyz probe passed: Database connected, seeded invoices verified.")

        for header in ("Content-Security-Policy", "X-Content-Type-Options", "X-Frame-Options"):
            _require(header in h_res.headers, f"Missing security header {header}")
        print("[OK] Security headers present (CSP, nosniff, DENY framing).")

        db = SessionLocal()
        try:
            for tenant in ("tenant_alpha", "tenant_beta"):
                result = verify_chain(db, tenant)
                _require(result["valid"], f"Audit ledger for {tenant} failed: {result['reason']}")
                print(f"[OK] Audit hash chain intact for {tenant}: {result['total_entries']} sealed entries.")
        finally:
            db.close()

        from src.config import settings
        from src.demo_credentials import CREDENTIALS_FILE

        if settings.DEMO_MODE and not settings.is_production:
            print(f"[OK] Demo persona passwords (per install, git-ignored): {CREDENTIALS_FILE}")

        print("=" * 70)
        print("ALL PREFLIGHT CHECKS PASSED DETERMINISTICALLY (100% HEALTHY)!")
        print("=" * 70)
        return True
    except Exception as exc:
        print(f"[FAIL] Preflight check FAILED: {exc}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Autonomous Procurement Agent Unified Launcher")
    parser.add_argument("--check", action="store_true", help="Run deterministic health and seed verification")
    parser.add_argument("--api-only", action="store_true", help="Launch only FastAPI backend")
    parser.add_argument("--ui-only", action="store_true", help="Launch only Streamlit dashboard")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the Control Tower in a browser")
    args = parser.parse_args()

    if args.check:
        success = run_preflight_check()
        sys.exit(0 if success else 1)

    # Preflight check before starting servers
    if not run_preflight_check():
        print("Aborting startup due to preflight check failure.")
        sys.exit(1)

    # Determine free ports
    api_port = find_free_port(8000)
    ui_port = find_free_port(8501)

    print(f"\n[INFO] Binding FastAPI to http://127.0.0.1:{api_port}")
    print(f"[INFO] Binding Streamlit to http://127.0.0.1:{ui_port}\n")

    env = os.environ.copy()
    env["PORT"] = str(api_port)
    env["UI_PORT"] = str(ui_port)
    env["API_URL"] = f"http://127.0.0.1:{api_port}"

    processes: List[subprocess.Popen] = []

    def shutdown(signum=None, frame=None):
        print("\n[INFO] Graceful shutdown triggered. Terminating child processes...")
        for p in processes:
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                p.kill()
        print("[INFO] All servers shut down cleanly.")
        sys.exit(0)

    # Register signals for graceful termination
    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    try:
        # Spawn FastAPI Server
        if not args.ui_only:
            api_cmd = [
                sys.executable,
                "-m",
                "uvicorn",
                "src.api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
                "--no-server-header",
            ]
            print(f"[START] Spawning API: {' '.join(api_cmd)}")
            api_proc = subprocess.Popen(api_cmd, cwd=str(PROJECT_ROOT), env=env)  # nosec B603
            processes.append(api_proc)

        # Spawn Streamlit Server
        if not args.api_only:
            time.sleep(1.5)  # Allow API socket to bind
            ui_cmd = [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(PROJECT_ROOT / "src" / "ui" / "dashboard.py"),
                "--server.port",
                str(ui_port),
                "--server.address",
                "127.0.0.1",
                "--server.headless",
                "true",
                "--browser.gatherUsageStats",
                "false",
                "--server.enableXsrfProtection",
                "true",
            ]
            print(f"[START] Spawning Streamlit: {' '.join(ui_cmd)}")
            ui_proc = subprocess.Popen(ui_cmd, cwd=str(PROJECT_ROOT), env=env)  # nosec B603
            processes.append(ui_proc)

        print("\n" + "=" * 70)
        print("SERVERS LIVE AND OPERATIONAL!")
        print(f"API Endpoints & Swagger: http://127.0.0.1:{api_port}/docs")
        print(f"Streamlit Control Tower: http://127.0.0.1:{ui_port}")
        print("Press Ctrl+C to terminate.")
        print("=" * 70 + "\n")

        if not args.api_only and not args.no_browser:
            time.sleep(2.0)
            webbrowser.open(f"http://127.0.0.1:{ui_port}")

        # Keep parent alive and monitor child processes
        while True:
            for p in processes:
                ret = p.poll()
                if ret is not None:
                    print(f"[WARN] Process {p.args} exited with code {ret}")
                    shutdown()
            time.sleep(1)

    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
