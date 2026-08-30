"""Autopilot orchestration service (Task 15).

One deterministic place that knows the *order* of the Revenue Autopilot
lifecycle and nothing else. Every decision is delegated:

::

    detector            -> Opportunity          (Task 07)
    AI diagnosis        -> Hypothesis          (Task 08)
    planner             -> Experiment          (Task 09)
    policy engine       -> APPROVE / REJECT    (Task 10)
    runtime             -> simulated traffic    (Task 11)
    statistics engine   -> KEEP / ROLLBACK / INCONCLUSIVE   (Task 12)
    executor            -> real Razorpay Test resource      (Task 13)
    audit service       -> lifecycle history                (Task 14)

Deliberate boundaries of this module:

- No business logic is duplicated here: no policy math, no statistics, no
  Razorpay payloads, no hashing, no prompt building.
- ``advance_autopilot`` performs **at most one** meaningful lifecycle
  transition per call so the UI can show the system detecting, authorizing
  and executing step by step instead of hiding it behind one request.
- Nothing is committed. Domain functions ``flush``; the API boundary owns
  ``commit``/``rollback`` for the whole top-level operation.
- The sealed causal/simulation model is never imported. Experimental traffic
  comes in only through the Task 11 runtime service.
- No invented revenue-loss estimates, no automatic AI re-planning: an
  explicit policy rejection stops the lifecycle at ``POLICY_REJECTED``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.db.models import (
    Experiment,
    ExperimentResult,
    Hypothesis,
    Merchant,
    MerchantPolicy,
    Opportunity,
    PaymentAttempt,
    PolicyDecision,
    RazorpayResource,
)
from app.engines.diagnosis import diagnose_opportunity
from app.engines.metrics import (
    get_overall_metrics,
    get_payment_method_metrics,
    get_segment_metrics,
)
from app.engines.opportunities import ACTIVE_STATUSES, run_opportunity_detection
from app.engines.planner import plan_experiment
from app.engines.policy import evaluate_experiment_policy
from app.engines.statistics import evaluate_experiment_results
from app.services.audit import (
    ENTITY_EXPERIMENT,
    ENTITY_HYPOTHESIS,
    ENTITY_OPPORTUNITY,
    get_experiment_audit_history,
    get_merchant_audit_history,
    verify_merchant_audit_chain,
)
from app.services.experiments import execute_experiment_batch
from app.services.executor import (
    ExperimentExecutionConfigurationError,
    deploy_experiment_treatment,
    rollback_experiment_treatment,
)
from app.services.portfolio import build_opportunity_portfolio
from app.services.champion import get_merchant_champion_state
from app.services.memory import get_merchant_experiment_memory

# ---------------------------------------------------------------------------
# Lifecycle vocabulary (mirrored as Literals in app.api.schemas)
# ---------------------------------------------------------------------------

STEP_OPPORTUNITY_DETECTED = "OPPORTUNITY_DETECTED"
STEP_HYPOTHESIS_PROPOSED = "HYPOTHESIS_PROPOSED"
STEP_EXPERIMENT_PLANNED = "EXPERIMENT_PLANNED"
STEP_POLICY_APPROVED = "POLICY_APPROVED"
STEP_POLICY_REJECTED = "POLICY_REJECTED"
STEP_RESOURCE_DEPLOYED = "RESOURCE_DEPLOYED"
STEP_DEPLOYMENT_BLOCKED = "DEPLOYMENT_BLOCKED"
STEP_EXPERIMENT_BATCH_RUN = "EXPERIMENT_BATCH_RUN"
STEP_EXPERIMENT_EVALUATED = "EXPERIMENT_EVALUATED"
STEP_RESOURCE_ROLLED_BACK = "RESOURCE_ROLLED_BACK"
STEP_COMPLETED = "COMPLETED"
STEP_NO_ACTION = "NO_ACTION"

AUTOPILOT_STEPS: tuple[str, ...] = (
    STEP_OPPORTUNITY_DETECTED,
    STEP_HYPOTHESIS_PROPOSED,
    STEP_EXPERIMENT_PLANNED,
    STEP_POLICY_APPROVED,
    STEP_POLICY_REJECTED,
    STEP_RESOURCE_DEPLOYED,
    STEP_DEPLOYMENT_BLOCKED,
    STEP_EXPERIMENT_BATCH_RUN,
    STEP_EXPERIMENT_EVALUATED,
    STEP_RESOURCE_ROLLED_BACK,
    STEP_COMPLETED,
    STEP_NO_ACTION,
)

STATE_IDLE = "IDLE"
STATE_HYPOTHESIS_PENDING = "HYPOTHESIS_PENDING"
STATE_EXPERIMENT_PENDING = "EXPERIMENT_PENDING"
STATE_POLICY_REVIEW_PENDING = "POLICY_REVIEW_PENDING"
STATE_DEPLOYMENT_PENDING = "DEPLOYMENT_PENDING"
STATE_DEPLOYMENT_BLOCKED = "DEPLOYMENT_BLOCKED"
STATE_POLICY_REJECTED = "POLICY_REJECTED"
STATE_RUNNING = "RUNNING"
STATE_EVALUATION_PENDING = "EVALUATION_PENDING"
STATE_COMPLETED = "COMPLETED"

ACTION_DETECT = "DETECT_OPPORTUNITIES"
ACTION_DIAGNOSE = "DIAGNOSE_OPPORTUNITY"
ACTION_PLAN = "PLAN_EXPERIMENT"
ACTION_POLICY = "EVALUATE_POLICY"
ACTION_DEPLOY = "DEPLOY_TREATMENT"
ACTION_BLOCKED = "CONFIGURE_OFFER_MAPPING"
ACTION_RUN_BATCH = "RUN_EXPERIMENT_BATCH"
ACTION_EVALUATE = "EVALUATE_EXPERIMENT"
ACTION_ROLLBACK = "ROLLBACK_TREATMENT"
ACTION_STOP = "STOP"
ACTION_DONE = "DONE"

#: The lifecycle position implied by the next pending action. The status
#: read model and every step response share this single mapping, so the UI is
#: never told two different things about the same merchant.
STATE_BY_ACTION: dict[str, str] = {
    ACTION_DETECT: STATE_IDLE,
    ACTION_DIAGNOSE: STATE_HYPOTHESIS_PENDING,
    ACTION_PLAN: STATE_EXPERIMENT_PENDING,
    ACTION_POLICY: STATE_POLICY_REVIEW_PENDING,
    ACTION_DEPLOY: STATE_DEPLOYMENT_PENDING,
    ACTION_BLOCKED: STATE_DEPLOYMENT_BLOCKED,
    ACTION_RUN_BATCH: STATE_RUNNING,
    ACTION_EVALUATE: STATE_EVALUATION_PENDING,
    ACTION_ROLLBACK: STATE_COMPLETED,
    ACTION_STOP: STATE_POLICY_REJECTED,
    ACTION_DONE: STATE_COMPLETED,
}

#: Entity types mirror the Task 14 audit vocabulary.
ENTITY_MERCHANT = "merchant"
ENTITY_OPPORTUNITY = ENTITY_OPPORTUNITY
ENTITY_HYPOTHESIS = ENTITY_HYPOTHESIS
ENTITY_EXPERIMENT = ENTITY_EXPERIMENT

#: Demo merchant supported by P0 (there is no auth or account signup yet).
P0_MERCHANT_ID = "merchant_techbazaar"

#: Fixed seed for orchestrated traffic batches, matching the Task 11 default
#: so repeated ``advance_autopilot`` calls stay deterministic.
DEFAULT_RUNTIME_SEED = 20260827
DEFAULT_RUNTIME_BATCH_SIZE = 500

#: Open opportunity statuses: exactly the detector's own active set.
ACTIVE_OPPORTUNITY_STATUSES = tuple(sorted(ACTIVE_STATUSES))
ACTIVE_EXPERIMENT_STATUSES = ("proposed", "approved", "running")
DEPLOYABLE_EXPERIMENT_STATUSES = ("approved", "running")
TERMINAL_EXPERIMENT_STATUSES = ("completed", "rolled_back", "cancelled")
TREATMENT_VARIANT = "treatment"
PAYMENT_LINK_RESOURCE = "payment_link"

#: Audit depth of the composite cycle read model. Matches the default depth
#: of the existing audit endpoints; no new audit query framework is added.
CYCLE_AUDIT_LIMIT = 100


# ---------------------------------------------------------------------------
# Errors (small and explicit; the API layer maps them to HTTP statuses)
# ---------------------------------------------------------------------------


class AutopilotError(Exception):
    """Base error for Autopilot orchestration."""


class MerchantNotFoundError(AutopilotError):
    """The requested merchant does not exist."""


class OpportunityNotFoundError(AutopilotError):
    """The requested opportunity does not exist."""


class HypothesisNotFoundError(AutopilotError):
    """The requested hypothesis does not exist."""


class ExperimentNotFoundError(AutopilotError):
    """The requested experiment does not exist."""


class MerchantPolicyNotConfiguredError(AutopilotError):
    """The merchant has no policy row to authorize an experiment against."""


class InvalidTransitionError(AutopilotError):
    """The persisted lifecycle state does not permit the requested action."""


# ---------------------------------------------------------------------------
# Step result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AutopilotStep:
    """What one Autopilot call did, and what a further call would do."""

    merchant_id: str
    step: str
    entity_type: str | None
    entity_id: str | None
    message: str
    status: str
    next_action: str | None


@dataclass(frozen=True)
class _Transition:
    """The single next lifecycle action for a merchant."""

    action: str
    opportunity: Opportunity | None = None
    hypothesis: Hypothesis | None = None
    experiment: Experiment | None = None
    decision: PolicyDecision | None = None
    result: ExperimentResult | None = None
    resource: RazorpayResource | None = None


# ---------------------------------------------------------------------------
# Small formatting helpers for merchant-visible messages
# ---------------------------------------------------------------------------


def _ratio(value: object) -> str:
    """Format an observable ratio as a percentage, or ``n/a``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "n/a"
    return f"{value:.1%}"


