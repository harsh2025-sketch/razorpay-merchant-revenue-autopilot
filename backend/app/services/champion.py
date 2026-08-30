"""Deterministic champion/challenger state derived from statistical KEEP results.

Champion state intentionally owns no mutable database row. Completed
Experiment rows already freeze the exact control/treatment configuration that
was tested, and ExperimentResult owns the statistical decision. A KEEP result
therefore has enough persisted truth to promote that treatment deterministically.

The baseline merchant configuration is champion version 1. Every KEEP advances
one version and replaces the champion only for that intervention type. ROLLBACK
and INCONCLUSIVE results never mutate the derived champion.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Experiment, ExperimentResult, Merchant


class ChampionStateError(Exception):
    """Base error for deterministic champion-state reads."""


class ChampionMerchantNotFoundError(ChampionStateError):
    """The requested merchant does not exist."""


@dataclass(frozen=True)
class ChampionConfig:
    intervention_type: str
    config: dict[str, Any]
    source_experiment_id: str
    promoted_at: datetime
    absolute_lift: float
    p_value: float


@dataclass(frozen=True)
class MerchantChampionState:
    merchant_id: str
    version: int
    promotion_count: int
    configs: tuple[ChampionConfig, ...]
    latest_promotion_experiment_id: str | None

    def config_for(self, intervention_type: str) -> ChampionConfig | None:
        return next(
            (row for row in self.configs if row.intervention_type == intervention_type),
            None,
        )


def get_merchant_champion_state(db: Session, merchant_id: str) -> MerchantChampionState:
    """Reconstruct the current merchant champion entirely from persisted KEEP results."""
    if db.get(Merchant, merchant_id) is None:
        raise ChampionMerchantNotFoundError(f"Merchant not found: {merchant_id!r}")

    rows = (
        db.query(Experiment, ExperimentResult)
        .join(ExperimentResult, ExperimentResult.experiment_id == Experiment.id)
        .filter(
            Experiment.merchant_id == merchant_id,
            ExperimentResult.decision == "KEEP",
        )
        .order_by(
            ExperimentResult.decided_at.asc(),
            Experiment.created_at.asc(),
            Experiment.id.asc(),
        )
        .all()
    )

    by_intervention: dict[str, ChampionConfig] = {}
    latest_experiment_id: str | None = None
    for experiment, result in rows:
        by_intervention[experiment.intervention_type] = ChampionConfig(
            intervention_type=experiment.intervention_type,
            config=dict(experiment.treatment_config or {}),
            source_experiment_id=experiment.id,
            promoted_at=result.decided_at,
            absolute_lift=float(result.absolute_lift),
            p_value=float(result.p_value),
        )
        latest_experiment_id = experiment.id

    configs = tuple(by_intervention[key] for key in sorted(by_intervention))
    return MerchantChampionState(
        merchant_id=merchant_id,
        version=1 + len(rows),
        promotion_count=len(rows),
        configs=configs,
        latest_promotion_experiment_id=latest_experiment_id,
    )


def champion_control_config(
    db: Session,
    *,
    merchant_id: str,
    intervention_type: str,
    fallback_control: dict[str, Any],
) -> tuple[dict[str, Any], int, str | None]:
    """Return the current control configuration for a new challenger.

    Returns ``(control_config, champion_version, source_experiment_id)``. When
    the merchant has never kept a treatment of this intervention type, the
    canonical planner fallback remains the control.
    """
    state = get_merchant_champion_state(db, merchant_id)
    champion = state.config_for(intervention_type)
    if champion is None:
        return dict(fallback_control), state.version, None
    return dict(champion.config), state.version, champion.source_experiment_id


def is_identical_to_current_champion(
    db: Session,
    *,
    merchant_id: str,
    intervention_type: str,
    challenger_config: dict[str, Any],
) -> bool:
    """Whether the challenger is identical to an already-promoted config."""
    state = get_merchant_champion_state(db, merchant_id)
    champion = state.config_for(intervention_type)
    return champion is not None and champion.config == challenger_config
