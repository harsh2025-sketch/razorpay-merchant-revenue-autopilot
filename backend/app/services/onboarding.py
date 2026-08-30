"""Merchant onboarding and initial merchant-data ingestion (Task 21A).

This module adds a production-facing entry path without changing the existing
core domain schema. A merchant can be registered with a default deterministic
policy and an initial canonical CSV can be ingested as real observed payment
history. TechBazaar remains the explicit demo merchant and is never mixed with
merchant-uploaded observations.

Task 21A intentionally supports only the *initial* merchant CSV. Incremental
append/dedup semantics belong to Task 21B, so a second historical upload is
rejected instead of silently replaying or duplicating data.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Merchant, MerchantPolicy, PaymentAttempt

TECHBAZAAR_MERCHANT_ID = "merchant_techbazaar"
CSV_SOURCE = "merchant_csv"
MAX_CSV_BYTES = 10 * 1024 * 1024
MAX_CSV_ROWS = 100_000

DEFAULT_ALLOWED_INTERVENTIONS = [
    "payment_method_config",
    "offer_discount",
    "partial_payment",
    "expiry_config",
]

REQUIRED_CSV_COLUMNS = {
    "external_id",
    "amount_paise",
    "status",
    "created_at",
    "segment",
    "payment_method",
}

OPTIONAL_CSV_COLUMNS = {
    "currency",
    "failure_reason",
    "device_type",
    "customer_ref",
    "internal_order_ref",
    "razorpay_order_id",
    "razorpay_payment_id",
    "razorpay_payment_link_id",
    "completed_at",
}

ALLOWED_STATUSES = {"captured", "failed", "abandoned"}


class OnboardingError(Exception):
    """Base error for merchant onboarding."""


class MerchantOnboardingNotFoundError(OnboardingError):
    """Requested merchant does not exist."""


class MerchantAlreadyHasDataError(OnboardingError):
    """Initial ingestion was requested for a merchant that already has history."""


class MerchantCsvValidationError(OnboardingError):
    """Uploaded CSV does not satisfy the canonical merchant-data contract."""


@dataclass(frozen=True)
class MerchantDataStatus:
    merchant_id: str
    data_source: str
    historical_observations: int
    real_observations: int
    simulated_observations: int
    segment_count: int

    @property
    def has_data(self) -> bool:
        return self.historical_observations > 0


@dataclass(frozen=True)
class InitialCsvImportResult:
    merchant_id: str
    rows_imported: int
    data_status: MerchantDataStatus


def _clean_text(value: object, *, max_length: int, field: str, required: bool) -> str | None:
    if value is None:
        if required:
            raise MerchantCsvValidationError(f"{field} is required")
        return None
    text = str(value).strip()
    if not text:
        if required:
            raise MerchantCsvValidationError(f"{field} is required")
        return None
    if len(text) > max_length:
        raise MerchantCsvValidationError(
            f"{field} exceeds the maximum length of {max_length} characters"
        )
    return text


def _parse_datetime(value: object, *, field: str, required: bool) -> datetime | None:
    text = _clean_text(value, max_length=80, field=field, required=required)
    if text is None:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise MerchantCsvValidationError(
            f"{field} must be an ISO-8601 datetime"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_amount(value: object) -> int:
    text = _clean_text(value, max_length=30, field="amount_paise", required=True)
    assert text is not None
    try:
        amount = int(text)
    except ValueError as exc:
        raise MerchantCsvValidationError("amount_paise must be an integer") from exc
    if amount <= 0:
        raise MerchantCsvValidationError("amount_paise must be greater than zero")
    return amount


def _canonical_payment_attempt_id(merchant_id: str, external_id: str) -> str:
    digest = hashlib.sha256(f"{merchant_id}:{external_id}".encode("utf-8")).hexdigest()
    return f"csv_{digest[:40]}"


def _default_policy(merchant_id: str) -> MerchantPolicy:
    return MerchantPolicy(
        merchant_id=merchant_id,
        max_experiment_exposure_pct=0.10,
        max_discount_pct=0.15,
        min_margin_pct=0.05,
        max_concurrent_experiments=3,
        max_experiment_duration_hours=168,
        min_sample_size=30,
        max_financial_exposure=50000,
        allowed_interventions=list(DEFAULT_ALLOWED_INTERVENTIONS),
    )


def register_merchant(
    db: Session,
    *,
    name: str,
    category: str | None = None,
    monthly_gmv_paise: int | None = None,
) -> Merchant:
    """Create one merchant plus the default deterministic policy.

    The function flushes but never commits. The API owns the transaction so a
    malformed initial CSV can roll the registration back atomically.
    """
    merchant_name = str(name or "").strip()
    if len(merchant_name) < 2 or len(merchant_name) > 120:
        raise OnboardingError("merchant name must contain 2 to 120 characters")

    clean_category = None
    if category is not None:
        clean_category = str(category).strip() or None
        if clean_category is not None and len(clean_category) > 80:
            raise OnboardingError("merchant category must be at most 80 characters")

    if monthly_gmv_paise is not None:
        if isinstance(monthly_gmv_paise, bool) or monthly_gmv_paise < 0:
            raise OnboardingError("monthly_gmv_paise must be a non-negative integer")

    merchant = Merchant(
        name=merchant_name,
        category=clean_category,
        monthly_gmv=monthly_gmv_paise,
    )
    db.add(merchant)
    db.flush()

    db.add(_default_policy(merchant.id))
    db.flush()
    return merchant


def merchant_data_status(db: Session, merchant_id: str) -> MerchantDataStatus:
    merchant = db.get(Merchant, merchant_id)
    if merchant is None:
        raise MerchantOnboardingNotFoundError(f"merchant not found: {merchant_id}")

    historical_filter = (
        PaymentAttempt.merchant_id == merchant_id,
        PaymentAttempt.experiment_id.is_(None),
    )
    historical = int(
        db.query(func.count(PaymentAttempt.id)).filter(*historical_filter).scalar() or 0
    )
    real = int(
        db.query(func.count(PaymentAttempt.id))
        .filter(*historical_filter, PaymentAttempt.is_simulated.is_(False))
        .scalar()
        or 0
    )
    simulated = int(
        db.query(func.count(PaymentAttempt.id))
        .filter(*historical_filter, PaymentAttempt.is_simulated.is_(True))
        .scalar()
        or 0
    )
    segment_count = int(
        db.query(func.count(func.distinct(PaymentAttempt.segment)))
        .filter(*historical_filter, PaymentAttempt.segment.is_not(None))
        .scalar()
        or 0
    )

    if merchant_id == TECHBAZAAR_MERCHANT_ID:
        source = "demo"
    elif real > 0:
        source = CSV_SOURCE
    else:
        source = "none"

    return MerchantDataStatus(
        merchant_id=merchant_id,
        data_source=source,
        historical_observations=historical,
        real_observations=real,
        simulated_observations=simulated,
        segment_count=segment_count,
    )


def _normalize_header(fieldnames: Iterable[str | None] | None) -> list[str]:
    if fieldnames is None:
        raise MerchantCsvValidationError("CSV header row is required")
    normalized: list[str] = []
    for raw in fieldnames:
        if raw is None:
            raise MerchantCsvValidationError("CSV contains an unnamed column")
        name = raw.strip().lower()
        if not name:
            raise MerchantCsvValidationError("CSV contains an empty column name")
        normalized.append(name)
    if len(set(normalized)) != len(normalized):
        raise MerchantCsvValidationError("CSV contains duplicate column names")
    missing = sorted(REQUIRED_CSV_COLUMNS.difference(normalized))
    if missing:
        raise MerchantCsvValidationError(
            "CSV is missing required columns: " + ", ".join(missing)
        )
    return normalized


def _row_value(row: dict[str | None, str | None], field: str) -> str | None:
    for key, value in row.items():
        if key is not None and key.strip().lower() == field:
            return value
    return None


def _parse_payment_row(
    *,
    merchant_id: str,
    row_number: int,
    row: dict[str | None, str | None],
    seen_external_ids: set[str],
) -> PaymentAttempt:
    try:
        external_id = _clean_text(
            _row_value(row, "external_id"),
            max_length=200,
            field="external_id",
            required=True,
        )
        assert external_id is not None
        if external_id in seen_external_ids:
            raise MerchantCsvValidationError(
                f"duplicate external_id in upload: {external_id}"
            )
        seen_external_ids.add(external_id)

        amount = _parse_amount(_row_value(row, "amount_paise"))

        raw_status = _clean_text(
            _row_value(row, "status"), max_length=30, field="status", required=True
        )
        assert raw_status is not None
        status = raw_status.lower()
        if status not in ALLOWED_STATUSES:
            raise MerchantCsvValidationError(
                "status must be one of: abandoned, captured, failed"
            )

        created_at = _parse_datetime(
            _row_value(row, "created_at"), field="created_at", required=True
        )
        assert created_at is not None
        completed_at = _parse_datetime(
            _row_value(row, "completed_at"), field="completed_at", required=False
        )

        segment = _clean_text(
            _row_value(row, "segment"), max_length=100, field="segment", required=True
        )
        payment_method = _clean_text(
            _row_value(row, "payment_method"),
            max_length=60,
            field="payment_method",
            required=True,
        )
        assert segment is not None and payment_method is not None

        currency = (
            _clean_text(
                _row_value(row, "currency"),
                max_length=3,
                field="currency",
                required=False,
            )
            or "INR"
        ).upper()
        if len(currency) != 3 or not currency.isalpha():
            raise MerchantCsvValidationError("currency must be a 3-letter code")

        internal_order_ref = _clean_text(
            _row_value(row, "internal_order_ref"),
            max_length=200,
            field="internal_order_ref",
            required=False,
        )

        return PaymentAttempt(
            id=_canonical_payment_attempt_id(merchant_id, external_id),
            merchant_id=merchant_id,
            customer_ref=_clean_text(
                _row_value(row, "customer_ref"),
                max_length=200,
                field="customer_ref",
                required=False,
            ),
            internal_order_ref=internal_order_ref or external_id,
            razorpay_order_id=_clean_text(
                _row_value(row, "razorpay_order_id"),
                max_length=200,
                field="razorpay_order_id",
                required=False,
            ),
            razorpay_payment_id=_clean_text(
                _row_value(row, "razorpay_payment_id"),
                max_length=200,
                field="razorpay_payment_id",
                required=False,
            ),
            razorpay_payment_link_id=_clean_text(
                _row_value(row, "razorpay_payment_link_id"),
                max_length=200,
                field="razorpay_payment_link_id",
                required=False,
            ),
            amount=amount,
            currency=currency,
            payment_method=payment_method.lower(),
            status=status,
            failure_reason=_clean_text(
                _row_value(row, "failure_reason"),
                max_length=200,
                field="failure_reason",
                required=False,
            ),
            device_type=_clean_text(
                _row_value(row, "device_type"),
                max_length=100,
                field="device_type",
                required=False,
            ),
            segment=segment,
            source=CSV_SOURCE,
            experiment_id=None,
            variant=None,
            created_at=created_at,
            completed_at=completed_at,
            is_simulated=False,
        )
    except MerchantCsvValidationError as exc:
        raise MerchantCsvValidationError(f"row {row_number}: {exc}") from exc


def ingest_initial_csv(
    db: Session,
    *,
    merchant_id: str,
    content: bytes,
) -> InitialCsvImportResult:
    """Validate and ingest a merchant's first canonical historical CSV.

    No historical row is written until the whole file has validated. The
    function flushes but never commits; the caller owns the transaction.
    """
    if db.get(Merchant, merchant_id) is None:
        raise MerchantOnboardingNotFoundError(f"merchant not found: {merchant_id}")

    if len(content) == 0:
        raise MerchantCsvValidationError("CSV file is empty")
    if len(content) > MAX_CSV_BYTES:
        raise MerchantCsvValidationError(
            f"CSV exceeds the {MAX_CSV_BYTES // (1024 * 1024)} MB upload limit"
        )

    existing = (
        db.query(func.count(PaymentAttempt.id))
        .filter(
            PaymentAttempt.merchant_id == merchant_id,
            PaymentAttempt.experiment_id.is_(None),
        )
        .scalar()
        or 0
    )
    if int(existing) > 0:
        raise MerchantAlreadyHasDataError(
            "merchant already has historical payment data; use incremental ingestion"
        )

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise MerchantCsvValidationError("CSV must be UTF-8 encoded") from exc

    reader = csv.DictReader(io.StringIO(text, newline=""))
    _normalize_header(reader.fieldnames)

    parsed: list[PaymentAttempt] = []
    seen_external_ids: set[str] = set()
    for row_number, row in enumerate(reader, start=2):
        if not any((value or "").strip() for value in row.values()):
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

    internal_ids = [attempt.id for attempt in parsed]
    collision = (
        db.query(PaymentAttempt.id)
        .filter(PaymentAttempt.id.in_(internal_ids))
        .first()
    )
    if collision is not None:
        raise MerchantCsvValidationError(
            "CSV contains payment identifiers that were already imported"
        )

    db.add_all(parsed)
    db.flush()

    status = merchant_data_status(db, merchant_id)
    return InitialCsvImportResult(
        merchant_id=merchant_id,
        rows_imported=len(parsed),
        data_status=status,
    )
