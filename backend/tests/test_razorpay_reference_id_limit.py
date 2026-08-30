"""Regression coverage for Razorpay Payment Link reference IDs."""

from __future__ import annotations

from app.services.executor import MAX_REFERENCE_ID_LENGTH, _build_reference_id


def test_uuid_experiment_id_is_compacted_to_razorpay_limit() -> None:
    experiment_id = "123e4567-e89b-12d3-a456-426614174000"
    unbounded = f"mra_{experiment_id}_treatment_v1"

    assert len(unbounded) > MAX_REFERENCE_ID_LENGTH

    reference_id = _build_reference_id(experiment_id)

    assert MAX_REFERENCE_ID_LENGTH == 40
    assert len(reference_id) <= 40
    assert reference_id.startswith("mra_")
    assert reference_id.endswith("_treatment_v1")
    assert reference_id == _build_reference_id(experiment_id)


def test_short_experiment_id_remains_readable() -> None:
    experiment_id = "exp_123"

    assert _build_reference_id(experiment_id) == "mra_exp_123_treatment_v1"
