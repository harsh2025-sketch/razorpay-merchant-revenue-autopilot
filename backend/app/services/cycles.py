"""Explicit lifecycle rollover for repeatable Autopilot optimization cycles.

A completed, rejected, or safely abandoned pre-deployment cycle remains fully
persisted. Starting another cycle never deletes payment attempts, experiments,
results, resources, or audit history. Instead this module closes the current
opportunity, cancels an approved experiment only when no treatment resource was
created, then resumes another already-detected opportunity or runs the existing
deterministic detector again.

This is deliberately separate from ``app.services.autopilot`` so the audited
one-step orchestration and all Tasks 07-15 decision boundaries remain unchanged.
The existing append-only audit chain is never rewritten or truncated by a
rollover; fresh detector/lifecycle events continue extending it normally.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Experiment, Opportunity
from app.engines.opportunities import run_opportunity_detection
from app.services import autopilot


RESTARTABLE_ACTIONS = frozenset(
    {
        autopilot.ACTION_STOP,
        autopilot.ACTION_BLOCKED,
        autopilot.ACTION_DEPLOY,
        autopilot.ACTION_DONE,
    }
)


def _is_restartable(db: Session, transition: Any) -> bool:
    """Return whether closing the current cycle is safe.

    ``DONE`` is accepted only for a genuinely terminal experiment. ``DEPLOY``
    is accepted only before any treatment resource exists, which covers the
    durable state left after a fail-closed deployment-blocked attempt. Runtime,
    evaluation, and rollback states can never be skipped.
    """
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
    """Close an approved experiment that never created a treatment resource.

    Leaving such an experiment ``approved`` would make it count as concurrently
    active forever. An explicit user-requested rollover therefore marks it
    ``cancelled``. No Razorpay cancellation is attempted because the caller is
    allowed here only when no treatment resource exists.
    """
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


def start_new_cycle(db: Session, merchant_id: str) -> Opportunity | None:
    """Close the terminal/safely-undeployed focus cycle and return the next one.

    Rules:
    - Runtime, evaluation, and rollback work can never be skipped.
    - Completed/rejected cycles are preserved and their opportunity becomes
      ``resolved``.
    - An approved experiment with no created treatment resource can be
      explicitly abandoned and is marked ``cancelled`` so policy concurrency
      stays truthful. This is the durable state seen after deployment is
      blocked by missing/unsupported resource configuration.
    - If another previously detected active opportunity exists, it becomes the
      next focus without creating a duplicate.
    - Otherwise the existing deterministic detector is run again against the
      merchant's current observable payment data.
    - Nothing commits here; the API boundary owns the transaction.
    """
    autopilot.get_merchant(db, merchant_id)

    current = autopilot.focus_opportunity(db, merchant_id)
    if current is None:
        run_opportunity_detection(db, merchant_id)
        return autopilot.focus_opportunity(db, merchant_id)

    transition = autopilot.resolve_transition(db, merchant_id)
    if transition.action not in RESTARTABLE_ACTIONS or not _is_restartable(db, transition):
        raise autopilot.InvalidTransitionError(
            "The current optimization cycle is still in progress and cannot be closed."
        )

    if transition.action in {autopilot.ACTION_BLOCKED, autopilot.ACTION_DEPLOY}:
        _cancel_undeployed_experiment(db, transition.experiment)

    current.status = "resolved"
    db.flush()

    # Prefer another already-detected opportunity from the same observation
    # pass before scanning again. This avoids manufacturing duplicates when
    # the detector originally surfaced more than one valid segment.
    next_focus = autopilot.focus_opportunity(db, merchant_id)
    if next_focus is not None:
        return next_focus

    run_opportunity_detection(db, merchant_id)
    return autopilot.focus_opportunity(db, merchant_id)
