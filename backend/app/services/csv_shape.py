"""Cheap structural validation for merchant onboarding CSV uploads.

The full semantic validation remains in ``services.onboarding``. This pass runs
before merchant registration so malformed CSV shape cannot create a merchant
row and Python's ``DictReader`` edge cases never surface as internal errors.
"""

from __future__ import annotations

import csv
import io

from app.services.onboarding import (
    MAX_CSV_BYTES,
    MAX_CSV_ROWS,
    OPTIONAL_CSV_COLUMNS,
    REQUIRED_CSV_COLUMNS,
    MerchantCsvValidationError,
)

ALLOWED_CSV_COLUMNS = REQUIRED_CSV_COLUMNS | OPTIONAL_CSV_COLUMNS


def validate_initial_csv_shape(content: bytes) -> None:
    """Reject malformed/unsupported CSV structure before registration."""
    if not content:
        raise MerchantCsvValidationError("CSV file is empty")
    if len(content) > MAX_CSV_BYTES:
        raise MerchantCsvValidationError(
            f"CSV exceeds the {MAX_CSV_BYTES // (1024 * 1024)} MB upload limit"
        )

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise MerchantCsvValidationError("CSV must be UTF-8 encoded") from exc

    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        raw_header = next(reader)
    except StopIteration as exc:
        raise MerchantCsvValidationError("CSV header row is required") from exc

    normalized = [column.strip().lower() for column in raw_header]
    if any(not column for column in normalized):
        raise MerchantCsvValidationError("CSV contains an empty column name")
    if len(set(normalized)) != len(normalized):
        raise MerchantCsvValidationError("CSV contains duplicate column names")

    missing = sorted(REQUIRED_CSV_COLUMNS.difference(normalized))
    if missing:
        raise MerchantCsvValidationError(
            "CSV is missing required columns: " + ", ".join(missing)
        )

    unsupported = sorted(set(normalized).difference(ALLOWED_CSV_COLUMNS))
    if unsupported:
        raise MerchantCsvValidationError(
            "CSV contains unsupported columns: " + ", ".join(unsupported)
        )

    rows = 0
    width = len(raw_header)
    for row_number, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) != width:
            raise MerchantCsvValidationError(
                f"row {row_number}: expected {width} columns, got {len(row)}"
            )
        rows += 1
        if rows > MAX_CSV_ROWS:
            raise MerchantCsvValidationError(
                f"CSV exceeds the {MAX_CSV_ROWS} row upload limit"
            )

    if rows == 0:
        raise MerchantCsvValidationError("CSV contains no payment rows")
