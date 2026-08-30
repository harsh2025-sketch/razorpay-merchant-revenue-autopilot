"""Experiment plan schema (Task 09).

Strict Pydantic models describing the deterministic output of the experiment
planner: the canonical, semantic experiment structure derived from a validated
persisted Hypothesis.

Boundary rules for this schema:
- Traffic allocation, primary metric, guardrails, sample targets, and
  duration are code-controlled planning defaults - never chosen by the LLM.
- Configs are SEMANTIC control/treatment definitions. They are NOT raw
  Razorpay API payloads (no ``options``/``checkout`` keys, no offer IDs).
- MerchantPolicy approval happens later (Task 10); this schema deliberately
  carries no policy decision, no approval flag, no Razorpay identifiers,
  no p-values, no winner, and no expected lift.

This module must never import the simulation/causal layer, the Razorpay
service, or the OpenAI client.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.hypothesis import AllowedInterventionType

# ---------------------------------------------------------------------------
# Deterministic planning defaults
#
# These are planning defaults only. MerchantPolicy may later override or
# reject them in Task 10 - the planner must NOT consult policy here.
# ---------------------------------------------------------------------------

#: Fraction of traffic exposed to the treatment variant (control gets the rest).
DEFAULT_TREATMENT_EXPOSURE: float = 0.10

#: The single primary metric; the AI never chooses it.
DEFAULT_PRIMARY_METRIC: Literal["conversion_rate"] = "conversion_rate"

#: Guardrail metric labels for future runtime/statistical evaluation.
DEFAULT_GUARDRAILS: tuple[str, ...] = (
    "captured_gmv",
    "failure_rate",
    "abandonment_rate",
)

#: Maximum experiment duration in hours.
DEFAULT_MAX_DURATION_HOURS: int = 72

#: Minimum sample per variant. Deliberately NOT a formal power analysis -
#: the statistical engine arrives in Task 11.
DEFAULT_MIN_SAMPLE_PER_VARIANT: int = 200

#: The only intervention universe the planner understands (same as Task 08).
AllowedExperimentIntervention = AllowedInterventionType


# ---------------------------------------------------------------------------
# Experiment plan
# ---------------------------------------------------------------------------


class ExperimentPlan(BaseModel):
    """Deterministic experiment structure derived from one Hypothesis.

    Extra fields are forbidden so that policy decisions, Razorpay resource
    IDs, and statistical results can never sneak into a plan.
    """

    model_config = ConfigDict(extra="forbid")

    merchant_id: str = Field(..., min_length=1)
    hypothesis_id: str = Field(..., min_length=1)
    opportunity_id: str = Field(..., min_length=1)

    name: str = Field(..., min_length=1)
    segment: str = Field(..., min_length=1)

    intervention_type: AllowedExperimentIntervention

    #: Canonical semantic control configuration (never a Razorpay payload).
    control_config: dict[str, object]
    #: Canonical semantic treatment configuration (never a Razorpay payload).
    treatment_config: dict[str, object]

    #: Treatment traffic fraction (0-1); control implicitly receives the rest.
    traffic_split_treatment_pct: float = Field(..., gt=0, le=1)

    primary_metric: Literal["conversion_rate"]

    guardrail_metrics: list[str]

    min_sample_per_variant: int = Field(..., gt=0)

    max_duration_hours: int = Field(..., gt=0)
