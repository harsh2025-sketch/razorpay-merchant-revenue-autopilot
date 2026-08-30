"""Incremental merchant-data ingestion and deterministic demo periods (Task 21B).

This module enforces the product rule that a new optimization cycle may only
consume genuinely new merchant evidence. Real merchant CSVs are parsed against
the same canonical contract as Task 21A, deduplicated by the stable external
transaction id, and append only previously unseen rows. TechBazaar advances in
deterministic non-overlapping periods with distinct payment-attempt ids.

A successful append is recorded in the existing generic operation ledger. That
record is not an external payment write; it is a durable data-revision marker
used by the cycle and detector boundaries to distinguish new evidence from a
replay of an unchanged historical dataset.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta, timezone
from typing import Iterable, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Merchant, OperationExecution, Opportunity, PaymentAttempt
from app.services.onboarding import (
    CSV_SOURCE,
    MAX_CSV_BYTES,
    MAX_CSV_ROWS,
    OPTIONAL_CSV_COLUMNS,
    REQUIRED_CSV_COLUMNS,
    TECHBAZAAR_MERCHANT_ID,
    MerchantCsvValidationError,
    MerchantOnboardingNotFoundError,
    _normalize_header,
    _parse_payment_row,
    merchant_data_status,
)
from app.simulation.generator import generate_baseline_events
from app.simulation.merchant import TECHBAZAAR_PROFILE

DATA_APPEND_OPERATION_TYPE = "merchant_data_append"
DEMO_PERIOD_DAYS = 7
DEMO_PERIOD_SEED_BASE = 20260901
QUERY_CHUNK_SIZE = 500


class IncrementalDataError(Exception):
    """Base error for Task 21B data updates."""


class IncrementalDataSourceError(IncrementalDataError):
    """Raised when a data-update endpoint is used for the wrong source type."""


class IncrementalDataConflictError(IncrementalDataError):
    """Raised when an existing transaction id carries different immutable data."""


@dataclass(frozen=True)
class IncrementalCsvResult:
    merchant_id: str
    rows_received: int
    rows_appended: int
    rows_deduplicated: int
    data_status: object


@dataclass(frozen=True)
class DemoPeriodResult:
    merchant_id: str
    period_index: int
    period_start: datetime
    period_end: datetime
    rows_appended: int
    data_status: object


@dataclass(frozen=True)
class DetectionReadiness:
    merchant_id: str
    ready: bool
    reason: str
    latest_opportunity_at: datetime | None
    latest_data_append_at: datetime | None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _chunks(values: Sequence[str], size: int = QUERY_CHUNK_SIZE) -> Iterable[Sequence[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _nonempty_csv_row(row: dict[str | None, object]) -> bool:
    for value in row.values():
        if isinstance(value, list):
            if any(str(item or "").strip() for item in value):
                return True
        elif str(value or "").strip():
            return True
    return False


def _parse_csv_attempts(*, merchant_id: str, content: bytes) -> list[PaymentAttempt]:
    if len(content) == 0:
        raise MerchantCsvValidationError("CSV file is empty")
    if len(content) > MAX_CSV_BYTES:
        raise MerchantCsvValidationError(
            f"CSV exceeds the {MAX_CSV_BYTES // (1024 * 1024)} MB upload limit"
        )

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise MerchantCsvValidationError("CSV must be UTF-8 encoded") from exc

    reader = csv.DictReader(io.StringIO(text, newline=""))
    normalized = _normalize_header(reader.fieldnames)
    unsupported = sorted(
        set(normalized).difference(REQUIRED_CSV_COLUMNS | OPTIONAL_CSV_COLUMNS)
    )
    if unsupported:
        raise MerchantCsvValidationError(
            "CSV contains unsupported columns: " + ", ".join(unsupported)
        )

    parsed: list[PaymentAttempt] = []
    seen_external_ids: set[str] = set()
    for row_number, row in enumerate(reader, start=2):
        if None in row:
            raise MerchantCsvValidationError(
                f"row {row_number}: row contains more values than the header"
            )
        if not _nonempty_csv_row(row):
            continue
        if len(parsed) >= MAX_CSV_ROWS:
            raise MerchantCsvValidationError(
                f"CSV exceeds the {MAX_CSV_ROWS} row upload limit"
            )
        parsed.append(
            _parse_payment_row(
                merchant_id=merchant_id,
                row_number=row_number,
                row=row,
                seen_external_ids=seen_external_ids,
            )
        )

    if not parsed:
        raise MerchantCsvValidationError("CSV contains no payment rows")
    return parsed


def _load_existing_attempts(db: Session, ids: Sequence[str]) -> dict[str, PaymentAttempt]:
    found: dict[str, PaymentAttempt] = {}
    for chunk in _chunks(ids):
        rows = db.query(PaymentAttempt).filter(PaymentAttempt.id.in_(list(chunk))).all()
        found.update({row.id: row for row in rows})
    return found


def _same_observation(existing: PaymentAttempt, incoming: PaymentAttempt) -> bool:
    """Compare immutable merchant-observation fields for one external id."""
    return (
        existing.merchant_id == incoming.merchant_id
        and existing.experiment_id is None
        and incoming.experiment_id is None
        and existing.amount == incoming.amount
        and existing.currency == incoming.currency
        and existing.payment_method == incoming.payment_method
        and existing.status == incoming.status
        and existing.failure_reason == incoming.failure_reason
        and existing.device_type == incoming.device_type
        and existing.segment == incoming.segment
        and existing.source == incoming.source
        and existing.customer_ref == incoming.customer_ref
        and existing.internal_order_ref == incoming.internal_order_ref
        and existing.razorpay_order_id == incoming.razorpay_order_id
        and existing.razorpay_payment_id == incoming.razorpay_payment_id
        and existing.razorpay_payment_link_id == incoming.razorpay_payment_link_id
        and _as_utc(existing.created_at) == _as_utc(incoming.created_at)
        and _as_utc(existing.completed_at) == _as_utc(incoming.completed_at)
        and existing.is_simulated is False
        and incoming.is_simulated is False
    )


def _merchant_revision_prefix(merchant_id: str) -> str:
    # Hashing makes the prefix SQL-LIKE-safe (merchant ids may contain '_' or
    # '%') and keeps arbitrary tenant identifiers out of generic ledger keys.
    digest = hashlib.sha256(merchant_id.encode("utf-8")).hexdigest()
    return f"merchant:{digest}:data_append:"


def _append_operation_key(merchant_id: str, revision_token: str) -> str:
    return _merchant_revision_prefix(merchant_id) + revision_token


def _record_append_revision(
    db: Session,
    *,
    merchant_id: str,
    revision_token: str,
    request_hash: str,
    response: dict,
) -> OperationExecution:
    key = _append_operation_key(merchant_id, revision_token)
    existing = (
        db.query(OperationExecution)
        .filter(OperationExecution.operation_key == key)
        .one_or_none()
    )
    if existing is not None:
        return existing
    row = OperationExecution(
        operation_key=key,
        operation_type=DATA_APPEND_OPERATION_TYPE,
        request_payload_hash=request_hash,
        status="succeeded",
        response_json=response,
    )
    db.add(row)
    db.flush()
    return row


def latest_data_append_at(db: Session, merchant_id: str) -> datetime | None:
    prefix = _merchant_revision_prefix(merchant_id)
    return (
        db.query(func.max(OperationExecution.created_at))
        .filter(OperationExecution.operation_type == DATA_APPEND_OPERATION_TYPE)
        .filter(OperationExecution.status == "succeeded")
        .filter(OperationExecution.operation_key.like(prefix + "%"))
        .scalar()
    )


def has_new_data_since(
    db: Session,
    merchant_id: str,
    since: datetime | None,
) -> bool:
    latest = latest_data_append_at(db, merchant_id)
    if latest is None:
        return False
    if since is None:
        return True
    return _as_utc(latest) > _as_utc(since)


def data_revision_count(db: Session, merchant_id: str) -> int:
    prefix = _merchant_revision_prefix(merchant_id)
    return int(
        db.query(func.count(OperationExecution.id))
        .filter(OperationExecution.operation_type == DATA_APPEND_OPERATION_TYPE)
        .filter(OperationExecution.status == "succeeded")
        .filter(OperationExecution.operation_key.like(prefix + "%"))
        .scalar()
        or 0
    )


def detection_readiness(db: Session, merchant_id: str) -> DetectionReadiness:
    """Whether a no-active-opportunity merchant has evidence worth rescanning.

    The first historical dataset is always analyzable. After a detector pass,
    another scan is actionable only when a successful append revision is newer
    than the latest persisted opportunity. All-duplicate uploads create no
    revision, so they cannot make unchanged evidence look fresh.
    """
    if db.get(Merchant, merchant_id) is None:
        raise MerchantOnboardingNotFoundError(f"merchant not found: {merchant_id}")

    historical = int(
        db.query(func.count(PaymentAttempt.id))
        .filter(PaymentAttempt.merchant_id == merchant_id)
        .filter(PaymentAttempt.experiment_id.is_(None))
        .scalar()
        or 0
    )
    latest_opportunity = (
        db.query(Opportunity)
        .filter(Opportunity.merchant_id == merchant_id)
        .order_by(Opportunity.created_at.desc(), Opportunity.id.desc())
        .first()
    )
    latest_append = latest_data_append_at(db, merchant_id)

    if historical == 0:
        return DetectionReadiness(
            merchant_id=merchant_id,
            ready=False,
            reason="EMPTY_DATA",
            latest_opportunity_at=(
                latest_opportunity.created_at if latest_opportunity is not None else None
            ),
            latest_data_append_at=latest_append,
        )
    if latest_opportunity is None:
        return DetectionReadiness(
            merchant_id=merchant_id,
            ready=True,
            reason="INITIAL_DATA",
            latest_opportunity_at=None,
            latest_data_append_at=latest_append,
        )
    if has_new_data_since(db, merchant_id, latest_opportunity.created_at):
        return DetectionReadiness(
            merchant_id=merchant_id,
            ready=True,
            reason="NEW_DATA",
            latest_opportunity_at=latest_opportunity.created_at,
            latest_data_append_at=latest_append,
        )
    return DetectionReadiness(
        merchant_id=merchant_id,
        ready=False,
        reason="WAITING_FOR_NEW_DATA",
        latest_opportunity_at=latest_opportunity.created_at,
        latest_data_append_at=latest_append,
    )


def ingest_incremental_csv(
    db: Session,
    *,
    merchant_id: str,
    content: bytes,
) -> IncrementalCsvResult:
    merchant = db.get(Merchant, merchant_id)
    if merchant is None:
        raise MerchantOnboardingNotFoundError(f"merchant not found: {merchant_id}")
    if merchant_id == TECHBAZAAR_MERCHANT_ID:
        raise IncrementalDataSourceError(
            "TechBazaar advances through deterministic demo periods, not merchant CSV uploads"
        )

    current = merchant_data_status(db, merchant_id)
    if current.real_observations <= 0:
        raise IncrementalDataSourceError(
            "merchant has no initial real payment history; complete onboarding first"
        )

    parsed = _parse_csv_attempts(merchant_id=merchant_id, content=content)
    existing = _load_existing_attempts(db, [row.id for row in parsed])

    new_rows: list[PaymentAttempt] = []
    duplicates = 0
    for incoming in parsed:
        previous = existing.get(incoming.id)
        if previous is None:
            new_rows.append(incoming)
            continue
        if not _same_observation(previous, incoming):
            raise IncrementalDataConflictError(
                "an existing external_id was re-uploaded with different transaction data"
            )
        duplicates += 1

    if new_rows:
        db.add_all(new_rows)
        db.flush()
        request_hash = hashlib.sha256(content).hexdigest()
        _record_append_revision(
            db,
            merchant_id=merchant_id,
            revision_token=f"csv:{request_hash}",
            request_hash=request_hash,
            response={
                "source": CSV_SOURCE,
                "rows_received": len(parsed),
                "rows_appended": len(new_rows),
                "rows_deduplicated": duplicates,
            },
        )

    status = merchant_data_status(db, merchant_id)
    return IncrementalCsvResult(
        merchant_id=merchant_id,
        rows_received=len(parsed),
        rows_appended=len(new_rows),
        rows_deduplicated=duplicates,
        data_status=status,
    )


def _demo_attempt_from_event(event, *, period_index: int, row_index: int) -> PaymentAttempt:
    return PaymentAttempt(
        id=f"pa_demo_p{period_index:03d}_{row_index:06d}",
        merchant_id=event.merchant_id,
        customer_ref=event.customer_ref,
        internal_order_ref=f"demo-period-{period_index}:{row_index}",
        amount=event.amount,
        currency=event.currency,
        payment_method=event.payment_method,
        status=event.status,
        failure_reason=event.failure_reason,
        device_type=event.device_type,
        segment=event.segment,
        source=event.source,
        experiment_id=None,
        variant=None,
        created_at=event.created_at,
        completed_at=event.completed_at,
        is_simulated=True,
    )


def append_next_demo_period(
    db: Session,
    *,
    days: int = DEMO_PERIOD_DAYS,
) -> DemoPeriodResult:
    merchant = db.get(Merchant, TECHBAZAAR_MERCHANT_ID)
    if merchant is None:
        raise MerchantOnboardingNotFoundError(
            f"merchant not found: {TECHBAZAAR_MERCHANT_ID}"
        )
    if days <= 0 or days > 31:
        raise IncrementalDataError("demo period days must be between 1 and 31")

    latest_created = (
        db.query(func.max(PaymentAttempt.created_at))
        .filter(PaymentAttempt.merchant_id == TECHBAZAAR_MERCHANT_ID)
        .filter(PaymentAttempt.experiment_id.is_(None))
        .scalar()
    )
    if latest_created is None:
        raise IncrementalDataSourceError(
            "TechBazaar historical baseline is not available"
        )
    latest_created = _as_utc(latest_created)
    assert latest_created is not None

    period_index = data_revision_count(db, TECHBAZAAR_MERCHANT_ID) + 2
    period_start_date = latest_created.date() + timedelta(days=1)
    period_end_date = period_start_date + timedelta(days=days - 1)
    period_end = datetime.combine(period_end_date, time(23, 59, 59), tzinfo=timezone.utc)
    profile = replace(
        TECHBAZAAR_PROFILE,
        days=days,
        anchor_timestamp_iso=period_end.isoformat(),
    )
    events = generate_baseline_events(
        profile=profile,
        seed=DEMO_PERIOD_SEED_BASE + period_index * 1009,
        days=days,
    )
    rows = [
        _demo_attempt_from_event(event, period_index=period_index, row_index=index)
        for index, event in enumerate(events, start=1)
    ]
    if not rows:
        raise IncrementalDataError("demo period generator produced no transactions")

    first_created = min(_as_utc(row.created_at) for row in rows)
    last_created = max(_as_utc(row.created_at) for row in rows)
    assert first_created is not None and last_created is not None
    if first_created <= latest_created:
        raise IncrementalDataConflictError(
            "next demo period overlaps existing historical observations"
        )

    collisions = _load_existing_attempts(db, [row.id for row in rows])
    if collisions:
        raise IncrementalDataConflictError(
            "next demo period would reuse existing payment-attempt identifiers"
        )

    db.add_all(rows)
    db.flush()
    revision_token = f"demo-period:{period_index}"
    request_hash = hashlib.sha256(
        f"{TECHBAZAAR_MERCHANT_ID}:{period_index}:{days}".encode("utf-8")
    ).hexdigest()
    _record_append_revision(
        db,
        merchant_id=TECHBAZAAR_MERCHANT_ID,
        revision_token=revision_token,
        request_hash=request_hash,
        response={
            "source": "demo_period",
            "period_index": period_index,
            "period_start": first_created.isoformat(),
            "period_end": last_created.isoformat(),
            "rows_appended": len(rows),
        },
    )

    status = merchant_data_status(db, TECHBAZAAR_MERCHANT_ID)
    return DemoPeriodResult(
        merchant_id=TECHBAZAAR_MERCHANT_ID,
        period_index=period_index,
        period_start=first_created,
        period_end=last_created,
        rows_appended=len(rows),
        data_status=status,
    )
