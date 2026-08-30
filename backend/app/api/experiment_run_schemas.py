"""Closed dashboard response for Task 21C one-click experiments."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class OneClickExperimentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    experiment_id: str
    generated_attempts: int
    runtime_batches: int
    control_attempts: int
    treatment_attempts: int
    sample_target_per_variant: int
    decision: Literal["KEEP", "ROLLBACK", "INCONCLUSIVE"]
    absolute_lift: float | None
    p_value: float | None
