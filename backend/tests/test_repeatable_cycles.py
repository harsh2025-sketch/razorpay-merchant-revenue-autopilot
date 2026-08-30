"""Regression tests for explicit repeatable Autopilot cycle rollover."""

from __future__ import annotations

import pytest

from app.db.models import AuditEvent, Experiment, ExperimentResult, Opportunity
from app.main import create_app
from app.services import autopilot
from app.services.audit import (
    ACTOR_DETECTOR,
    ENTITY_OPPORTUNITY,
    OPPORTUNITY_DETECTED,
    record_audit_event_once,
    verify_merchant_audit_chain,
)
from app.services.cycles import start_new_cycle
from tests.test_autopilot_service import (
    MERCHANT,
    add_policy_decision,
    add_resource,
    add_result,
    db_session,
    make_experiment,
    make_merchant,
    make_opportunity,
    seed_baseline,
)


def _audit_opportunity(db, opportunity: Opportunity) -> None:
    record_audit_event_once(
        db,
        merchant_id=opportunity.merchant_id,
        event_type=OPPORTUNITY_DETECTED,
        entity_type=ENTITY_OPPORTUNITY,
        entity_id=opportunity.id,
        data={
            "type": opportunity.type,
            "segment": opportunity.segment,
            "severity": opportunity.severity,
        },
        actor=ACTOR_DETECTOR,
    )


def test_completed_cycle_rolls_forward_without_deleting_history(db_session):
    make_merchant(db_session)
    seed_baseline(db_session)
    old_opportunity = make_opportunity(db_session)
    _audit_opportunity(db_session, old_opportunity)
    old_experiment = make_experiment(
        db_session, opportunity=old_opportunity, status="completed"
    )
    old_result = add_result(db_session, old_experiment, decision="KEEP")

    next_opportunity = start_new_cycle(db_session, MERCHANT)

    assert next_opportunity is not None
    assert next_opportunity.id != old_opportunity.id
    assert next_opportunity.status in autopilot.ACTIVE_OPPORTUNITY_STATUSES

    db_session.expire_all()
    persisted_old = db_session.get(Opportunity, old_opportunity.id)
    persisted_experiment = db_session.get(Experiment, old_experiment.id)
    persisted_result = db_session.get(ExperimentResult, old_result.id)
    assert persisted_old is not None and persisted_old.status == "resolved"
    assert persisted_experiment is not None and persisted_experiment.status == "completed"
    assert persisted_result is not None and persisted_result.decision == "KEEP"
    assert db_session.query(Opportunity).count() >= 2
    assert db_session.query(AuditEvent).count() >= 2
    assert verify_merchant_audit_chain(db_session, MERCHANT) is True

    transition = autopilot.resolve_transition(db_session, MERCHANT)
    assert transition.opportunity is None or transition.opportunity.id != old_opportunity.id
    assert transition.action != autopilot.ACTION_DONE


def test_policy_rejected_cycle_can_start_another_cycle(db_session):
    make_merchant(db_session)
    seed_baseline(db_session)
    old_opportunity = make_opportunity(db_session)
    old_experiment = make_experiment(
        db_session, opportunity=old_opportunity, status="rejected"
    )
    add_policy_decision(
        db_session,
        old_experiment,
        decision="REJECT",
        violations=["TREATMENT_EXPOSURE_EXCEEDED"],
    )

    assert autopilot.resolve_transition(db_session, MERCHANT).action == autopilot.ACTION_STOP

    next_opportunity = start_new_cycle(db_session, MERCHANT)

    assert db_session.get(Opportunity, old_opportunity.id).status == "resolved"
    assert db_session.get(Experiment, old_experiment.id).status == "rejected"
    assert next_opportunity is not None
    assert next_opportunity.id != old_opportunity.id


def test_approved_undeployed_cycle_is_cancelled_before_rollover(db_session):
    make_merchant(db_session)
    seed_baseline(db_session)
    old_opportunity = make_opportunity(db_session)
    old_experiment = make_experiment(
        db_session, opportunity=old_opportunity, status="approved"
    )
    add_policy_decision(db_session, old_experiment, decision="APPROVE")

    transition = autopilot.resolve_transition(db_session, MERCHANT)
    assert transition.action == autopilot.ACTION_DEPLOY
    assert autopilot.treatment_resource(db_session, old_experiment.id) is None

    next_opportunity = start_new_cycle(db_session, MERCHANT)

    db_session.expire_all()
    persisted = db_session.get(Experiment, old_experiment.id)
    assert persisted is not None
    assert persisted.status == "cancelled"
    assert persisted.ended_at is not None
    assert db_session.get(Opportunity, old_opportunity.id).status == "resolved"
    assert next_opportunity is not None
    assert next_opportunity.id != old_opportunity.id
    assert autopilot.autopilot_status(db_session, MERCHANT)["active_experiment_count"] == 0


def test_deployed_running_cycle_cannot_be_skipped(db_session):
    make_merchant(db_session)
    old_opportunity = make_opportunity(db_session)
    old_experiment = make_experiment(
        db_session, opportunity=old_opportunity, status="running"
    )
    add_policy_decision(db_session, old_experiment, decision="APPROVE")
    add_resource(db_session, old_experiment, status="active")

    with pytest.raises(autopilot.InvalidTransitionError):
        start_new_cycle(db_session, MERCHANT)

    assert db_session.get(Opportunity, old_opportunity.id).status == "detected"
    assert db_session.get(Experiment, old_experiment.id).status == "running"


def test_rollover_prefers_another_existing_detected_opportunity(db_session):
    make_merchant(db_session)
    old_opportunity = make_opportunity(db_session, severity=0.09)
    old_experiment = make_experiment(
        db_session, opportunity=old_opportunity, status="completed"
    )
    add_result(db_session, old_experiment, decision="INCONCLUSIVE")
    waiting_opportunity = make_opportunity(
        db_session,
        segment="web_general",
        severity=0.01,
        detected_value=0.48,
        baseline_value=0.58,
    )
    before_count = db_session.query(Opportunity).count()

    next_opportunity = start_new_cycle(db_session, MERCHANT)

    assert next_opportunity is not None
    assert next_opportunity.id == waiting_opportunity.id
    assert db_session.query(Opportunity).count() == before_count
    assert db_session.get(Opportunity, old_opportunity.id).status == "resolved"


def test_new_cycle_route_is_mounted_but_not_public_openapi():
    app = create_app(cors_origins=[])
    path = app.url_path_for(
        "start_new_cycle", merchant_id="merchant_techbazaar"
    )
    assert str(path) == "/api/v1/merchants/merchant_techbazaar/autopilot/new-cycle"
    assert "/api/v1/merchants/{merchant_id}/autopilot/new-cycle" not in app.openapi()[
        "paths"
    ]
