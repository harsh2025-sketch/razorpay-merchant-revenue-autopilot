"""Task 21C: one-click fixed-horizon experiment regressions."""

from __future__ import annotations

import pytest

from app.db.models import ExperimentResult, PaymentAttempt
from app.main import create_app
from app.services.one_click_experiment import (
    OneClickExperimentError,
    run_experiment_to_decision,
)
from tests.test_autopilot_service import (
    MERCHANT,
    add_attempts,
    add_policy_decision,
    add_resource,
    db_session,
    make_experiment,
    make_merchant,
)


def _attempt_count(db, experiment_id: str) -> int:
    return (
        db.query(PaymentAttempt)
        .filter(PaymentAttempt.experiment_id == experiment_id)
        .count()
    )


def test_one_request_reaches_horizon_and_records_one_decision(db_session):
    make_merchant(db_session, exposure_cap=0.50)
    experiment = make_experiment(
        db_session,
        status="approved",
        min_sample=200,
        traffic=0.10,
    )
    add_policy_decision(db_session, experiment, decision="APPROVE")
    add_resource(db_session, experiment)

    result = run_experiment_to_decision(db_session, experiment.id)

    assert result.experiment_id == experiment.id
    assert result.control_attempts >= 200
    assert result.treatment_attempts >= 200
    assert result.sample_target_per_variant == 200
    assert result.generated_attempts > 0
    assert 1 <= result.runtime_batches <= 25
    assert result.decision in {"KEEP", "ROLLBACK", "INCONCLUSIVE"}
    assert db_session.query(ExperimentResult).filter_by(experiment_id=experiment.id).count() == 1
    db_session.refresh(experiment)
    assert experiment.status == "completed"


def test_repeated_one_click_call_is_idempotent_after_decision(db_session):
    make_merchant(db_session, exposure_cap=0.50)
    experiment = make_experiment(
        db_session,
        status="approved",
        min_sample=40,
        traffic=0.10,
    )
    add_policy_decision(db_session, experiment, decision="APPROVE")
    add_resource(db_session, experiment)

    first = run_experiment_to_decision(db_session, experiment.id)
    attempts_after_first = _attempt_count(db_session, experiment.id)
    second = run_experiment_to_decision(db_session, experiment.id)

    assert second.decision == first.decision
    assert second.generated_attempts == 0
    assert second.runtime_batches == 0
    assert _attempt_count(db_session, experiment.id) == attempts_after_first
    assert db_session.query(ExperimentResult).filter_by(experiment_id=experiment.id).count() == 1


def test_existing_full_horizon_evaluates_without_generating_more_traffic(db_session):
    make_merchant(db_session)
    experiment = make_experiment(
        db_session,
        status="running",
        min_sample=10,
        traffic=0.50,
    )
    add_policy_decision(db_session, experiment, decision="APPROVE")
    add_resource(db_session, experiment)
    add_attempts(db_session, experiment, variant="control", count=10, captured=5)
    add_attempts(db_session, experiment, variant="treatment", count=10, captured=7)
    before = _attempt_count(db_session, experiment.id)

    result = run_experiment_to_decision(db_session, experiment.id)

    assert result.generated_attempts == 0
    assert result.runtime_batches == 0
    assert result.control_attempts == 10
    assert result.treatment_attempts == 10
    assert _attempt_count(db_session, experiment.id) == before
    assert db_session.query(ExperimentResult).filter_by(experiment_id=experiment.id).count() == 1


def test_run_to_decision_requires_persisted_policy_approval(db_session):
    make_merchant(db_session)
    experiment = make_experiment(db_session, status="approved", min_sample=10)
    add_resource(db_session, experiment)

    with pytest.raises(OneClickExperimentError, match="not authorized"):
        run_experiment_to_decision(db_session, experiment.id)

    assert _attempt_count(db_session, experiment.id) == 0
    assert db_session.query(ExperimentResult).count() == 0


def test_run_to_decision_requires_active_deployed_treatment(db_session):
    make_merchant(db_session)
    experiment = make_experiment(db_session, status="approved", min_sample=10)
    add_policy_decision(db_session, experiment, decision="APPROVE")

    with pytest.raises(OneClickExperimentError, match="no active deployed treatment"):
        run_experiment_to_decision(db_session, experiment.id)

    assert _attempt_count(db_session, experiment.id) == 0


def test_dashboard_route_is_mounted_but_not_added_to_frozen_openapi():
    app = create_app(cors_origins=[])
    path = app.url_path_for(
        "run_experiment_to_fixed_horizon", experiment_id="experiment_test"
    )
    assert str(path) == "/api/v1/experiments/experiment_test/run-to-decision"
    assert (
        "/api/v1/experiments/{experiment_id}/run-to-decision"
        not in app.openapi()["paths"]
    )
