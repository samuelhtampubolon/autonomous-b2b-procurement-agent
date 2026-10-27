"""Tamper-evident, append-only audit ledger.

Every agent or human action is recorded as an `AgentAuditLog` row chained to the
previous row of the same tenant:

    entry_hash = SHA-256( prev_hash || canonical_json(entry_fields) )

Altering, re-ordering, or deleting any historic entry changes its hash and breaks
every link after it, which `verify_chain` reports with the first broken entry id.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import event
from sqlalchemy.orm import Session

from src.models import AgentAuditLog
from src.security import redact_pii

logger = logging.getLogger("procurement.audit")

GENESIS_HASH = "0" * 64


class AuditLedgerTamperError(RuntimeError):
    """Raised when application code attempts to rewrite a sealed audit entry."""


def _canonical_timestamp(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat(timespec="microseconds")


def _to_naive_utc(value: Optional[datetime]) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def compute_entry_hash(
    *,
    prev_hash: str,
    tenant_id: str,
    invoice_id: Optional[int],
    action_taken: str,
    details: str,
    tokens_used: int,
    compute_cost_usd: float,
    labor_saved_usd: float,
    actor: Optional[str],
    created_at: datetime,
) -> str:
    """Deterministic SHA-256 over the previous hash and a canonical JSON encoding."""
    payload: Dict[str, Any] = {
        "tenant_id": tenant_id,
        "invoice_id": invoice_id,
        "action_taken": action_taken,
        "details": details,
        "tokens_used": int(tokens_used),
        "compute_cost_usd": f"{float(compute_cost_usd):.8f}",
        "labor_saved_usd": f"{float(labor_saved_usd):.8f}",
        "actor": actor or "",
        "created_at": _canonical_timestamp(created_at),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()


def _hash_for(entry: AgentAuditLog, prev_hash: str) -> str:
    return compute_entry_hash(
        prev_hash=prev_hash,
        tenant_id=entry.tenant_id,
        invoice_id=entry.invoice_id,
        action_taken=entry.action_taken,
        details=entry.details,
        tokens_used=entry.tokens_used or 0,
        compute_cost_usd=entry.compute_cost_usd or 0.0,
        labor_saved_usd=entry.labor_saved_usd or 0.0,
        actor=entry.actor,
        created_at=entry.created_at,
    )


def _latest_hash(db: Session, tenant_id: str) -> str:
    db.flush()
    row = (
        db.query(AgentAuditLog.entry_hash)
        .filter(AgentAuditLog.tenant_id == tenant_id, AgentAuditLog.entry_hash.isnot(None))
        .order_by(AgentAuditLog.id.desc())
        .first()
    )
    return row[0] if row else GENESIS_HASH


def append_audit_log(
    db: Session,
    *,
    tenant_id: str,
    action_taken: str,
    details: str,
    invoice_id: Optional[int] = None,
    tokens_used: int = 0,
    compute_cost_usd: float = 0.0,
    labor_saved_usd: float = 0.0,
    actor: Optional[str] = None,
    created_at: Optional[datetime] = None,
) -> AgentAuditLog:
    """Append a sealed entry to the tenant's ledger (caller commits the transaction)."""
    entry = AgentAuditLog(
        tenant_id=tenant_id,
        invoice_id=invoice_id,
        action_taken=action_taken[:64],
        details=redact_pii(details),
        tokens_used=int(tokens_used),
        compute_cost_usd=float(compute_cost_usd),
        labor_saved_usd=float(labor_saved_usd),
        actor=actor,
        created_at=_to_naive_utc(created_at),
    )
    entry.prev_hash = _latest_hash(db, tenant_id)
    entry.entry_hash = _hash_for(entry, entry.prev_hash)
    db.add(entry)
    db.flush()
    return entry


def verify_chain(db: Session, tenant_id: str) -> Dict[str, Any]:
    """Recompute the tenant's hash chain and report the first inconsistency, if any."""
    entries = (
        db.query(AgentAuditLog)
        .filter(AgentAuditLog.tenant_id == tenant_id)
        .order_by(AgentAuditLog.id.asc())
        .all()
    )

    expected_prev = GENESIS_HASH
    for index, entry in enumerate(entries):
        if entry.prev_hash != expected_prev:
            return {
                "valid": False,
                "total_entries": len(entries),
                "verified_entries": index,
                "first_invalid_entry_id": entry.id,
                "head_hash": expected_prev,
                "reason": "Broken link: prev_hash does not match the preceding entry (entry removed or reordered)",
            }
        recomputed = _hash_for(entry, expected_prev)
        if entry.entry_hash != recomputed:
            return {
                "valid": False,
                "total_entries": len(entries),
                "verified_entries": index,
                "first_invalid_entry_id": entry.id,
                "head_hash": expected_prev,
                "reason": "Hash mismatch: entry content was modified after it was sealed",
            }
        expected_prev = entry.entry_hash

    return {
        "valid": True,
        "total_entries": len(entries),
        "verified_entries": len(entries),
        "first_invalid_entry_id": None,
        "head_hash": expected_prev,
        "reason": "All entries verified: hash chain intact",
    }


def backfill_missing_hashes(db: Session) -> int:
    """Seal legacy entries created before hash chaining existed (one-time upgrade)."""
    pending = db.query(AgentAuditLog.tenant_id).filter(AgentAuditLog.entry_hash.is_(None)).distinct().all()
    sealed = 0
    for (tenant_id,) in pending:
        prev = GENESIS_HASH
        for entry in (
            db.query(AgentAuditLog)
            .filter(AgentAuditLog.tenant_id == tenant_id)
            .order_by(AgentAuditLog.id.asc())
            .all()
        ):
            if entry.entry_hash is None:
                entry.prev_hash = prev
                entry.entry_hash = _hash_for(entry, prev)
                sealed += 1
            prev = entry.entry_hash
    if sealed:
        db.commit()
        logger.info("Audit ledger upgrade: sealed %d legacy entries", sealed)
    return sealed


@event.listens_for(AgentAuditLog, "before_update")
def _block_sealed_updates(mapper, connection, target: AgentAuditLog) -> None:
    """Application-level append-only guard (the DB trigger is the second line of defence)."""
    from sqlalchemy import inspect as sa_inspect

    state = sa_inspect(target)
    changed = {attr.key for attr in state.attrs if attr.history.has_changes()}
    # invoice_id may be nulled by ON DELETE SET NULL; hashes may be set once during backfill.
    allowed = {"invoice_id"}
    if state.attrs.entry_hash.history.deleted == [None] or state.attrs.entry_hash.history.deleted == ():
        allowed |= {"prev_hash", "entry_hash"}
    if changed - allowed:
        raise AuditLedgerTamperError("Audit log entries are append-only and cannot be modified")
