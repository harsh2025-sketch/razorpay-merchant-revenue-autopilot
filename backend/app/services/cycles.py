"""Explicit lifecycle rollover for repeatable Autopilot optimization cycles.

A completed, rejected, or safely abandoned pre-deployment cycle remains fully
persisted. Starting another cycle never deletes payment attempts, experiments,
results, resources, or audit history. Instead this module closes the current
opportunity, cancels an approved experiment only when no treatment resource was
created, then resumes another already-started opportunity or selects the best
untouched candidate through the deterministic opportunity portfolio.

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
from app.services.portfolio import build_opportunity_portfolio


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


def _next_portfolio_focus(db: Session, merchant_id: str) -> Opportunity | None:
    """Resume started work first; otherwise choose the ranked untouched candidate."""
    focus = autopilot.focus_opportunity(db, merchant_id)
    if focus is None:
        return None

    # ``focus_opportunity`` already prefers the active opportunity furthest
    # along the pipeline. If it has entered diagnosis, never re-rank it behind
    # a new candidate; interruption-safe resume remains the stronger invariant.
    if autopilot.latest_hypothesis(db, focus.id) is not None:
        return focus

    portfolio = build_opportunity_portfolio(db, merchant_id)
    if portfolio.next_best_opportunity_id is None:
        # Missing/empty policy should not make the existing lifecycle vanish.
        # Return the old deterministic focus so the normal policy/configuration
        # boundary can surface the problem explicitly.
        return focus
    return db.get(Opportunity, portfolio.next_best_opportunity_id) or focus


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
    - If another already-started active opportunity exists, it is resumed
      before any untouched candidate.
    - Untouched active opportunities are selected by the deterministic Task 19B
      portfolio instead of detector severity alone.
    - If no active candidate remains, the existing deterministic detector runs
      again and its untouched results are ranked by the same portfolio.
    - Nothing commits here; the API boundary owns the transaction.
    """
    autopilot.get_merchant(db, merchant_id)

    current = autopilot.focus_opportunity(db, merchant_id)
    if current is None:
        run_opportunity_detection(db, merchant_id)
        return _next_portfolio_focus(db, merchant_id)

    transition = autopilot.resolve_transition(db, merchant_id)
    if transition.action not in RESTARTABLE_ACTIONS or not _is_restartable(db, transition):
        raise autopilot.InvalidTransitionError(
            "The current optimization cycle is still in progress and cannot be closed."
        )

    if transition.action in {autopilot.ACTION_BLOCKED, autopilot.ACTION_DEPLOY}:
        _cancel_undeployed_experiment(db, transition.experiment)

    current.status = "resolved"
    db.flush()

    # Prefer another active opportunity from the same observation pass before
    # scanning again. Started work always resumes; untouched work is portfolio-
    # ranked so experiment traffic goes to the highest current candidate.
    next_focus = _next_portfolio_focus(db, merchant_id)
    if next_focus is not None:
        return next_focus

    run_opportunity_detection(db, merchant_id)
    return _next_portfolio_focus(db, merchant_id)
