"""Strict request/response models for the FastAPI product API (Task 15).

These models are the only shape the dashboard may ever see. Rules enforced
here, at the edge of the system:

- ``extra="forbid"`` on every model: unknown fields are rejected rather than
  silently passed through, so nothing can ride along on a request or a
  response that this file did not decide to expose.
- Raw ORM rows are never serialised. Each response model is an explicit
  projection of persisted, merchant-visible fields.
- No credentials. The only externally visible Razorpay value is the public
  resource id (``plink_...``/``offer_...``); key ids and secrets never appear.
- No hidden causal information and no chain-of-thought: free-form internal
  payloads (opportunity evidence) pass through the Task 14 audit sanitizer,
  which drops secret-shaped and causal/hidden keys.
- The Autopilot vocabulary (step / state / next action) is a closed
  ``Literal`` set so the UI can rely on stable values.

This module deliberately contains no business logic and no database access.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.audit import sanitize_audit_data

# ---------------------------------------------------------------------------
# Stable Autopilot vocabulary
#
# ``step``        what just happened (see app.services.autopilot)
# ``state``       where the merchant now sits in the lifecycle
# ``next_action`` what a single further call/request would do next
# ---------------------------------------------------------------------------

AutopilotStepName = Literal[
    "OPPORTUNITY_DETECTED",
    "HYPOTHESIS_PROPOSED",
    "EXPERIMENT_PLANNED",
    "POLICY_APPROVED",
    "POLICY_REJECTED",
    "RESOURCE_DEPLOYED",
    "DEPLOYMENT_BLOCKED",
    "EXPERIMENT_BATCH_RUN",
    "EXPERIMENT_EVALUATED",
    "RESOURCE_ROLLED_BACK",
    "COMPLETED",
    "NO_ACTION",
]

AutopilotState = Literal[
    "IDLE",
    "HYPOTHESIS_PENDING",
    "EXPERIMENT_PENDING",
    "POLICY_REVIEW_PENDING",
    "DEPLOYMENT_PENDING",
    "DEPLOYMENT_BLOCKED",
    "POLICY_REJECTED",
    "RUNNING",
    "EVALUATION_PENDING",
    "COMPLETED",
]

AutopilotNextAction = Literal[
    "DETECT_OPPORTUNITIES",
    "DIAGNOSE_OPPORTUNITY",
    "PLAN_EXPERIMENT",
    "EVALUATE_POLICY",
    "DEPLOY_TREATMENT",
    "CONFIGURE_OFFER_MAPPING",
    "RUN_EXPERIMENT_BATCH",
    "EVALUATE_EXPERIMENT",
    "ROLLBACK_TREATMENT",
    "STOP",
    "DONE",
]

AutopilotEntityType = Literal["merchant", "opportunity", "hypothesis", "experiment"]

ExperimentStatus = Literal[
    "proposed",
    "approved",
    "running",
    "rejected",
    "completed",
    "rolled_back",
    "cancelled",
]


class ApiModel(BaseModel):
    """Base for every API model: closed shape, ORM/dataclass readable."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)


# ---------------------------------------------------------------------------
# Merchant / metrics
# ---------------------------------------------------------------------------


class MerchantSummary(ApiModel):
    merchant_id: str
    name: str
    category: str | None = None
    #: Stored in paise (integer subunits); the API never converts or inflates.
    monthly_gmv_paise: int | None = None
    created_at: datetime | None = None


class ConversionMetricsResponse(ApiModel):
    """Overall conversion metrics straight from the Task 07 metric engine."""

    attempts: int
    captured: int
    failed: int
    abandoned: int
    conversion_rate: float | None = None


class SegmentMetricsResponse(ApiModel):
    """Observable per-segment metrics, projected from the Task 07 engine.

    Plain counts and ratios over ``PaymentAttempt`` rows only. The frontend
    sorts and displays these itself; no segment is pre-labelled "weakest" and
    no trend or revenue-at-risk value is invented.
    """

    segment: str
    attempts: int
    captured: int
    failed: int
    abandoned: int
    conversion_rate: float | None = None
    #: Stored in paise (integer subunits); the API never converts or inflates.
    gmv_paise: int
    captured_gmv_paise: int
    average_captured_order_value_paise: float | None = None


class PaymentMethodMetricsResponse(ApiModel):
    """Observable per-payment-method metrics from the Task 07 engine.

    ``success_rate`` keeps the engine's own observable name on purpose: it is
    captured/attempted, not an AI-derived or causal quantity.
    """

    payment_method: str
    attempts: int
    captured: int
    failed: int
    abandoned: int
    success_rate: float | None = None


# ---------------------------------------------------------------------------
# Lifecycle entities
# ---------------------------------------------------------------------------


