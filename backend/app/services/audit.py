"""Append-only, tamper-evident lifecycle audit trail (Task 14).

Revenue Autopilot records merchant-visible lifecycle events for detection,
AI hypothesis creation, experiment planning, policy authorization, Razorpay
execution, runtime start, statistical completion, and rollback.

Design:
- Append-only at the application layer (no updates/deletes in this module).
- ``db.add`` + ``flush`` only. The caller owns the transaction; this module
  never commits.
- Per-merchant SHA-256 hash chain over a canonical JSON representation.
- Compact, sanitized payloads: no secrets, no raw OpenAI prompts, no raw
  Razorpay response bodies, no hidden causal fields, no chain-of-thought.

The P0 hash chain is intended for demo/auditability. It does not claim
distributed-ledger guarantees. Concurrent multi-writer hardening (locks,
serializable isolation, conflict retries) is out of scope for Task 14.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AuditEvent

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AuditError(ValueError):
    """Raised when audit inputs are invalid or the local chain cannot be extended."""


# ---------------------------------------------------------------------------
# Stable event types
# ---------------------------------------------------------------------------

OPPORTUNITY_DETECTED = "OPPORTUNITY_DETECTED"
AI_DIAGNOSIS_CREATED = "AI_DIAGNOSIS_CREATED"
HYPOTHESIS_PROPOSED = "HYPOTHESIS_PROPOSED"
EXPERIMENT_PLANNED = "EXPERIMENT_PLANNED"
POLICY_REJECTED = "POLICY_REJECTED"
POLICY_APPROVED = "POLICY_APPROVED"
RAZORPAY_RESOURCE_CREATED = "RAZORPAY_RESOURCE_CREATED"
EXPERIMENT_STARTED = "EXPERIMENT_STARTED"
EXPERIMENT_COMPLETED = "EXPERIMENT_COMPLETED"
TREATMENT_PROMOTED = "TREATMENT_PROMOTED"
EXPERIMENT_ROLLED_BACK = "EXPERIMENT_ROLLED_BACK"
RAZORPAY_RESOURCE_CANCELLED = "RAZORPAY_RESOURCE_CANCELLED"

AUDIT_EVENT_TYPES = frozenset(
    {
        OPPORTUNITY_DETECTED,
        AI_DIAGNOSIS_CREATED,
        HYPOTHESIS_PROPOSED,
        EXPERIMENT_PLANNED,
        POLICY_REJECTED,
        POLICY_APPROVED,
        RAZORPAY_RESOURCE_CREATED,
        EXPERIMENT_STARTED,
        EXPERIMENT_COMPLETED,
        TREATMENT_PROMOTED,
        EXPERIMENT_ROLLED_BACK,
        RAZORPAY_RESOURCE_CANCELLED,
    }
)

# ---------------------------------------------------------------------------
# Stable actors
# ---------------------------------------------------------------------------

ACTOR_DETECTOR = "detector"
ACTOR_AI = "ai"
ACTOR_PLANNER = "planner"
ACTOR_POLICY = "policy"
ACTOR_RUNTIME = "runtime"
ACTOR_STATISTICS = "statistics"
ACTOR_RAZORPAY_EXECUTOR = "razorpay_executor"
ACTOR_SYSTEM = "system"

AUDIT_ACTORS = frozenset(
    {
        ACTOR_DETECTOR,
        ACTOR_AI,
        ACTOR_PLANNER,
        ACTOR_POLICY,
        ACTOR_RUNTIME,
        ACTOR_STATISTICS,
        ACTOR_RAZORPAY_EXECUTOR,
        ACTOR_SYSTEM,
    }
)

ENTITY_OPPORTUNITY = "opportunity"
ENTITY_HYPOTHESIS = "hypothesis"
ENTITY_EXPERIMENT = "experiment"

MIN_HISTORY_LIMIT = 1
MAX_HISTORY_LIMIT = 1000
DEFAULT_HISTORY_LIMIT = 100

# Keys that must never be persisted on an audit payload.
_SECRET_EXACT = frozenset(
    {
        "authorization",
        "api_key",
        "api_secret",
        "apikey",
        "key_secret",
        "key_id",
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "openai_api_key",
        "razorpay_key_id",
        "razorpay_key_secret",
        "bearer",
        "auth",
    }
)
_SECRET_SUFFIXES = ("_secret", "_token", "_password", "_api_key", "_apikey")
_FORBIDDEN_EXACT = frozenset(
    {
        "prompt",
        "messages",
        "system_prompt",
        "openai_prompt",
        "raw_openai_response",
        "full_openai_response",
        "openai_response",
        "response_body",
        "razorpay_response",
        "raw_response",
        "raw_razorpay_response",
        "choices",
        "expected_lift",
        "treatment_effect",
        "hidden_problem",
        "best_intervention",
        "causal_label",
        "causal_true_uplift",
        "hidden_effect",
        "simulator_truth",
        "true_intervention_effect",
        "chain_of_thought",
        "hidden_causal_probability",
        "hidden_causal_effect",
        "true_effect",
        "hidden_causal_truth",
    }
)


# ---------------------------------------------------------------------------
# Canonicalization / hashing
# ---------------------------------------------------------------------------


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def canonical_created_at(dt: datetime) -> str:
    """UTC ISO-8601 representation used inside the hash payload."""
    return _as_utc(dt).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compute_event_hash(
    *,
    merchant_id: str,
    event_type: str,
    entity_type: str | None,
    entity_id: str | None,
    data: dict[str, Any],
    actor: str,
    prev_hash: str | None,
    created_at: datetime,
) -> str:
    """SHA-256 of the canonical event body. Database id is not included."""
    payload = {
        "actor": actor,
        "created_at": canonical_created_at(created_at),
        "data": data,
        "entity_id": entity_id,
        "entity_type": entity_type,
        "event_type": event_type,
        "merchant_id": merchant_id,
        "prev_hash": prev_hash,
    }
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return digest


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------


def _normalize_key(key: object) -> str:
    return str(key).lower().replace("-", "_")


def _is_forbidden_key(key: object) -> bool:
    lowered = _normalize_key(key)
    if lowered in _SECRET_EXACT or lowered in _FORBIDDEN_EXACT:
        return True
    if any(lowered.endswith(suffix) for suffix in _SECRET_SUFFIXES):
        return True
    if "prompt" in lowered:
        return True
    if "causal" in lowered:
        return True
    if lowered.startswith("hidden_"):
        return True
    if "authorization" in lowered:
        return True
    return False


def _looks_like_secret_value(value: str) -> bool:
    stripped = value.strip()
    lowered = stripped.lower()
    if lowered.startswith("bearer "):
        return True
    if "authorization:" in lowered:
        return True
    if stripped.startswith(("sk-", "sk_", "rzp_test_", "rzp_live_")):
        return True
    return False


def _json_safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return value
    if isinstance(value, datetime):
        return canonical_created_at(value)
    if isinstance(value, (bytes, bytearray)):
        return "[redacted]"
    return str(value)


def sanitize_audit_data(data: dict[str, Any] | None) -> dict[str, Any]:
    """Return a compact, secret-free, JSON-safe copy of ``data``."""
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise AuditError("data must be a dict or None")
    return _sanitize_value(data)  # type: ignore[return-value]


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, nested in value.items():
            if _is_forbidden_key(key):
                continue
            cleaned[str(key)] = _sanitize_value(nested)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str):
        if _looks_like_secret_value(value):
            return "[redacted]"
        return value
    return _json_safe_scalar(value)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _require_event_type(event_type: object) -> str:
    if not isinstance(event_type, str) or not event_type:
        raise AuditError("event_type is required and must be a stable audit constant")
    if event_type not in AUDIT_EVENT_TYPES:
        raise AuditError(f"invalid event_type: {event_type!r}")
    return event_type


def _require_actor(actor: object) -> str:
    if not isinstance(actor, str) or actor not in AUDIT_ACTORS:
        raise AuditError(f"invalid actor: {actor!r}")
    return actor


def _require_merchant_id(merchant_id: object) -> str:
    if not isinstance(merchant_id, str) or not merchant_id.strip():
        raise AuditError("merchant_id is required")
    return merchant_id


def _optional_str(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AuditError(f"{field} must be a string or None")
    return value


def validate_history_limit(limit: object) -> int:
    """Validate a history limit: int (not bool), 1..1000 inclusive."""
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise AuditError(
            f"limit must be an int, not {type(limit).__name__}"
        )
    if limit < MIN_HISTORY_LIMIT or limit > MAX_HISTORY_LIMIT:
        raise AuditError(
            f"limit must satisfy {MIN_HISTORY_LIMIT} <= limit <= {MAX_HISTORY_LIMIT}, "
            f"got {limit}"
        )
    return limit


def _latest_merchant_event(db: Session, merchant_id: str) -> AuditEvent | None:
    return (
        db.query(AuditEvent)
        .filter(AuditEvent.merchant_id == merchant_id)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .first()
    )


def _next_created_at(latest: AuditEvent | None) -> datetime:
    now = datetime.now(timezone.utc)
    if latest is None or latest.created_at is None:
        return now
    latest_ts = _as_utc(latest.created_at)
    if now <= latest_ts:
        return latest_ts + timedelta(microseconds=1)
    return now


def _find_audit_event(
    db: Session,
    *,
    merchant_id: str,
    event_type: str,
    entity_type: str | None,
    entity_id: str | None,
) -> AuditEvent | None:
    return (
        db.query(AuditEvent)
        .filter(AuditEvent.merchant_id == merchant_id)
        .filter(AuditEvent.event_type == event_type)
        .filter(AuditEvent.entity_type == entity_type)
        .filter(AuditEvent.entity_id == entity_id)
        .order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc())
        .first()
    )


# ---------------------------------------------------------------------------
# Public write API
# ---------------------------------------------------------------------------


def has_audit_event(
    db: Session,
    *,
    merchant_id: str,
    event_type: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
) -> bool:
    """Application-level uniqueness probe. No DB constraint is added."""
    return (
        _find_audit_event(
            db,
            merchant_id=merchant_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        is not None
    )


def record_audit_event(
    db: Session,
    *,
    merchant_id: str,
    event_type: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    data: dict | None = None,
    actor: str = ACTOR_SYSTEM,
) -> AuditEvent:
    """Append one audit event and flush. Never commits.

    Programming/config errors raise ``AuditError`` rather than silently
    skipping the record.
    """
    merchant_id = _require_merchant_id(merchant_id)
    event_type = _require_event_type(event_type)
    actor = _require_actor(actor)
    entity_type = _optional_str(entity_type, "entity_type")
    entity_id = _optional_str(entity_id, "entity_id")
    sanitized = sanitize_audit_data(data)

    latest = _latest_merchant_event(db, merchant_id)
    if latest is not None and not latest.event_hash:
        raise AuditError(
            "previous audit event is missing event_hash; refusing to extend a broken chain"
        )
    prev_hash = latest.event_hash if latest is not None else None
    created_at = _next_created_at(latest)
    event_hash = compute_event_hash(
        merchant_id=merchant_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        data=sanitized,
        actor=actor,
        prev_hash=prev_hash,
        created_at=created_at,
    )

    event = AuditEvent(
        merchant_id=merchant_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        data=sanitized,
        actor=actor,
        prev_hash=prev_hash,
        event_hash=event_hash,
        created_at=created_at,
    )
    db.add(event)
    db.flush()
    return event


def record_audit_event_once(
    db: Session,
    *,
    merchant_id: str,
    event_type: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    data: dict | None = None,
    actor: str = ACTOR_SYSTEM,
) -> AuditEvent:
    """Record a lifecycle event if one does not already exist for this identity.

    Uniqueness is application-level on
    ``(merchant_id, event_type, entity_type, entity_id)``.
    No database unique constraint is added.
    """
    merchant_id = _require_merchant_id(merchant_id)
    event_type = _require_event_type(event_type)
    entity_type = _optional_str(entity_type, "entity_type")
    entity_id = _optional_str(entity_id, "entity_id")
    existing = _find_audit_event(
        db,
        merchant_id=merchant_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    if existing is not None:
        return existing
    return record_audit_event(
        db,
        merchant_id=merchant_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        data=data,
        actor=actor,
    )


# ---------------------------------------------------------------------------
# Public read / verify API
# ---------------------------------------------------------------------------


def get_merchant_audit_history(
    db: Session,
    merchant_id: str,
    *,
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> list[AuditEvent]:
    """Chronological merchant-visible history. Never commits."""
    merchant_id = _require_merchant_id(merchant_id)
    limit = validate_history_limit(limit)
    return (
        db.query(AuditEvent)
        .filter(AuditEvent.merchant_id == merchant_id)
        .order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc())
        .limit(limit)
        .all()
    )


def get_experiment_audit_history(
    db: Session,
    experiment_id: str,
    *,
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> list[AuditEvent]:
    """Lifecycle events whose entity is this experiment. Never commits."""
    if not isinstance(experiment_id, str) or not experiment_id:
        raise AuditError("experiment_id is required")
    limit = validate_history_limit(limit)
    return (
        db.query(AuditEvent)
        .filter(AuditEvent.entity_type == ENTITY_EXPERIMENT)
        .filter(AuditEvent.entity_id == experiment_id)
        .order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc())
        .limit(limit)
        .all()
    )


def verify_merchant_audit_chain(db: Session, merchant_id: str) -> bool:
    """Recompute hashes chronologically.

    Returns True if the chain is valid. Returns False on prev_hash or
    event_hash mismatch. Does not raise for ordinary tamper detection.
    """
    merchant_id = _require_merchant_id(merchant_id)
    events = (
        db.query(AuditEvent)
        .filter(AuditEvent.merchant_id == merchant_id)
        .order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc())
        .all()
    )
    expected_prev: str | None = None
    for event in events:
        if event.prev_hash != expected_prev:
            return False
        if not event.event_hash or not event.created_at:
            return False
        data = event.data if isinstance(event.data, dict) else {}
        recomputed = compute_event_hash(
            merchant_id=event.merchant_id,
            event_type=event.event_type,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            data=data,
            actor=event.actor,
            prev_hash=event.prev_hash,
            created_at=event.created_at,
        )
        if event.event_hash != recomputed:
            return False
        expected_prev = event.event_hash
    return True