def _signed_ratio(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "n/a"
    return f"{value:+.1%}"


def _number(value: object, digits: int = 4) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "n/a"
    return f"{value:.{digits}f}"


# ---------------------------------------------------------------------------
# Read models
# ---------------------------------------------------------------------------


def get_merchant(db: Session, merchant_id: str) -> Merchant:
    """Return the merchant row or raise a clear service error."""
    merchant = db.get(Merchant, merchant_id)
    if merchant is None:
        raise MerchantNotFoundError(f"Merchant not found: {merchant_id!r}")
    return merchant


def merchant_summary(db: Session, merchant_id: str) -> dict[str, Any]:
    merchant = get_merchant(db, merchant_id)
    return {
        "merchant_id": merchant.id,
        "name": merchant.name,
        "category": merchant.category,
        "monthly_gmv_paise": merchant.monthly_gmv,
        "created_at": merchant.created_at,
    }


def list_opportunities(db: Session, merchant_id: str) -> list[Opportunity]:
    """Persisted opportunities for one merchant, most relevant first.

    Deterministic ordering: severity descending, then newest first, then id
    as a stable tie-break.
    """
    get_merchant(db, merchant_id)
    return list(
        db.query(Opportunity)
        .filter(Opportunity.merchant_id == merchant_id)
        .order_by(
            Opportunity.severity.desc(),
            Opportunity.created_at.desc(),
            Opportunity.id.asc(),
        )
        .all()
    )


def get_opportunity(
    db: Session, opportunity_id: str, *, merchant_id: str | None = None
) -> Opportunity:
    opportunity = db.get(Opportunity, opportunity_id)
    if opportunity is None:
        raise OpportunityNotFoundError(f"Opportunity not found: {opportunity_id!r}")
    if merchant_id is not None and opportunity.merchant_id != merchant_id:
        # Merchant isolation: never confirm foreign rows to another merchant.
        raise OpportunityNotFoundError(
            f"Opportunity not found for merchant {merchant_id!r}: {opportunity_id!r}"
        )
    return opportunity


def get_hypothesis(
    db: Session, hypothesis_id: str, *, merchant_id: str | None = None
) -> Hypothesis:
    hypothesis = db.get(Hypothesis, hypothesis_id)
    if hypothesis is None:
        raise HypothesisNotFoundError(f"Hypothesis not found: {hypothesis_id!r}")
    if merchant_id is not None and hypothesis.merchant_id != merchant_id:
        raise HypothesisNotFoundError(
            f"Hypothesis not found for merchant {merchant_id!r}: {hypothesis_id!r}"
        )
    return hypothesis


def get_experiment(
    db: Session, experiment_id: str, *, merchant_id: str | None = None
) -> Experiment:
    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise ExperimentNotFoundError(f"Experiment not found: {experiment_id!r}")
    if merchant_id is not None and experiment.merchant_id != merchant_id:
        raise ExperimentNotFoundError(
            f"Experiment not found for merchant {merchant_id!r}: {experiment_id!r}"
        )
    return experiment


def latest_hypothesis(db: Session, opportunity_id: str) -> Hypothesis | None:
    return (
        db.query(Hypothesis)
        .filter(Hypothesis.opportunity_id == opportunity_id)
        .order_by(Hypothesis.created_at.desc(), Hypothesis.id.desc())
        .first()
    )


def latest_experiment_for_hypothesis(
    db: Session, hypothesis_id: str
) -> Experiment | None:
    return (
        db.query(Experiment)
        .filter(Experiment.hypothesis_id == hypothesis_id)
        .order_by(Experiment.created_at.desc(), Experiment.id.desc())
        .first()
    )


def latest_experiment(db: Session, merchant_id: str) -> Experiment | None:
    return (
        db.query(Experiment)
        .filter(Experiment.merchant_id == merchant_id)
        .order_by(Experiment.created_at.desc(), Experiment.id.desc())
        .first()
    )


def latest_policy_decision(db: Session, experiment_id: str) -> PolicyDecision | None:
    return (
        db.query(PolicyDecision)
        .filter(PolicyDecision.experiment_id == experiment_id)
        .order_by(PolicyDecision.evaluated_at.desc(), PolicyDecision.id.desc())
        .first()
    )


def get_result(db: Session, experiment_id: str) -> ExperimentResult | None:
    return (
        db.query(ExperimentResult)
        .filter(ExperimentResult.experiment_id == experiment_id)
        .one_or_none()
    )


def treatment_resource(db: Session, experiment_id: str) -> RazorpayResource | None:
    """The real Razorpay treatment resource created by the Task 13 executor."""
    return (
        db.query(RazorpayResource)
        .filter(
            RazorpayResource.experiment_id == experiment_id,
            RazorpayResource.resource_type == PAYMENT_LINK_RESOURCE,
            RazorpayResource.variant == TREATMENT_VARIANT,
        )
        .order_by(RazorpayResource.created_at.desc(), RazorpayResource.id.desc())
        .first()
    )


def variant_counts(db: Session, experiment_id: str) -> tuple[int, int]:
    """Observable (control, treatment) attempt counts for one experiment."""
    rows = (
        db.query(PaymentAttempt.variant, func.count(PaymentAttempt.id))
        .filter(
            PaymentAttempt.experiment_id == experiment_id,
            PaymentAttempt.variant.in_(("control", TREATMENT_VARIANT)),
        )
        .group_by(PaymentAttempt.variant)
        .all()
    )
    counts = {variant: int(count or 0) for variant, count in rows}
    return counts.get("control", 0), counts.get(TREATMENT_VARIANT, 0)


def experiment_progress(db: Session, experiment: Experiment) -> dict[str, Any]:
    """Progress toward the fixed sample horizon. No statistical inference."""
    control, treatment = variant_counts(db, experiment.id)
    target = int(experiment.min_sample_per_variant)
    return {
        "experiment_id": experiment.id,
        "control_attempts": control,
        "treatment_attempts": treatment,
        "sample_target_per_variant": target,
        "control_remaining": max(0, target - control),
        "treatment_remaining": max(0, target - treatment),
        "sample_target_reached": control >= target and treatment >= target,
    }


def gmv_totals(db: Session, merchant_id: str) -> tuple[int, int]:
    """(attempted, captured) gross merchandise value in paise.

    Direct observable aggregation over ``PaymentAttempt`` rows. The metric
    engine exposes GMV per segment only, and no revenue-loss estimate is
    derived from these numbers anywhere in this layer.
    """
    attempted = (
        db.query(func.sum(PaymentAttempt.amount))
        .filter(PaymentAttempt.merchant_id == merchant_id)
        .scalar()
    )
    captured = (
        db.query(
            func.sum(
                case((PaymentAttempt.status == "captured", PaymentAttempt.amount), else_=0)
            )
        )
        .filter(PaymentAttempt.merchant_id == merchant_id)
        .scalar()
    )
    return int(attempted or 0), int(captured or 0)


# ---------------------------------------------------------------------------
# Lifecycle position
# ---------------------------------------------------------------------------


def _opportunity_stage(db: Session, opportunity_id: str) -> int:
    """How far this opportunity has already travelled down the pipeline."""
    has_experiment = (
        db.query(Experiment.id)
        .filter(Experiment.opportunity_id == opportunity_id)
        .first()
        is not None
    )
    if has_experiment:
        return 2
    has_hypothesis = (
        db.query(Hypothesis.id)
        .filter(Hypothesis.opportunity_id == opportunity_id)
        .first()
        is not None
    )
    return 1 if has_hypothesis else 0


def focus_opportunity(db: Session, merchant_id: str) -> Opportunity | None:
    """Return the one active opportunity Autopilot should drive now.

    Started work always wins: among active opportunities, the lifecycle
    stage (experiment > hypothesis > untouched) is considered before any
    portfolio ranking so an interrupted cycle is resumed deterministically.

    Only when every candidate is untouched does Task 19B choose among them
    using the explainable opportunity portfolio. If portfolio selection is
    unavailable (for example, merchant policy is missing), the historical
    severity/newest/id ordering remains the fail-visible fallback.
    """
    candidates = (
        db.query(Opportunity)
        .filter(Opportunity.merchant_id == merchant_id)
        .filter(Opportunity.status.in_(ACTIVE_OPPORTUNITY_STATUSES))
        .order_by(
            Opportunity.severity.desc(),
            Opportunity.created_at.desc(),
            Opportunity.id.asc(),
        )
        .all()
    )
    if not candidates:
        return None

    staged = [(opportunity, _opportunity_stage(db, opportunity.id)) for opportunity in candidates]
    best_stage = max(stage for _, stage in staged)
    if best_stage > 0:
        # ``candidates`` already carries the deterministic legacy ordering.
        # Ranking never displaces work that has entered diagnosis/planning.
        return next(opportunity for opportunity, stage in staged if stage == best_stage)

    untouched = [opportunity for opportunity, stage in staged if stage == 0]
    portfolio = build_opportunity_portfolio(
        db,
        merchant_id,
        opportunities=untouched,
    )
    if portfolio.next_best_opportunity_id is not None:
        selected = next(
            (
                opportunity
                for opportunity in untouched
                if opportunity.id == portfolio.next_best_opportunity_id
            ),
            None,
        )
        if selected is not None:
            return selected

    return untouched[0]


def _experiment_transition(db: Session, experiment: Experiment) -> _Transition:
    decision = latest_policy_decision(db, experiment.id)
    result = get_result(db, experiment.id)
    resource = treatment_resource(db, experiment.id)

    if experiment.status == "proposed":
        if decision is None:
            return _Transition(ACTION_POLICY, experiment=experiment)
        if decision.decision == "REJECT":
            return _Transition(ACTION_STOP, experiment=experiment, decision=decision)
        # An APPROVE row with the experiment still 'proposed' means the policy
        # engine's own state write was lost. Nothing is deployed on top of an
        # inconsistent state; the fall-through below reports it as terminal.

    if experiment.status == "rejected" or (
        decision is not None and decision.decision == "REJECT"
    ):
        # No automatic AI replan in P0: the rejection stays visible.
        return _Transition(ACTION_STOP, experiment=experiment, decision=decision)

    if experiment.status in DEPLOYABLE_EXPERIMENT_STATUSES:
        if decision is None:
            return _Transition(ACTION_POLICY, experiment=experiment)
        if resource is None:
            return _Transition(
                ACTION_DEPLOY, experiment=experiment, decision=decision
            )
        control, treatment = variant_counts(db, experiment.id)
        target = int(experiment.min_sample_per_variant)
        if control < target or treatment < target:
            return _Transition(
                ACTION_RUN_BATCH, experiment=experiment, resource=resource
            )
        if result is None:
            return _Transition(
                ACTION_EVALUATE, experiment=experiment, resource=resource
            )
        return _Transition(
            ACTION_DONE, experiment=experiment, result=result, resource=resource
        )

    if experiment.status in TERMINAL_EXPERIMENT_STATUSES:
        if (
            result is not None
            and result.decision == "ROLLBACK"
            and resource is not None
            and resource.status == "active"
        ):
            return _Transition(
                ACTION_ROLLBACK,
                experiment=experiment,
                result=result,
                resource=resource,
            )
        return _Transition(
            ACTION_DONE, experiment=experiment, result=result, resource=resource
        )

    # Approved-but-unmarked, or an unknown status: refuse to act on an
    # inconsistent lifecycle state instead of guessing what was meant.
    return _Transition(ACTION_DONE, experiment=experiment, result=result)


def resolve_transition(db: Session, merchant_id: str) -> _Transition:
    """Determine the single next meaningful action for this merchant."""
    get_merchant(db, merchant_id)

    opportunity = focus_opportunity(db, merchant_id)
    if opportunity is None:
        return _Transition(ACTION_DETECT)

    hypothesis = latest_hypothesis(db, opportunity.id)
    if hypothesis is None:
        return _Transition(ACTION_DIAGNOSE, opportunity=opportunity)

    experiment = latest_experiment_for_hypothesis(db, hypothesis.id)
    if experiment is None:
        return _Transition(
            ACTION_PLAN, opportunity=opportunity, hypothesis=hypothesis
        )

    return _experiment_transition(db, experiment)


# ---------------------------------------------------------------------------
# Aggregate read models
# ---------------------------------------------------------------------------


def _resource_status_label(resource: RazorpayResource | None) -> str:
    if resource is None:
        return "none"
    return resource.status


def autopilot_status(db: Session, merchant_id: str) -> dict[str, Any]:
    """Summary of the latest lifecycle state for the Command Center."""
    get_merchant(db, merchant_id)

    opportunity_count = (
        db.query(func.count(Opportunity.id))
        .filter(Opportunity.merchant_id == merchant_id)
        .scalar()
    )
    active_opportunity_count = (
        db.query(func.count(Opportunity.id))
        .filter(Opportunity.merchant_id == merchant_id)
        .filter(Opportunity.status.in_(ACTIVE_OPPORTUNITY_STATUSES))
        .scalar()
    )
    experiment_count = (
        db.query(func.count(Experiment.id))
        .filter(Experiment.merchant_id == merchant_id)
        .scalar()
    )
    active_experiment_count = (
        db.query(func.count(Experiment.id))
        .filter(Experiment.merchant_id == merchant_id)
        .filter(Experiment.status.in_(ACTIVE_EXPERIMENT_STATUSES))
        .scalar()
    )
    latest_opportunity = (
        db.query(Opportunity)
        .filter(Opportunity.merchant_id == merchant_id)
        .order_by(Opportunity.created_at.desc(), Opportunity.id.desc())
        .first()
    )
    experiment = latest_experiment(db, merchant_id)
    decision = latest_policy_decision(db, experiment.id) if experiment else None
    result = get_result(db, experiment.id) if experiment else None
    resource = treatment_resource(db, experiment.id) if experiment else None
    transition = resolve_transition(db, merchant_id)

    return {
        "merchant_id": merchant_id,
        "opportunity_count": int(opportunity_count or 0),
        "experiment_count": int(experiment_count or 0),
        "active_opportunity_count": int(active_opportunity_count or 0),
        "active_experiment_count": int(active_experiment_count or 0),
        "latest_opportunity_id": latest_opportunity.id if latest_opportunity else None,
        "latest_experiment_id": experiment.id if experiment else None,
        "latest_experiment_status": experiment.status if experiment else None,
        "latest_decision": decision.decision if decision else None,
        "latest_statistical_decision": result.decision if result else None,
        "latest_resource_status": _resource_status_label(resource),
        "state": STATE_BY_ACTION[transition.action],
        "next_action": transition.action,
        "audit_chain_valid": verify_merchant_audit_chain(db, merchant_id),
        "progress": experiment_progress(db, experiment) if experiment else None,
    }


def overview(db: Session, merchant_id: str) -> dict[str, Any]:
    """Command Center payload: merchant, observable metrics, lifecycle."""
    get_merchant(db, merchant_id)

    metrics = get_overall_metrics(db, merchant_id)
    attempted_gmv, captured_gmv = gmv_totals(db, merchant_id)
    experiment = latest_experiment(db, merchant_id)
    result = get_result(db, experiment.id) if experiment else None
    active_opportunity_count = (
        db.query(func.count(Opportunity.id))
        .filter(Opportunity.merchant_id == merchant_id)
        .filter(Opportunity.status.in_(ACTIVE_OPPORTUNITY_STATUSES))
        .scalar()
    )
    active_experiment_count = (
        db.query(func.count(Experiment.id))
        .filter(Experiment.merchant_id == merchant_id)
        .filter(Experiment.status.in_(ACTIVE_EXPERIMENT_STATUSES))
        .scalar()
    )

    return {
        "merchant": merchant_summary(db, merchant_id),
        "metrics": asdict(metrics),
        # Observable breakdowns come straight from the existing Task 07
        # metric engine; no trend, weakest-segment or revenue-at-risk value
        # is derived on top of them.
        "segment_metrics": get_segment_metrics(db, merchant_id),
        "payment_method_metrics": get_payment_method_metrics(db, merchant_id),
        "attempted_gmv_paise": attempted_gmv,
        "captured_gmv_paise": captured_gmv,
        "active_opportunity_count": int(active_opportunity_count or 0),
        "active_experiment_count": int(active_experiment_count or 0),
        "latest_experiment": experiment,
        "latest_result": result,
        "audit_chain_valid": verify_merchant_audit_chain(db, merchant_id),
        "autopilot_status": autopilot_status(db, merchant_id),
    }


def merchant_intelligence(db: Session, merchant_id: str) -> dict[str, Any]:
    """Explain what Autopilot currently prioritizes and has learned.

    This is a pure read model over Tasks 19A-19C. It does not create a new
    source of truth: opportunity priority comes from the deterministic
    portfolio service, learned history comes from terminal experiment memory,
    and champion state is reconstructed only from persisted KEEP results.
    """
    get_merchant(db, merchant_id)
    portfolio = build_opportunity_portfolio(db, merchant_id)
    champion = get_merchant_champion_state(db, merchant_id)
    memory = get_merchant_experiment_memory(db, merchant_id)

    return {
        "merchant": merchant_summary(db, merchant_id),
        "portfolio": {
            "merchant_id": portfolio.merchant_id,
            "next_best_opportunity_id": portfolio.next_best_opportunity_id,
            "opportunities": [asdict(row) for row in portfolio.opportunities],
        },
        "champion": {
            "merchant_id": champion.merchant_id,
            "version": champion.version,
            "promotion_count": champion.promotion_count,
            "latest_promotion_experiment_id": champion.latest_promotion_experiment_id,
            "configs": [asdict(row) for row in champion.configs],
        },
        "memory": {
            "merchant_id": memory.merchant_id,
            "trial_count": memory.trial_count,
            "completed_result_count": memory.completed_result_count,
            "policy_rejection_count": memory.policy_rejection_count,
            "keep_count": memory.keep_count,
            "rollback_count": memory.rollback_count,
            "inconclusive_count": memory.inconclusive_count,
            "knowledge": [asdict(row) for row in memory.knowledge],
            # Memory is built oldest-first for deterministic aggregation. The
            # dashboard consumes terminal history newest-first.
            "records": [asdict(row) for row in reversed(memory.records)],
        },
    }


def merchant_audit_history(
    db: Session, merchant_id: str, *, limit: int = 100
) -> list[Any]:
    get_merchant(db, merchant_id)
    return get_merchant_audit_history(db, merchant_id, limit=limit)


def experiment_audit_history(
    db: Session, experiment_id: str, *, limit: int = 100
) -> list[Any]:
    get_experiment(db, experiment_id)
    return get_experiment_audit_history(db, experiment_id, limit=limit)


def get_merchant_policy(db: Session, merchant_id: str) -> MerchantPolicy | None:
    """The persisted merchant policy row, or ``None`` when none is configured.

    Read-only: the row is projected as-is by the API layer's public policy
    model. This layer never reads a limit to decide or compute anything.
    """
    return (
        db.query(MerchantPolicy)
        .filter(MerchantPolicy.merchant_id == merchant_id)
        .one_or_none()
    )


def opportunity_audit_history(
    db: Session,
    opportunity: Opportunity,
    hypothesis: Hypothesis | None,
    *,
    limit: int = CYCLE_AUDIT_LIMIT,
) -> list[Any]:
    """Merchant audit events tied to this opportunity's own entities.

    Used only while the lifecycle has no experiment yet: the events are the
    merchant's existing chronological history filtered to the entities this
    cycle is about (the opportunity, plus its hypothesis once one exists).
    No new audit table, query framework or filter is introduced.
    """
    relevant = {(ENTITY_OPPORTUNITY, opportunity.id)}
    if hypothesis is not None:
        relevant.add((ENTITY_HYPOTHESIS, hypothesis.id))
    return [
        event
        for event in get_merchant_audit_history(
            db, opportunity.merchant_id, limit=limit
        )
        if (event.entity_type, event.entity_id) in relevant
    ]


def get_autopilot_cycle(db: Session, opportunity_id: str) -> dict[str, Any]:
    """Read-only projection of one opportunity's complete persisted lifecycle.

    Every value comes from already-persisted rows through the existing domain
    helpers (``latest_hypothesis``, ``latest_experiment_for_hypothesis``,
    ``latest_policy_decision``, ``treatment_resource``,
    ``experiment_progress``, ``get_result``, the audit history and
    ``verify_merchant_audit_chain``). Nothing is decided, written, flushed or
    committed here, and no engine (diagnosis, planner, policy, statistics,
    runtime) or external boundary (Razorpay, OpenAI) is touched: a browser
    refresh of the detail page rebuilds exactly what the lifecycle already
    stored, at every stage, with absent stages reported as ``None``.
    """
    opportunity = get_opportunity(db, opportunity_id)
    hypothesis = latest_hypothesis(db, opportunity.id)
    experiment = (
        latest_experiment_for_hypothesis(db, hypothesis.id)
        if hypothesis is not None
        else None
    )
    decision = (
        latest_policy_decision(db, experiment.id) if experiment is not None else None
    )
    policy = get_merchant_policy(db, opportunity.merchant_id)
    resource = (
        treatment_resource(db, experiment.id) if experiment is not None else None
    )
    progress = experiment_progress(db, experiment) if experiment is not None else None
    result = get_result(db, experiment.id) if experiment is not None else None
    if experiment is not None:
        # The experiment's own trail is the complete, best-scoped history
        # once the lifecycle has reached planning.
        audit_events = get_experiment_audit_history(
            db, experiment.id, limit=CYCLE_AUDIT_LIMIT
        )
    else:
        audit_events = opportunity_audit_history(db, opportunity, hypothesis)

    return {
        "opportunity": opportunity,
        "hypothesis": hypothesis,
        "experiment": experiment,
        "policy_decision": decision,
        "merchant_policy": policy,
        "razorpay_resource": resource,
        "progress": progress,
        "result": result,
        "audit_events": audit_events,
        "audit_chain_valid": verify_merchant_audit_chain(
            db, opportunity.merchant_id
        ),
    }


# ---------------------------------------------------------------------------
# Lifecycle actions - each one delegates to a single domain function
# ---------------------------------------------------------------------------


def run_detection(db: Session, merchant_id: str) -> list[Opportunity]:
    """Detect and persist opportunities. Flushed, not committed."""
    get_merchant(db, merchant_id)
    return run_opportunity_detection(db, merchant_id)


def diagnose(
    db: Session, opportunity_id: str, *, client: Any | None = None
) -> Hypothesis:
    opportunity = get_opportunity(db, opportunity_id)
    return diagnose_opportunity(
        db, opportunity.id, client=client
    )


def plan(db: Session, hypothesis_id: str) -> Experiment:
    hypothesis = get_hypothesis(db, hypothesis_id)
    if hypothesis.status != "proposed":
        raise InvalidTransitionError(
            f"hypothesis {hypothesis.id!r} has status {hypothesis.status!r}; "
            "only a 'proposed' hypothesis can be planned"
        )
    return plan_experiment(db, hypothesis.id)


def authorize_experiment(db: Session, experiment_id: str) -> PolicyDecision:
    experiment = get_experiment(db, experiment_id)
    policy = (
        db.query(MerchantPolicy)
        .filter(MerchantPolicy.merchant_id == experiment.merchant_id)
        .one_or_none()
    )
    if policy is None:
        raise MerchantPolicyNotConfiguredError(
            f"merchant {experiment.merchant_id!r} has no MerchantPolicy row; "
            "an experiment cannot be authorized without one"
        )
    return evaluate_experiment_policy(db, experiment.id)


def deploy(
    db: Session, experiment_id: str, *, razorpay_client: Any | None = None
) -> RazorpayResource:
    """Deploy the approved treatment, or re-raise what Task 13 reported.

    This wrapper writes nothing of its own. That is what makes the API's
    ledger rule safe: when the external create fails, the only flushed change
    in the session is the ``OperationExecution`` row the executor recorded, so
    the boundary can keep that record and still discard the request.
    """
    experiment = get_experiment(db, experiment_id)
    return deploy_experiment_treatment(
        db, experiment.id, razorpay_client=razorpay_client
    )


def run_batch(
    db: Session,
    experiment_id: str,
    *,
    batch_size: int = DEFAULT_RUNTIME_BATCH_SIZE,
    seed: int = DEFAULT_RUNTIME_SEED,
) -> Any:
    experiment = get_experiment(db, experiment_id)
    if experiment.status not in DEPLOYABLE_EXPERIMENT_STATUSES:
        raise InvalidTransitionError(
            f"experiment {experiment.id!r} has status {experiment.status!r}; "
            "traffic requires an approved or running experiment"
        )
    return execute_experiment_batch(
        db, experiment.id, batch_size=batch_size, seed=seed
    )


def ensure_sample_target(
    db: Session, experiment: Experiment
) -> tuple[int, int, int]:
    """Return (control, treatment, target) or raise if evaluation is early."""
    control, treatment = variant_counts(db, experiment.id)
    target = int(experiment.min_sample_per_variant)
    if control < target or treatment < target:
        raise InvalidTransitionError(
            f"experiment {experiment.id!r} has not reached its fixed horizon: "
            f"control {control}/{target}, treatment {treatment}/{target}"
        )
    return control, treatment, target


def evaluate(db: Session, experiment_id: str) -> ExperimentResult:
    experiment = get_experiment(db, experiment_id)
    existing = get_result(db, experiment.id)
    if existing is not None:
        # Idempotent re-read: the horizon was already evaluated once.
        return existing
    if experiment.status not in TERMINAL_EXPERIMENT_STATUSES:
        ensure_sample_target(db, experiment)
    return evaluate_experiment_results(db, experiment.id)


def rollback(
    db: Session, experiment_id: str, *, razorpay_client: Any | None = None
) -> RazorpayResource | None:
    """Cancel the deployed treatment, or re-raise what Task 13 reported.

    Like :func:`deploy` this wrapper performs no writes: an ambiguous cancel
    leaves only the ``pending`` ledger row behind, so the next call is refused
    by Task 13 instead of issuing a second cancel.
    """
    experiment = get_experiment(db, experiment_id)
    return rollback_experiment_treatment(
        db, experiment.id, razorpay_client=razorpay_client
    )


# ---------------------------------------------------------------------------
# Autopilot stepping
# ---------------------------------------------------------------------------


def _step(
    db: Session,
    merchant_id: str,
    *,
    step: str,
    entity_type: str | None,
    entity_id: str | None,
    message: str,
    status: str | None = None,
    next_action: str | None = None,
) -> AutopilotStep:
    """Build a step, defaulting status/next action to the *resolved* state.

    Resolving the next transition after performing an action guarantees that
    ``status``/``next_action`` describe reality instead of a hardcoded guess.
    Only a genuinely terminal step (a blocked deployment, a policy rejection)
    passes both explicitly, because its follow-up must not be re-derived.
    """
    if status is None or next_action is None:
        follow_up = resolve_transition(db, merchant_id).action
        resolved_status = status if status is not None else STATE_BY_ACTION[follow_up]
        resolved_action = next_action if next_action is not None else follow_up
    else:
        resolved_status = status
        resolved_action = next_action
    return AutopilotStep(
        merchant_id=merchant_id,
        step=step,
        entity_type=entity_type,
        entity_id=entity_id,
        message=message,
        status=resolved_status,
        next_action=resolved_action,
    )


def _detection_step(db: Session, merchant_id: str) -> AutopilotStep:
    persisted = run_detection(db, merchant_id)
    if not persisted:
        return _step(
            db,
            merchant_id,
            step=STEP_NO_ACTION,
            entity_type=ENTITY_MERCHANT,
            entity_id=merchant_id,
            message=(
                "No payment-conversion opportunity currently exceeds the "
                "detection thresholds for this merchant."
            ),
        )
    # Same selection rule the lifecycle walk uses, so the reported entity and
    # the entity the next step will act on can never disagree.
    best = focus_opportunity(db, merchant_id) or persisted[0]
    count = len(persisted)
    return _step(
        db,
        merchant_id,
        step=STEP_OPPORTUNITY_DETECTED,
        entity_type=ENTITY_OPPORTUNITY,
        entity_id=best.id,
        message=(
            f"Detected {count} opportunit{'y' if count == 1 else 'ies'}; focusing "
            f"segment {best.segment!r} converting at {_ratio(best.detected_value)} "
            f"against a {_ratio(best.baseline_value)} comparison baseline "
            f"(severity {_number(best.severity, 3)})."
        ),
    )


def _diagnosis_step(
    db: Session, merchant_id: str, opportunity: Opportunity, *, client: Any | None
) -> AutopilotStep:
    hypothesis = diagnose_opportunity(db, opportunity.id, client=client)
    return _step(
        db,
        merchant_id,
        step=STEP_HYPOTHESIS_PROPOSED,
        entity_type=ENTITY_HYPOTHESIS,
        entity_id=hypothesis.id,
        message=(
            f"AI proposed a {hypothesis.intervention_type} hypothesis for segment "
            f"{opportunity.segment!r} (confidence: {hypothesis.confidence or 'n/a'}); "
            "deterministic validation passed."
        ),
    )


def _planning_step(
    db: Session, merchant_id: str, hypothesis: Hypothesis
) -> AutopilotStep:
    experiment = plan_experiment(db, hypothesis.id)
    return _step(
        db,
        merchant_id,
        step=STEP_EXPERIMENT_PLANNED,
        entity_type=ENTITY_EXPERIMENT,
        entity_id=experiment.id,
        message=(
            f"Planned experiment {experiment.name!r}: "
            f"{_ratio(experiment.traffic_split_treatment_pct)} of "
            f"{experiment.segment!r} traffic in treatment, "
            f"{experiment.min_sample_per_variant} attempts per variant maximum "
            f"{experiment.max_duration_hours}h."
        ),
    )


def _policy_step(
    db: Session, merchant_id: str, experiment: Experiment
) -> AutopilotStep:
    decision = evaluate_experiment_policy(db, experiment.id)
    if decision.decision == "APPROVE":
        return _step(
            db,
            merchant_id,
            step=STEP_POLICY_APPROVED,
            entity_type=ENTITY_EXPERIMENT,
            entity_id=decision.experiment_id,
            message=(
                f"Merchant policy approved {experiment.name!r} within its "
                "configured exposure, discount and duration limits."
            ),
        )
    violations = ", ".join(list(decision.violations or [])) or "unspecified"
    return _step(
        db,
        merchant_id,
        step=STEP_POLICY_REJECTED,
        entity_type=ENTITY_EXPERIMENT,
        entity_id=decision.experiment_id,
        message=(
            f"Merchant policy rejected {experiment.name!r}: {violations}. "
            "No automatic re-planning is attempted."
        ),
        status=STATE_POLICY_REJECTED,
        next_action=ACTION_STOP,
    )


def _deployment_blocked_step(
    db: Session, merchant_id: str, experiment: Experiment, reason: str
) -> AutopilotStep:
    return _step(
        db,
        merchant_id,
        step=STEP_DEPLOYMENT_BLOCKED,
        entity_type=ENTITY_EXPERIMENT,
        entity_id=experiment.id,
        message=f"Deployment blocked: {reason}",
        status=STATE_DEPLOYMENT_BLOCKED,
        next_action=ACTION_BLOCKED,
    )


def _deploy_step(
    db: Session,
    merchant_id: str,
    experiment: Experiment,
    *,
    razorpay_client: Any | None,
) -> AutopilotStep:
    try:
        resource = deploy_experiment_treatment(
            db, experiment.id, razorpay_client=razorpay_client
        )
    except ExperimentExecutionConfigurationError as exc:
        # Task 13 fails closed (e.g. offer_discount has no verified Offer
        # mapping) *before* any external call or idempotency marker is
        # written, so this stays a visible, recoverable Autopilot state
        # instead of an API crash.
        return _deployment_blocked_step(db, merchant_id, experiment, str(exc))
    return _step(
        db,
        merchant_id,
        step=STEP_RESOURCE_DEPLOYED,
        entity_type=ENTITY_EXPERIMENT,
        entity_id=experiment.id,
        message=(
            f"Created Razorpay Test Mode {resource.resource_type} "
            f"{resource.razorpay_id!r} for the {resource.variant} variant."
        ),
    )


def _batch_step(
    db: Session,
    merchant_id: str,
    experiment: Experiment,
    *,
    batch_size: int,
) -> AutopilotStep:
    summary = execute_experiment_batch(
        db,
        experiment.id,
        batch_size=batch_size,
        seed=DEFAULT_RUNTIME_SEED,
    )
    target = int(summary.sample_target_per_variant)
    return _step(
        db,
        merchant_id,
        step=STEP_EXPERIMENT_BATCH_RUN,
        entity_type=ENTITY_EXPERIMENT,
        entity_id=experiment.id,
        message=(
            f"Ran {summary.generated_attempts} simulated attempt(s); control "
            f"{summary.control_attempts}/{target}, treatment "
            f"{summary.treatment_attempts}/{target}."
        ),
    )


def _evaluation_step(
    db: Session, merchant_id: str, experiment: Experiment
) -> AutopilotStep:
    result = evaluate_experiment_results(db, experiment.id)
    return _step(
        db,
        merchant_id,
        step=STEP_EXPERIMENT_EVALUATED,
        entity_type=ENTITY_EXPERIMENT,
        entity_id=experiment.id,
        message=(
            f"Fixed-horizon evaluation complete: {result.decision} "
            f"(absolute lift {_signed_ratio(result.absolute_lift)}, "
            f"p={_number(result.p_value)})."
        ),
    )


def _rollback_step(
    db: Session,
    merchant_id: str,
    experiment: Experiment,
    *,
    razorpay_client: Any | None,
) -> AutopilotStep:
    resource = rollback_experiment_treatment(
        db, experiment.id, razorpay_client=razorpay_client
    )
    if resource is None:
        return _step(
            db,
            merchant_id,
            step=STEP_COMPLETED,
            entity_type=ENTITY_EXPERIMENT,
            entity_id=experiment.id,
            message=(
                "Decision is ROLLBACK but no treatment resource was deployed; "
                "there is nothing to cancel."
            ),
        )
    return _step(
        db,
        merchant_id,
        step=STEP_RESOURCE_ROLLED_BACK,
        entity_type=ENTITY_EXPERIMENT,
        entity_id=experiment.id,
        message=(
            f"Cancelled Razorpay treatment resource {resource.razorpay_id!r} "
            "after the statistical ROLLBACK decision."
        ),
    )


def _terminal_step(
    db: Session, merchant_id: str, transition: _Transition
) -> AutopilotStep:
    experiment = transition.experiment
    result = transition.result
    if experiment is None:
        return _step(
            db,
            merchant_id,
            step=STEP_NO_ACTION,
            entity_type=ENTITY_MERCHANT,
            entity_id=merchant_id,
            message="Nothing to do for this merchant right now.",
            status=STATE_IDLE,
            next_action=None,
        )
    if result is None:
        return _step(
            db,
            merchant_id,
            step=STEP_COMPLETED,
            entity_type=ENTITY_EXPERIMENT,
            entity_id=experiment.id,
            message=(
                f"Experiment {experiment.name!r} is already "
                f"{experiment.status}; no further Autopilot action is available."
            ),
        )
    detail = "no treatment resource was deployed"
    if transition.resource is not None:
        detail = (
            f"treatment resource {transition.resource.razorpay_id!r} is "
            f"{transition.resource.status}"
        )
    return _step(
        db,
        merchant_id,
        step=STEP_COMPLETED,
        entity_type=ENTITY_EXPERIMENT,
        entity_id=experiment.id,
        message=(
            f"Experiment {experiment.name!r} is complete with decision "
            f"{result.decision}; {detail}."
        ),
    )


def advance_autopilot(
    db: Session,
    merchant_id: str,
    *,
    openai_client: Any | None = None,
    razorpay_client: Any | None = None,
    runtime_batch_size: int = DEFAULT_RUNTIME_BATCH_SIZE,
) -> AutopilotStep:
    """Advance one merchant by at most one meaningful lifecycle transition.

    The action performed is decided entirely from persisted database state
    (never from a request-supplied "what to do next"), in this priority:

    1. no active opportunity            -> detect
    2. opportunity without a hypothesis -> AI diagnosis
    3. hypothesis without an experiment -> plan
    4. proposed experiment              -> policy evaluation
    5. rejected experiment              -> stop, visibly POLICY_REJECTED
    6. approved without a resource      -> deploy (safe DEPLOYMENT_BLOCKED
                                           when execution is not authorized)
    7. sample target not reached        -> one runtime batch
    8. target reached, no result        -> statistical evaluation
    9. completed with ROLLBACK          -> cancel the real resource
    10. otherwise completed             -> terminal report

    Never commits: the caller (API boundary) owns the transaction.
    """
    transition = resolve_transition(db, merchant_id)
    action = transition.action

    if action == ACTION_DETECT:
        return _detection_step(db, merchant_id)
    if action == ACTION_DIAGNOSE:
        assert transition.opportunity is not None
        return _diagnosis_step(
            db, merchant_id, transition.opportunity, client=openai_client
        )
    if action == ACTION_PLAN:
        assert transition.hypothesis is not None
        return _planning_step(db, merchant_id, transition.hypothesis)

    experiment = transition.experiment
    if experiment is None:  # pragma: no cover - defensive
        raise InvalidTransitionError(
            f"no lifecycle entity is available to advance for {merchant_id!r}"
        )

    if action == ACTION_POLICY:
        return _policy_step(db, merchant_id, experiment)
    if action == ACTION_STOP:
        return _policy_step_rejection(db, merchant_id, transition)
    if action == ACTION_DEPLOY:
        return _deploy_step(
            db, merchant_id, experiment, razorpay_client=razorpay_client
        )
    if action == ACTION_BLOCKED:
        return _deployment_blocked_step(
            db,
            merchant_id,
            experiment,
            "the approved intervention cannot be mapped to a supported "
            "Razorpay resource yet.",
        )
    if action == ACTION_RUN_BATCH:
        return _batch_step(
            db, merchant_id, experiment, batch_size=runtime_batch_size
        )
    if action == ACTION_EVALUATE:
        return _evaluation_step(db, merchant_id, experiment)
    if action == ACTION_ROLLBACK:
        return _rollback_step(
            db, merchant_id, experiment, razorpay_client=razorpay_client
        )
    return _terminal_step(db, merchant_id, transition)


def _policy_step_rejection(
    db: Session, merchant_id: str, transition: _Transition
) -> AutopilotStep:
    """Report an existing rejection without re-running the policy engine."""
    experiment = transition.experiment
    assert experiment is not None
    decision = transition.decision
    violations = ", ".join(list(decision.violations or [])) if decision else "unspecified"
    return _step(
        db,
        merchant_id,
        step=STEP_POLICY_REJECTED,
        entity_type=ENTITY_EXPERIMENT,
        entity_id=experiment.id,
        message=(
            f"Merchant policy rejected {experiment.name!r}: {violations}. "
            "No automatic re-planning is attempted."
        ),
        status=STATE_POLICY_REJECTED,
        next_action=ACTION_STOP,
    )


__all__ = [
    "ACTIVE_EXPERIMENT_STATUSES",
    "ACTIVE_OPPORTUNITY_STATUSES",
    "ACTION_BLOCKED",
    "ACTION_DETECT",
    "ACTION_DIAGNOSE",
    "ACTION_DONE",
    "ACTION_DEPLOY",
    "ACTION_EVALUATE",
    "ACTION_PLAN",
    "ACTION_POLICY",
    "ACTION_ROLLBACK",
    "ACTION_RUN_BATCH",
    "ACTION_STOP",
    "AUTOPILOT_STEPS",
    "AutopilotError",
    "AutopilotStep",
    "CYCLE_AUDIT_LIMIT",
    "DEFAULT_RUNTIME_BATCH_SIZE",
    "DEFAULT_RUNTIME_SEED",
    "ExperimentNotFoundError",
    "HypothesisNotFoundError",
    "InvalidTransitionError",
    "MerchantNotFoundError",
    "MerchantPolicyNotConfiguredError",
    "OpportunityNotFoundError",
    "P0_MERCHANT_ID",
    "STATE_BY_ACTION",
    "advance_autopilot",
    "autopilot_status",
    "deploy",
    "diagnose",
    "evaluate",
    "authorize_experiment",
    "experiment_audit_history",
    "experiment_progress",
    "get_autopilot_cycle",
    "get_experiment",
    "get_merchant",
    "get_merchant_policy",
    "get_opportunity",
    "gmv_totals",
    "list_opportunities",
    "merchant_audit_history",
    "merchant_summary",
    "opportunity_audit_history",
    "overview",
    "plan",
    "resolve_transition",
    "rollback",
    "run_batch",
    "run_detection",
    "variant_counts",
]