class OpportunityResponse(ApiModel):
    id: str
    merchant_id: str
    type: str
    segment: str | None = None
    severity: float
    detected_metric: str
    detected_value: float | None = None
    baseline_value: float | None = None
    status: str
    created_at: datetime
    #: Observable evidence built by the detector, run through the audit
    #: sanitizer as a defence-in-depth guard against secrets/causal keys.
    evidence: dict[str, Any] = Field(default_factory=dict)

    @field_validator("evidence", mode="before")
    @classmethod
    def _sanitize_evidence(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("opportunity evidence must be an object")
        return sanitize_audit_data(value)


class HypothesisResponse(ApiModel):
    id: str
    opportunity_id: str
    merchant_id: str
    ai_model: str | None = None
    hypothesis_text: str
    intervention_type: str
    intervention_params: dict[str, Any] = Field(default_factory=dict)
    confidence: str | None = None
    #: Short, merchant-visible explanation. Never chain-of-thought.
    reasoning_summary: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    status: str
    created_at: datetime


class ExperimentResponse(ApiModel):
    id: str
    merchant_id: str
    hypothesis_id: str
    opportunity_id: str
    name: str
    segment: str
    intervention_type: str
    #: Canonical semantic configs, never Razorpay API payloads.
    control_config: dict[str, Any] = Field(default_factory=dict)
    treatment_config: dict[str, Any] = Field(default_factory=dict)
    traffic_split_treatment_pct: float
    primary_metric: str
    guardrail_metrics: list[str] = Field(default_factory=list)
    min_sample_per_variant: int
    max_duration_hours: int
    status: ExperimentStatus
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime


class ExperimentProgressResponse(ApiModel):
    """Observable runtime progress toward the fixed sample horizon."""

    experiment_id: str
    control_attempts: int
    treatment_attempts: int
    sample_target_per_variant: int
    control_remaining: int
    treatment_remaining: int
    sample_target_reached: bool


class PolicyDecisionResponse(ApiModel):
    id: str
    experiment_id: str
    merchant_id: str
    decision: Literal["APPROVE", "REJECT"]
    violations: list[str] = Field(default_factory=list)
    original_params: dict[str, Any] = Field(default_factory=dict)
    final_params: dict[str, Any] | None = None
    evaluated_at: datetime


class ExperimentResultResponse(ApiModel):
    """Persisted statistical decision. Computed only by the stats engine."""

    experiment_id: str
    control_count: int
    treatment_count: int
    control_conversions: int
    treatment_conversions: int
    control_rate: float | None = None
    treatment_rate: float | None = None
    absolute_lift: float | None = None
    relative_lift: float | None = None
    p_value: float | None = None
    confidence_interval_lower: float | None = None
    confidence_interval_upper: float | None = None
    is_significant: bool | None = None
    decision: Literal["KEEP", "ROLLBACK", "INCONCLUSIVE"] | None = None
    decided_at: datetime | None = None


class RazorpayResourceResponse(ApiModel):
    """Public identity of a real Razorpay Test Mode resource.

    ``config`` internals and every credential-shaped value stay server-side.
    """

    id: str
    experiment_id: str | None = None
    variant: str | None = None
    resource_type: str
    razorpay_id: str
    status: Literal["active", "cancelled"]
    created_at: datetime


class ExperimentRunResponse(ApiModel):
    """One simulated traffic batch (Task 11 runtime summary)."""

    experiment_id: str
    generated_attempts: int
    control_attempts: int
    treatment_attempts: int
    control_captured: int
    treatment_captured: int
    total_assignments: int
    sample_target_per_variant: int
    control_remaining: int
    treatment_remaining: int
    status: ExperimentStatus


class AuditEventResponse(ApiModel):
    """One tamper-evident lifecycle event (Task 14)."""

    id: str
    event_type: str
    actor: str
    entity_type: str | None = None
    entity_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    prev_hash: str | None = None
    event_hash: str | None = None

    @field_validator("data", mode="before")
    @classmethod
    def _sanitize_data(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("audit event data must be an object")
        return sanitize_audit_data(value)


class MerchantPolicyPublicResponse(ApiModel):
    """Merchant-safe guardrail limits from the persisted policy row.

    Lets a detail page show "proposed 20% against a configured maximum of
    15%" without inventing the maximum or parsing prose. This is a read-only
    projection of what the Task 10 engine already enforced; nothing here is
    re-derived, and no threshold is computed at this layer. The internal row
    id and timestamps are not merchant-visible; the policy row holds no
    secrets.
    """

    merchant_id: str
    max_experiment_exposure_pct: float
    max_discount_pct: float
    min_margin_pct: float
    max_concurrent_experiments: int
    max_experiment_duration_hours: int
    min_sample_size: int
    max_financial_exposure: int
    #: Fail closed: a persisted value that is not a proper list-like
    #: collection of intervention names becomes ``[]``; nothing is invented.
    allowed_interventions: list[str]

    @field_validator("allowed_interventions", mode="before")
    @classmethod
    def _fail_closed_interventions(cls, value: Any) -> list[str]:
        if isinstance(value, str) or not isinstance(value, (list, tuple)):
            return []
        return [item for item in value if isinstance(item, str)]


class AutopilotCycleResponse(ApiModel):
    """One complete persisted lifecycle, ready for a detail-page refresh.

    Every stage that has not happened yet is explicitly ``None``, so the
    response works at any point of the lifecycle (opportunity only, diagnosed,
    planned, approved, rejected, deployed, running, completed). The
    relationships are read from persisted foreign keys, never reconstructed
    from audit strings. Only merchant-visible projections appear: no
    operation-execution payloads, no OpenAI prompts, no secrets and no raw
    Razorpay API responses.
    """

    opportunity: OpportunityResponse
    hypothesis: HypothesisResponse | None = None
    experiment: ExperimentResponse | None = None
    policy_decision: PolicyDecisionResponse | None = None
    merchant_policy: MerchantPolicyPublicResponse | None = None
    razorpay_resource: RazorpayResourceResponse | None = None
    progress: ExperimentProgressResponse | None = None
    result: ExperimentResultResponse | None = None
    audit_events: list[AuditEventResponse] = Field(default_factory=list)
    audit_chain_valid: bool


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class RunBatchRequest(ApiModel):
    """Optional body for ``POST /experiments/{id}/run``.

    Bounds are structural only. The semantic limits that belong to the runtime
    (maximum batch size, allowed statuses, supported interventions) stay in
    Task 11 and are enforced there; the API maps their errors, it does not
    reimplement them. Booleans are refused rather than silently coerced to 1.
    """

    batch_size: int = Field(default=500, gt=0)
    seed: int = Field(default=20260827)

    @field_validator("batch_size", "seed", mode="before")
    @classmethod
    def _refuse_bools(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("batch_size and seed must be real integers")
        return value


# ---------------------------------------------------------------------------
# Aggregate read models
# ---------------------------------------------------------------------------


class AutopilotStatusResponse(ApiModel):
    """Latest lifecycle summary for one merchant.

    ``latest_decision`` is the persisted policy verdict (authorization);
    ``latest_statistical_decision`` is the experiment's KEEP/ROLLBACK/
    INCONCLUSIVE outcome. There is deliberately no "revenue recovered" field:
    nothing in the backend invents that number.
    """

    merchant_id: str
    opportunity_count: int
    experiment_count: int
    active_opportunity_count: int
    active_experiment_count: int
    latest_opportunity_id: str | None = None
    latest_experiment_id: str | None = None
    latest_experiment_status: ExperimentStatus | None = None
    latest_decision: Literal["APPROVE", "REJECT"] | None = None
    latest_statistical_decision: (
        Literal["KEEP", "ROLLBACK", "INCONCLUSIVE"] | None
    ) = None
    latest_resource_status: Literal["active", "cancelled", "none"] = "none"
    state: AutopilotState
    next_action: AutopilotNextAction | None = None
    audit_chain_valid: bool
    progress: ExperimentProgressResponse | None = None


class MerchantOverviewResponse(ApiModel):
    """Command Center payload for a single merchant."""

    merchant: MerchantSummary
    metrics: ConversionMetricsResponse
    segment_metrics: list[SegmentMetricsResponse]
    payment_method_metrics: list[PaymentMethodMetricsResponse]
    attempted_gmv_paise: int
    captured_gmv_paise: int
    active_opportunity_count: int
    active_experiment_count: int
    latest_experiment: ExperimentResponse | None = None
    latest_result: ExperimentResultResponse | None = None
    audit_chain_valid: bool
    autopilot_status: AutopilotStatusResponse


class AutopilotStepResponse(ApiModel):
    """Outcome of exactly one Autopilot transition."""

    merchant_id: str
    step: AutopilotStepName
    entity_type: AutopilotEntityType | None = None
    entity_id: str | None = None
    #: Concise, merchant-visible explanation. No chain-of-thought.
    message: str
    status: AutopilotState
    next_action: AutopilotNextAction | None = None


class ExperimentRollbackResponse(ApiModel):
    experiment_id: str
    status: Literal["rolled_back", "no_active_resource"]
    resource: RazorpayResourceResponse | None = None


class ApiErrorResponse(ApiModel):
    """Deterministic error envelope: a stable code plus a safe message."""

    code: str
    message: str


__all__ = [
    "ApiErrorResponse",
    "ApiModel",
    "AuditEventResponse",
    "AutopilotCycleResponse",
    "AutopilotEntityType",
    "AutopilotNextAction",
    "AutopilotState",
    "AutopilotStatusResponse",
    "AutopilotStepName",
    "AutopilotStepResponse",
    "ConversionMetricsResponse",
    "ExperimentProgressResponse",
    "ExperimentResponse",
    "ExperimentResultResponse",
    "ExperimentRollbackResponse",
    "ExperimentRunResponse",
    "ExperimentStatus",
    "HypothesisResponse",
    "MerchantOverviewResponse",
    "MerchantPolicyPublicResponse",
    "MerchantSummary",
    "OpportunityResponse",
    "PaymentMethodMetricsResponse",
    "PolicyDecisionResponse",
    "RazorpayResourceResponse",
    "RunBatchRequest",
    "SegmentMetricsResponse",
]
