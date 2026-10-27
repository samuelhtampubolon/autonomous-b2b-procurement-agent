"""Per-installation demo persona credentials.

No demo password is stored in the repository. Each password is resolved in this order:

1. An explicit environment variable (`DEMO_ANALYST_PASSWORD`, ...), required in production.
2. Outside production, a random password generated once with `secrets.token_urlsafe` and
   kept in `data/.demo_credentials.json` (owner read/write only, git-ignored), so the API,
   the launcher and the Streamlit control tower on the same machine share it.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Dict, List, NamedTuple

from src.config import DATA_DIR, settings

CREDENTIALS_FILE: Path = DATA_DIR / ".demo_credentials.json"


class DemoUser(NamedTuple):
    tenant_id: str
    username: str
    env_var: str
    role: str
    label: str


DEMO_USERS: List[DemoUser] = [
    DemoUser("tenant_alpha", "analyst_alpha", "DEMO_ANALYST_PASSWORD", "procurement_specialist", "Analyst (Tenant Alpha)"),
    DemoUser("tenant_alpha", "cpo_alpha", "DEMO_CPO_PASSWORD", "cpo", "CPO (Tenant Alpha)"),
    DemoUser("tenant_alpha", "auditor_alpha", "DEMO_AUDITOR_PASSWORD", "auditor", "Auditor (Tenant Alpha, read-only)"),
    DemoUser("tenant_beta", "hacker_beta", "DEMO_BETA_PASSWORD", "procurement_specialist", "Attacker (Tenant Beta)"),
]


class DemoCredentialsError(RuntimeError):
    """Raised when demo passwords cannot be resolved safely."""


def _load_file() -> Dict[str, str]:
    if not CREDENTIALS_FILE.exists():
        return {}
    try:
        data = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str) and v}


def _write_file(data: Dict[str, str]) -> None:
    # Create with 0600 from the start so the passwords are never world-readable.
    fd = os.open(str(CREDENTIALS_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    try:
        CREDENTIALS_FILE.chmod(0o600)
    except OSError:  # pragma: no cover - Windows ACLs do not map to chmod
        pass


def resolve_demo_passwords() -> Dict[str, str]:
    """Return {username: password} for every demo persona."""
    env_values = {u.username: os.getenv(u.env_var, "").strip() for u in DEMO_USERS}

    if settings.is_production:
        missing = [u.env_var for u in DEMO_USERS if not env_values[u.username]]
        if missing:
            raise DemoCredentialsError(
                "Refusing to create demo accounts in production without explicit passwords; set "
                + ", ".join(missing)
            )
        return env_values

    stored = _load_file()
    changed = False
    for user in DEMO_USERS:
        if not stored.get(user.username):
            stored[user.username] = secrets.token_urlsafe(18)
            changed = True
    if changed:
        _write_file(stored)
    return {u.username: env_values[u.username] or stored[u.username] for u in DEMO_USERS}
