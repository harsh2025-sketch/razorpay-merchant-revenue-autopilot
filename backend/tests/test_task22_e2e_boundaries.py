"""Task 22 E2E boundary regressions.

The canonical TechBazaar merchant owns the sealed synthetic evaluation world.
Merchant-uploaded historical data must never be fed into that causal simulator
and presented as real experimental evidence.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.experiment_run_router import run_experiment_to_fixed_horizon
from app.db.models import ExperimentResult, PaymentAttempt
from app.services.one_click_experiment import (
    LiveExperimentTrafficRequired,
    run_experiment_to_decision,
)
from tests.test_autopilot_service import (
    add_policy_decision,
    add_resource,
    db_session,
    make_experiment,
    make_merchant,
    make_opportunity,
)

REAL_MERCHANT = "merchant_uploaded_e2e"


def _approved_deployed_uploaded_experiment(db):
    make_merchant(db, merchant_id=REAL_MERCHANT, exposure_cap=0.50)
    opportunity = make_opportunity(db, merchant_id=REAL_MERCHANT)
    experiment = make_experiment(
        db,
        opportunity=opportunity,
        status="approved",
        min_sample=40,
        traffic=0.10,
    )
    add_policy_decision(db, experiment, decision="APPROVE")
    add_resource(db, experiment)
    return experiment


def _experiment_attempts(db, experiment_id: str) -> int:
    return (
        db.query(PaymentAttempt)
        .filter(PaymentAttempt.experiment_id == experiment_id)
        .count()
    )


def test_uploaded_merchant_never_enters_techbazaar_synthetic_runtime(db_session):
    experiment = _approved_deployed_uploaded_experiment(db_session)

    with pytest.raises(
        LiveExperimentTrafficRequired,
        match="assigned real experiment outcomes",
    ):
        run_experiment_to_decision(db_session, experiment.id)

    assert _experiment_attempts(db_session, experiment.id) == 0
    assert (
        db_session.query(ExperimentResult)
        .filter(ExperimentResult.experiment_id == experiment.id)
        .count()
        == 0
    )


def test_uploaded_merchant_http_boundary_returns_stable_live_traffic_code(db_session):
    experiment = _approved_deployed_uploaded_experiment(db_session)
    experiment_id = experiment.id

    with pytest.raises(HTTPException) as raised:
        run_experiment_to_fixed_horizon(experiment_id, db_session)

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "LIVE_EXPERIMENT_TRAFFIC_REQUIRED"
    assert "evaluation-only" in raised.value.detail["message"]
    assert _experiment_attempts(db_session, experiment_id) == 0
    assert (
        db_session.query(ExperimentResult)
        .filter(ExperimentResult.experiment_id == experiment_id)
        .count()
        == 0
    )
