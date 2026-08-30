"""Schemas for fixed-horizon statistical experiment evaluation."""
from typing import Literal
from pydantic import BaseModel, ConfigDict

class StatisticalEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    experiment_id: str
    control_count: int
    treatment_count: int
    control_conversions: int
    treatment_conversions: int
    control_rate: float
    treatment_rate: float
    absolute_lift: float
    relative_lift: float | None
    p_value: float
    confidence_interval_lower: float
    confidence_interval_upper: float
    is_significant: bool
    decision: Literal["KEEP", "ROLLBACK", "INCONCLUSIVE"]
