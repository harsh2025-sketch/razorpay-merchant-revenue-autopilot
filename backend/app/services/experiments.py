"""Experiment runtime service (Task 11).

Delegates execution to the simulation runner. This module must not import
the sealed causal model or expose hidden effect values.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.simulation.runner import (
    ExperimentRunSummary,
    ExperimentRuntimeError,
    assign_variant,
    run_experiment_batch,
)

__all__ = [
    "ExperimentRunSummary",
    "ExperimentRuntimeError",
    "assign_variant",
    "run_experiment_batch",
]


def execute_experiment_batch(
    db: Session,
    experiment_id: str,
    *,
    batch_size: int = 100,
    seed: int = 20260827,
) -> ExperimentRunSummary:
    """Run one simulated traffic batch for an approved/running experiment."""
    return run_experiment_batch(
        db,
        experiment_id,
        batch_size=batch_size,
        seed=seed,
    )
