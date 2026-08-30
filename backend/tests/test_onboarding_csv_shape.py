"""Structural fail-closed coverage for Task 21A merchant CSV uploads."""

from __future__ import annotations

import pytest

from app.services.csv_shape import validate_initial_csv_shape
from app.services.onboarding import MerchantCsvValidationError


VALID = b"""external_id,amount_paise,status,created_at,segment,payment_method\np1,10000,captured,2026-08-01T10:00:00Z,android_budget,upi\n"""


def test_valid_minimal_csv_shape_passes() -> None:
    validate_initial_csv_shape(VALID)


def test_unsupported_column_is_rejected() -> None:
    csv_bytes = b"""external_id,amount_paise,status,created_at,segment,payment_method,hidden_expected_lift\np1,10000,captured,2026-08-01T10:00:00Z,android_budget,upi,0.9\n"""

    with pytest.raises(MerchantCsvValidationError, match="unsupported columns"):
        validate_initial_csv_shape(csv_bytes)


def test_extra_row_values_are_rejected_cleanly() -> None:
    csv_bytes = b"""external_id,amount_paise,status,created_at,segment,payment_method\np1,10000,captured,2026-08-01T10:00:00Z,android_budget,upi,unexpected\n"""

    with pytest.raises(MerchantCsvValidationError, match="expected 6 columns, got 7"):
        validate_initial_csv_shape(csv_bytes)


def test_short_row_is_rejected_cleanly() -> None:
    csv_bytes = b"""external_id,amount_paise,status,created_at,segment,payment_method\np1,10000,captured,2026-08-01T10:00:00Z,android_budget\n"""

    with pytest.raises(MerchantCsvValidationError, match="expected 6 columns, got 5"):
        validate_initial_csv_shape(csv_bytes)
