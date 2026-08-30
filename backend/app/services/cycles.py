"""Explicit lifecycle rollover for repeatable Autopilot optimization cycles.

A completed, rejected, or safely abandoned pre-deployment cycle remains fully
persisted. Starting another cycle never deletes payment attempts, experiments,
results, resources, or audit history.

Task 21B adds one crucial product invariant: once every opportunity from an
observation pass has been consumed, Autopilot may not run the detector again
until the historical observation revision changes. A durable detector marker
also makes zero-opportunity passes non-repeatable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Experiment, Opportunity
from app.engines.opportunities import run_opportunity_detection
from app.services import autopilot
from app.services.incremental_data import detection_readiness


RESTARTABLE_ACTIONS = frozenset(
    {
        autopilot.ACTION_STOP,
        autopilot.ACTION_BLOCKED,
        autopilot.ACTION_DEPLOY,
        autopilot.ACTION_DONE,
    }
)


def _is_restartable(db: Session, transition: Any) -> bool:
    """Return whether closing the current cycle is safe."""
    if transition.action in {autopilot.ACTION_STOP, autopilot.ACTION_BLOCKED}:
        return True
    if transition.action == autopilot.ACTION_DEPLOY:
        return (
            transition.experiment is not None
            and transition.experiment.status in autopilot.DEPLOYABLE_EXPERIMENT_STATUSES
            and autopilot.treatment_resource(db, transition.experiment.id) is None
        )
    return (
        transition.action == autopilot.ACTION_DONE
        and transition.experiment is not None
        and transition.experiment.status in autopilot.TERMINAL_EXPERIMENT_STATUSES
    )


def _cancel_undeployed_experiment(
    db: Session,
    experiment: Experiment | None,
) -> None:
    """Close an approved experiment that never created a treatment resource."""
    if experiment is None:
        return
    if experiment.status not in autopilot.DEPLOYABLE_EXPERIMENT_STATUSES:
        return
    if autopilot.treatment_resource(db, experiment.id) is not None:
        raise autopilot.InvalidTransitionError(
            "A deployed treatment must finish its current cycle before rollover."
        )

    experiment.status = "cancelled"
    experiment.ended_at = datetime.now(timezone.utc)
    db.flush()


def _latest_opportunity(db: Session, merchant_id: str) -> Opportunity | None:
    return (
        db.query(Opportunity)
        .filter(Opportunity.merchant_id == merchant_id)
        .order_by(Opportunity.created_at.desc(), Opportunity.id.desc())
        .first()
    )


def _resolve_stale_active_opportunities(
    db: Session,
    merchant_id: str,
    *,
    older_than: datetime,
) -> None:
    """Retire waiting opportunities from an older observation revision.

    When new data arrives while an earlier pass still has untouched waiting
    opportunities, those rows keep their historical evidence for audit but are
    resolved before the updated dataset is scanned.
    """
    (
        db.query(Opportunity)
        .filter(Opportunity.merchant_id == merchant_id)
        .filter(Opportunity.status.in_(list(autopilot.ACTIVE_OPPORTUNITY_STATUSES)))
        .filter(Opportunity.created_at <= older_than)
        .update({Opportunity.status: "resolved"}, synchronize_session=False)
    )
    db.flush()


def _detect_if_revision_ready(
    db: Session,
    merchant_id: str,
    *,
    stale_before: datetime | None = None,
) -> Opportunity | None:
    """Run one detector pass only when the historical revision is unconsumed."""
    readiness = detection_readiness(db, merchant_id)
    if not readiness.ready:
        return None

    if stale_before is not None:
        _resolve_stale_active_opportunities(
            db,
            merchant_id,
            older_than=stale_before,
        )

    run_opportunity_detection(db, merchant_id)
    return autopilot.focus_opportunity(db, merchant_id)


def start_new_cycle(db: Session, merchant_id: str) -> Opportunity | None:
    """Close a safe cycle and return the next opportunity, if evidence permits.

    Rules:
    - Runtime, evaluation, and rollback work can never be skipped.
    - Completed/rejected cycles are preserved and their opportunity becomes
      ``resolved``.
    - An approved experiment with no treatment resource can be explicitly
      abandoned and is marked ``cancelled``.
    - Without new merchant data, another opportunity already detected in the
      same observation pass may be used, but the detector is never run again.
    - When the historical revision changed, remaining waiting opportunities
      from the old pass are preserved as resolved and one new detector pass is
      run against the updated merchant evidence.
    - A detector pass with zero opportunities is still consumed and waits for
      new data before another scan.
    - Nothing commits here; the API boundary owns the transaction.
    """
    autopilot.get_merchant(db, merchant_id)

    current = autopilot.focus_opportunity(db, merchant_id)
    latest_before = _latest_opportunity(db, merchant_id)

    if current is None:
        return _detect_if_revision_ready(db, merchant_id)

    transition = autopilot.resolve_transition(db, merchant_id)
    if transition.action not in RESTARTABLE_ACTIONS or not _is_restartable(db, transition):
        raise autopilot.InvalidTransitionError(
            "The current optimization cycle is still in progress and cannot be closed."
        )

    if transition.action in {autopilot.ACTION_BLOCKED, autopilot.ACTION_DEPLOY}:
        _cancel_undeployed_experiment(db, transition.experiment)

    current.status = "resolved"
    db.flush()

    readiness = detection_readiness(db, merchant_id)
    if readiness.ready:
        # A changed historical revision supersedes every untouched opportunity
        # from the prior pass. ``latest_before`` is the newest row from that
        # pass, so resolving through its timestamp retires the whole snapshot.
        return _detect_if_revision_ready(
            db,
            merchant_id,
            stale_before=(latest_before.created_at if latest_before is not None else None),
        )

    # No new evidence: another waiting opportunity from the same detector pass
    # remains legitimate. Once those are exhausted, stop and wait for data.
    next_focus = autopilot.focus_opportunity(db, merchant_id)
    if next_focus is not None:
        return next_focus
    return None
