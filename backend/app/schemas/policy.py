from typing import Any, Literal
from pydantic import BaseModel, ConfigDict

class PolicyEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["APPROVE", "REJECT"]
    violations: list[str]
    original_params: dict[str, Any]
    final_params: dict[str, Any] | None
