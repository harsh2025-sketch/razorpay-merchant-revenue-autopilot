"""Application-level idempotency for real Razorpay resource operations (Task 13).

This module wraps ``OperationExecution`` rows so external write operations
(e.g. creating or cancelling a Razorpay Payment Link) are performed at most
once per logical operation.

Rules enforced here:

- ``pending``     - an operation is in flight (or ambiguous). A second call
                    is refused; no second external write is issued.
- ``succeeded``   - a usable resource exists. A matching hash is reused.
                    A different hash is a conflict and is never re-executed.
- ``failed``      - a definitive, non-ambiguous API failure. A matching hash
                    may be retried explicitly by transitioning to ``pending``.

Ambiguous failures (network timeouts / server errors) are recorded as
``pending`` with ``response_json={"error": "ambiguous_network_failure"}`` so
the system never blindly retries a possibly-created external resource.

The module never commits and never stores raw secrets or full external
responses.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import OperationExecution

__all__ = [
    "IdempotencyConflictError",
    "IdempotencyError",
    "IdempotencyInProgressError",
    "begin_operation",
    "compute_request_hash",
    "mark_operation_ambiguous",
    "mark_operation_failed",
    "mark_operation_succeeded",
]

class IdempotencyError(Exception):
    """Base error for idempotency violations."""


class IdempotencyConflictError(IdempotencyError):
    """The same operation key was reused with a different request payload."""


class IdempotencyInProgressError(IdempotencyError):
    """An operation for this key is already pending/in-progress/ambiguous."""


def compute_request_hash(payload: dict[str, Any]) -> str:
    """Return a deterministic SHA-256 hash of the canonical JSON payload.

    Dictionary key order does not affect the hash because ``sort_keys`` is
    used, and whitespace is removed via compact separators.
    """
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def begin_operation(
    db: Session,
    *,
    operation_key: str,
    operation_type: str,
    request_payload: dict[str, Any],
) -> OperationExecution:
    """Create or return the ``OperationExecution`` for a logical operation.

    Returns an operation in one of two states:

    - ``pending`` when the caller should execute the external operation, or
    - ``succeeded`` when a previous matching operation already completed.

    Any other state raises an ``IdempotencyError`` subclass.
    """
    request_hash = compute_request_hash(request_payload)
    existing = (
        db.query(OperationExecution)
        .filter(OperationExecution.operation_key == operation_key)
        .one_or_none()
    )

    if existing is None:
        operation = OperationExecution(
            operation_key=operation_key,
            operation_type=operation_type,
            request_payload_hash=request_hash,
            status="pending",
            razorpay_resource_id=None,
            response_json=None,
        )
        db.add(operation)
        db.flush()
        return operation

    if existing.status == "succeeded":
        if existing.request_payload_hash != request_hash:
            raise IdempotencyConflictError(
                f"operation {operation_key!r} already succeeded with a "
                "different request payload"
            )
        return existing

    if existing.status == "pending":
        raise IdempotencyInProgressError(
            f"operation {operation_key!r} is already pending or ambiguous; "
            "refusing to issue a second external write"
        )

    if existing.status == "failed":
        if existing.request_payload_hash != request_hash:
            raise IdempotencyConflictError(
                f"operation {operation_key!r} failed with a different request "
                "payload; refusing to execute"
            )
        # Explicit retry of a definitive, non-ambiguous failure is safe.
        existing.status = "pending"
        existing.razorpay_resource_id = None
        existing.response_json = None
        db.flush()
        return existing

    # Unknown status (should never happen with Task 13 vocabulary). Fail
    # closed rather than risking a duplicate external resource.
    raise IdempotencyInProgressError(
        f"operation {operation_key!r} has unexpected status {existing.status!r}"
    )


def _safe_status_code(status_code: int | None) -> int | None:
    if isinstance(status_code, bool):
        return None
    if not isinstance(status_code, int):
        return None
    return status_code


def mark_operation_succeeded(
    db: Session,
    operation: OperationExecution,
    *,
    razorpay_resource_id: str,
    response_json: dict[str, Any] | None = None,
) -> OperationExecution:
    """Mark a completed external operation as succeeded."""
    operation.status = "succeeded"
    operation.razorpay_resource_id = razorpay_resource_id
    operation.response_json = _safe_metadata(response_json)
    db.flush()
    return operation


def mark_operation_failed(
    db: Session,
    operation: OperationExecution,
    *,
    status_code: int | None = None,
    message: str | None = None,
) -> OperationExecution:
    """Mark a definitive API failure.

    Only a compact, safe error envelope is persisted. The raw exception
    message is deliberately NOT stored because it could contain credentials;
    a later explicit retry (same payload) is allowed.
    """
    operation.status = "failed"
    operation.razorpay_resource_id = None
    response: dict[str, Any] = {"error": "definitive_api_failure"}
    code = _safe_status_code(status_code)
    if code is not None:
        response["status_code"] = code
    operation.response_json = response
    db.flush()
    return operation


def mark_operation_ambiguous(
    db: Session,
    operation: OperationExecution,
    *,
    status_code: int | None = None,
) -> OperationExecution:
    """Mark an ambiguous failure as pending (never auto-retry).

    The operation stays ``pending`` so any subsequent deploy/rollback call is
    refused by ``begin_operation`` instead of sending another POST. Only the
    documented safe envelope is persisted; no raw exception details.
    """
    operation.status = "pending"
    operation.razorpay_resource_id = None
    operation.response_json = {"error": "ambiguous_network_failure"}
    db.flush()
    return operation


def _safe_metadata(response_json: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(response_json, dict):
        return {}
    metadata: dict[str, Any] = {}
    for key in ("id", "status", "reference_id"):
        value = response_json.get(key)
        if value is not None:
            metadata[key] = str(value)[:200]
    # Never persist arbitrary external response bodies here.
    return metadata
