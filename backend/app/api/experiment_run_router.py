"""Task 21C dashboard route for one-click fixed-horizon experiments.

This route is intentionally additive and excluded from the frozen Task 15
OpenAPI surface. It coordinates only the already-authorized runtime/statistics
portion of the lifecycle; policy and Razorpay deployment remain separate
explicit steps.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.experiment_run_schemas import OneClickExperimentResponse
from app.db.session import get_db
from app.engines.statistics import StatisticalEvaluationError
from app.services.one_click_experiment import (
    OneClickExperimentError,
    run_experiment_to_decision,
)
from app.simulation.runner import ExperimentRuntimeError


logger = logging.getLogger("app.api.experiment_run")
router = APIRouter(prefix="/api/v1", tags=["experiments"])


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _safe_message(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:300]


@router.post(
    "/experiments/{experiment_id}/run-to-decision",
    response_model=OneClickExperimentResponse,
    include_in_schema=False,
)
def run_experiment_to_fixed_horizon(
    experiment_id: str,
    db: Session = Depends(get_db),
) -> OneClickExperimentResponse:
    """Run authorized simulated traffic to horizon and evaluate once."""
    try:
        result = run_experiment_to_decision(db, experiment_id)
        payload = OneClickExperimentResponse.model_validate(result)
        db.commit()
        return payload
    except OneClickExperimentError as exc:
        db.rollback()
        raise _error(409, "EXPERIMENT_RUN_BLOCKED", _safe_message(exc)) from None
    except ExperimentRuntimeError as exc:
        db.rollback()
        raise _error(422, "EXPERIMENT_RUNTIME_FAILED", _safe_message(exc)) from None
    except StatisticalEvaluationError as exc:
        db.rollback()
        raise _error(422, "EXPERIMENT_EVALUATION_FAILED", _safe_message(exc)) from None
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("unexpected one-click experiment failure")
        raise _error(
            500,
            "EXPERIMENT_RUN_FAILED",
            "The experiment could not be completed.",
        ) from None


__all__ = ["router"]
