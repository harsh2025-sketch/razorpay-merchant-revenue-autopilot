"""Deterministic paired-cohort evaluation harness for Task 16.

The harness is the one evaluation module allowed to import the sealed causal
simulation.  It uses that model only after all strategy-selection work is
complete, and only to score a shared set of observable ``PaymentContext``
objects.  Baseline selection, evidence construction, hypothesis validation,
planning, and policy evaluation do not inspect treatment outcomes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json
import random
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import (
    Experiment,
    Merchant,
    MerchantPolicy,
    Opportunity,
    PaymentAttempt,
)
from app.engines.diagnosis import (
    build_evidence_catalog,
    persist_hypothesis,
    validate_proposal,
)
from app.engines.metrics import get_failure_reason_counts, get_payment_method_metrics
from app.engines.opportunities import run_opportunity_detection
from app.engines.planner import plan_experiment
from app.engines.policy import evaluate_experiment_policy, evaluate_policy
from app.schemas.hypothesis import HypothesisProposal
from app.simulation.causal_model import (
    InterventionSpec,
    PaymentContext,
    causal_model_fingerprint,
    simulate_outcome,
)
from app.simulation.generator import generate_baseline_events
from app.simulation.merchant import TECHBAZAAR_PROFILE, SegmentProfile

from app.evaluation.baselines import (
    ALLOWED_INTERVENTION_TYPES,
    SAFE_INTERVENTION_PARAMS,
    STRATEGIES,
    choose_random_intervention,
    choose_rule_based_intervention,
    stable_segment_derivation,
)
from app.evaluation.report import (
    BenchmarkReport,
    EvaluationAggregate,
    EvaluationRunResult,
)


DEFAULT_SEEDS: tuple[int, ...] = (
    20260827,
    20260828,
    20260829,
    20260830,
    20260831,
)

CANONICAL_SEGMENTS: tuple[str, ...] = (
    "android_mid",
    "android_budget",
    "web_general",
    "repeat_buyer",
    "ios_premium",
)

EVALUATION_CUSTOMERS_PER_SEGMENT = 5000
BASELINE_SEED = 20260827


@dataclass(frozen=True)
class BenchmarkConfig:
    """Validated benchmark knobs. Defaults are the frozen Task 16 cohort."""

    seeds: tuple[int, ...] = DEFAULT_SEEDS
    segments: tuple[str, ...] = CANONICAL_SEGMENTS
    customers_per_segment: int = EVALUATION_CUSTOMERS_PER_SEGMENT

    def validated(self) -> "BenchmarkConfig":
        seeds = tuple(int(seed) for seed in self.seeds)
        segments = tuple(self.segments)
        if not seeds or len(set(seeds)) != len(seeds):
            raise ValueError("seeds must be a non-empty sequence of unique integers")
        if not segments or len(set(segments)) != len(segments):
            raise ValueError("segments must be a non-empty sequence of unique names")
        unknown = sorted(set(segments) - set(CANONICAL_SEGMENTS))
        if unknown:
            raise ValueError(f"segments must be canonical; unknown values: {unknown}")
        if isinstance(self.customers_per_segment, bool) or not isinstance(
            self.customers_per_segment, int
        ):
            raise ValueError("customers_per_segment must be an integer")
        if self.customers_per_segment <= 0:
            raise ValueError("customers_per_segment must be positive")
        return BenchmarkConfig(
            seeds=seeds,
            segments=segments,
            customers_per_segment=self.customers_per_segment,
        )


@dataclass(frozen=True)
class _BaselineSnapshot:
    """Observable baseline metrics used before evaluation scoring."""

    segment: str
    segment_attempts: int
    segment_captured: int
    segment_conversion_rate: float
    comparison_attempts: int
    comparison_captured: int
    comparison_conversion_rate: float
    evidence: dict[str, object]
    rule_evidence: dict[str, object]
    average_attempt_amount_paise: float


@dataclass(frozen=True)
class _SelectedIntervention:
    proposed: dict[str, object] | None
    policy_decision: str
    deployed: dict[str, object] | None


def _profile_for(segment: str) -> SegmentProfile:
    for profile in TECHBAZAAR_PROFILE.segments:
        if profile.name == segment:
            return profile
    raise ValueError(f"unknown canonical segment: {segment!r}")


def _json_copy(value: Mapping[str, object]) -> dict[str, object]:
    return json.loads(json.dumps(dict(value), sort_keys=True))


def _intervention_payload(
    intervention_type: str, params: Mapping[str, object]
) -> dict[str, object]:
    return {
        "intervention_type": intervention_type,
        "params": _json_copy(params),
    }


def _context_rng(seed: int, segment: str) -> random.Random:
    # The derivation is stable across Python processes.  A separate RNG keeps
    # context generation independent from random-baseline selection.
    return random.Random(seed + stable_segment_derivation(f"context:{segment}"))


def build_paired_contexts(
    seed: int,
    segment: str,
    customers_per_segment: int = EVALUATION_CUSTOMERS_PER_SEGMENT,
) -> list[PaymentContext]:
    """Generate the one shared, deterministic cohort for a seed/segment pair.

    Every strategy is scored against the returned list.  ``event_ref`` follows
    the frozen ``eval_<seed>_<segment>_<index>`` convention.
    """
    profile = _profile_for(segment)
    if isinstance(customers_per_segment, bool) or not isinstance(
        customers_per_segment, int
    ) or customers_per_segment <= 0:
        raise ValueError("customers_per_segment must be a positive integer")

    rng = _context_rng(seed, segment)
    min_step = profile.min_amount_paise // 100
    max_step = profile.max_amount_paise // 100
    payment_methods = list(profile.payment_method_weights)
    payment_weights = list(profile.payment_method_weights.values())
    sources = list(profile.source_weights)
    source_weights = list(profile.source_weights.values())
    contexts: list[PaymentContext] = []
    for index in range(customers_per_segment):
        amount = rng.randint(min_step, max_step) * 100
        payment_method = rng.choices(payment_methods, weights=payment_weights, k=1)[0]
        device_type = rng.choices(
            profile.device_types, weights=profile.device_weights, k=1
        )[0]
        source = rng.choices(sources, weights=source_weights, k=1)[0]
        contexts.append(
            PaymentContext(
                event_ref=f"eval_{seed}_{segment}_{index}",
                merchant_id=TECHBAZAAR_PROFILE.merchant_id,
                customer_ref=f"eval_customer_{seed}_{segment}_{index}",
                segment=segment,
                amount=amount,
                currency=TECHBAZAAR_PROFILE.currency,
                payment_method=payment_method,
                device_type=device_type,
                source=source,
            )
        )
    return contexts


# ---------------------------------------------------------------------------
# Observable baseline database and evidence
# ---------------------------------------------------------------------------


def _new_baseline_database() -> tuple[Any, Session]:
    """Create a private in-memory DB seeded with canonical Task 05 data."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_factory()

    merchant = Merchant(
        id=TECHBAZAAR_PROFILE.merchant_id,
        name=TECHBAZAAR_PROFILE.name,
        category=TECHBAZAAR_PROFILE.category,
        monthly_gmv=TECHBAZAAR_PROFILE.monthly_gmv_paise,
    )
    policy = MerchantPolicy(
        id="policy_eval_techbazaar",
        merchant_id=TECHBAZAAR_PROFILE.merchant_id,
        max_experiment_exposure_pct=0.10,
        max_discount_pct=0.15,
        min_margin_pct=0.05,
        max_concurrent_experiments=3,
        max_experiment_duration_hours=168,
        min_sample_size=30,
        max_financial_exposure=50000,
        allowed_interventions=list(ALLOWED_INTERVENTION_TYPES),
    )
    db.add_all([merchant, policy])
    events = generate_baseline_events(
        profile=TECHBAZAAR_PROFILE,
        seed=BASELINE_SEED,
        days=TECHBAZAAR_PROFILE.days,
    )
    db.add_all(
        [
            PaymentAttempt(
                id=event.id,
                merchant_id=event.merchant_id,
                customer_ref=event.customer_ref,
                amount=event.amount,
                currency=event.currency,
                payment_method=event.payment_method,
                status=event.status,
                failure_reason=event.failure_reason,
                device_type=event.device_type,
                segment=event.segment,
                source=event.source,
                created_at=event.created_at,
                completed_at=event.completed_at,
                is_simulated=event.is_simulated,
            )
            for event in events
        ]
    )
    db.commit()
    return engine, db


def _rate(captured: int, attempts: int) -> float:
    return captured / attempts if attempts else 0.0


def _snapshot_for_segment(db: Session, segment: str) -> _BaselineSnapshot:
    merchant_id = TECHBAZAAR_PROFILE.merchant_id
    segment_rows = list(
        db.scalars(
            select(PaymentAttempt).where(
                PaymentAttempt.merchant_id == merchant_id,
                PaymentAttempt.segment == segment,
            )
        )
    )
    all_rows = list(
        db.scalars(
            select(PaymentAttempt).where(PaymentAttempt.merchant_id == merchant_id)
        )
    )
    segment_attempts = len(segment_rows)
    segment_captured = sum(row.status == "captured" for row in segment_rows)
    comparison_attempts = len(all_rows) - segment_attempts
    comparison_captured = sum(row.status == "captured" for row in all_rows) - segment_captured
    segment_rate = _rate(segment_captured, segment_attempts)
    comparison_rate = _rate(comparison_captured, comparison_attempts)

    payment_method_metrics = {
        metric.payment_method: {
            "attempts": metric.attempts,
            "captured": metric.captured,
            "failed": metric.failed,
            "abandoned": metric.abandoned,
            "success_rate": metric.success_rate,
        }
        for metric in get_payment_method_metrics(db, merchant_id, segment=segment)
    }
    failure_reasons = get_failure_reason_counts(db, merchant_id, segment=segment)
    evidence = {
        "segment": segment,
        "segment_attempts": segment_attempts,
        "segment_captured": segment_captured,
        "segment_conversion_rate": segment_rate,
        "comparison_attempts": comparison_attempts,
        "comparison_captured": comparison_captured,
        "comparison_conversion_rate": comparison_rate,
        "absolute_gap": comparison_rate - segment_rate,
        "payment_method_metrics": payment_method_metrics,
        "failure_reasons": failure_reasons,
    }

    # These are ordinary baseline PaymentAttempt metrics for the simple rule
    # baseline.  The amount midpoint is derived from the public segment data,
    # not from the causal model.
    profile = _profile_for(segment)
    midpoint = (profile.min_amount_paise + profile.max_amount_paise) / 2
    low_rows = [row for row in segment_rows if row.amount < midpoint]
    high_rows = [row for row in segment_rows if row.amount >= midpoint]
    low_abandonment = _rate(
        sum(row.status == "abandoned" for row in low_rows), len(low_rows)
    )
    high_abandonment = _rate(
        sum(row.status == "abandoned" for row in high_rows), len(high_rows)
    )
    low_conversion = _rate(
        sum(row.status == "captured" for row in low_rows), len(low_rows)
    )
    high_conversion = _rate(
        sum(row.status == "captured" for row in high_rows), len(high_rows)
    )
    rule_evidence: dict[str, object] = {
        "segment_attempts": segment_attempts,
        "segment_captured": segment_captured,
        "segment_conversion_rate": segment_rate,
        "comparison_conversion_rate": comparison_rate,
        "payment_method_metrics": payment_method_metrics,
        "high_value_abandonment_rate": high_abandonment,
        "low_value_abandonment_rate": low_abandonment,
        "high_value_conversion_rate": high_conversion,
        "low_value_conversion_rate": low_conversion,
    }
    average_amount = (
        float(sum(row.amount for row in segment_rows)) / segment_attempts
        if segment_attempts
        else 0.0
    )
    return _BaselineSnapshot(
        segment=segment,
        segment_attempts=segment_attempts,
        segment_captured=segment_captured,
        segment_conversion_rate=segment_rate,
        comparison_attempts=comparison_attempts,
        comparison_captured=comparison_captured,
        comparison_conversion_rate=comparison_rate,
        evidence=evidence,
        rule_evidence=rule_evidence,
        average_attempt_amount_paise=average_amount,
    )


def _observable_opportunity(
    db: Session, snapshot: _BaselineSnapshot
) -> Opportunity:
    """Persist an observable opportunity when Task 07 has no gap row.

    High-converting canonical segments still need an observable evidence
    record so the benchmark can exercise the complete Autopilot lifecycle.
    This record contains only the same baseline metrics as a Task 07 finding.
    """
    opportunity = Opportunity(
        merchant_id=TECHBAZAAR_PROFILE.merchant_id,
        type="segment_conversion_divergence",
        segment=snapshot.segment,
        severity=abs(
            snapshot.comparison_conversion_rate - snapshot.segment_conversion_rate
        ),
        detected_metric="conversion_rate",
        detected_value=snapshot.segment_conversion_rate,
        baseline_value=snapshot.comparison_conversion_rate,
        evidence=deepcopy(snapshot.evidence),
        status="detected",
    )
    db.add(opportunity)
    db.flush()
    return opportunity


def _opportunity_for_segment(
    db: Session, snapshot: _BaselineSnapshot
) -> Opportunity:
    """Use Task 07 evidence, with a neutral observation fallback."""
    detected = run_opportunity_detection(
        db,
        TECHBAZAAR_PROFILE.merchant_id,
        min_segment_attempts=100,
        min_absolute_gap=0.0,
        max_results=None,
    )
    for opportunity in detected:
        if opportunity.segment == snapshot.segment:
            return opportunity
    return _observable_opportunity(db, snapshot)


# ---------------------------------------------------------------------------
# Deterministic evaluation diagnosis adapter
# ---------------------------------------------------------------------------


def _first_evidence_ref(evidence_catalog: Mapping[str, object]) -> str:
    if not evidence_catalog:
        raise ValueError("an evidence catalog must contain at least one observable key")
    return sorted(evidence_catalog)[0]


def _allowed_choice(
    desired: str, allowed_interventions: set[str]
) -> str:
    if desired in allowed_interventions:
        return desired
    for intervention_type in ALLOWED_INTERVENTION_TYPES:
        if intervention_type in allowed_interventions:
            return intervention_type
    raise ValueError("allowed_interventions must contain a supported intervention")


def evaluation_proposal_from_evidence(
    evidence_catalog: Mapping[str, object],
    allowed_interventions: Iterable[str],
) -> HypothesisProposal:
    """Create a deterministic Task 08-shaped proposal from evidence only.

    This is an offline stand-in for the model response.  It does not receive a
    segment label, causal configuration, treatment outcome, or hidden truth.
    The evidence keys cited by the proposal are the same keys inspected by the
    rule branches, making the boundary auditable.

    Branches, in order:
    - card materially below UPI by five points -> payment method config;
    - an available high-value bucket gap -> partial payment;
    - conversion gap versus the comparison baseline of at least eight points
      -> five percent offer discount;
    - a high observed conversion rate (at least 60%) -> partial payment;
    - otherwise -> four-hour expiry configuration.
    """
    allowed = set(allowed_interventions)

    def number(*keys: str) -> float | None:
        for key in keys:
            value = evidence_catalog.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            value_f = float(value)
            if value_f == value_f and abs(value_f) != float("inf"):
                return value_f
        return None

    card = number("payment_method.card.success_rate")
    upi = number("payment_method.upi.success_rate")
    selected: str
    refs: list[str]
    reason: str
    if (
        card is not None
        and upi is not None
        and card + 0.05 < upi
    ):
        selected = "payment_method_config"
        refs = [
            key
            for key in (
                "payment_method.card.success_rate",
                "payment_method.upi.success_rate",
            )
            if key in evidence_catalog
        ]
        reason = "Observable card success is materially below UPI success."
    elif (
        number("high_value_abandonment_rate") is not None
        and number("low_value_abandonment_rate") is not None
        and number("high_value_abandonment_rate")
        - number("low_value_abandonment_rate")
        >= 0.08
    ) or (
        number("high_value_conversion_rate") is not None
        and number("low_value_conversion_rate") is not None
        and number("low_value_conversion_rate")
        - number("high_value_conversion_rate")
        >= 0.08
    ):
        selected = "partial_payment"
        refs = [
            key
            for key in (
                "high_value_abandonment_rate",
                "low_value_abandonment_rate",
                "high_value_conversion_rate",
                "low_value_conversion_rate",
            )
            if key in evidence_catalog
        ]
        reason = "Observable amount-bucket performance differs materially."
    elif (number("absolute_gap") or 0.0) >= 0.08:
        selected = "offer_discount"
        refs = [
            key
            for key in (
                "segment_conversion_rate",
                "comparison_conversion_rate",
                "absolute_gap",
            )
            if key in evidence_catalog
        ]
        reason = "Observable segment conversion trails its comparison baseline."
    elif (number("segment_conversion_rate") or 0.0) >= 0.60:
        selected = "partial_payment"
        refs = [
            key
            for key in ("segment_conversion_rate", "segment_captured")
            if key in evidence_catalog
        ]
        reason = "Observable segment conversion is strong while testing payment flexibility."
    else:
        selected = "expiry_config"
        refs = [
            key
            for key in ("segment_conversion_rate", "absolute_gap")
            if key in evidence_catalog
        ]
        reason = "Observable evidence does not isolate a stronger intervention signal."

    selected = _allowed_choice(selected, allowed)
    if not refs:
        refs = [_first_evidence_ref(evidence_catalog)]

    # Use only frozen safe semantic parameters.  The existing Task 08 schema
    # and validation path performs the final shape checks.
    params = deepcopy(SAFE_INTERVENTION_PARAMS[selected])
    return HypothesisProposal(
        diagnosis=reason,
        hypothesis_text=(
            "Changing the selected checkout configuration may improve "
            "conversion for this observed cohort; test it against control."
        ),
        intervention_type=selected,
        intervention_params=params,
        confidence="medium",
        reasoning_summary=f"Evidence-based offline adapter: {reason}",
        evidence_refs=refs,
    )


# ---------------------------------------------------------------------------
# Selection and policy gate
# ---------------------------------------------------------------------------


def _configs_for_payload(
    payload: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    intervention_type = str(payload["intervention_type"])
    params = dict(payload["params"] if isinstance(payload.get("params"), Mapping) else {})
    if intervention_type == "payment_method_config":
        return {"payment_methods": "merchant_default"}, {"payment_methods": params}
    if intervention_type == "offer_discount":
        return {"offer": None}, params
    if intervention_type == "partial_payment":
        return {"accept_partial": False}, params
    if intervention_type == "expiry_config":
        return {"expiry_hours": "merchant_default"}, params
    raise ValueError(f"unsupported intervention payload: {intervention_type!r}")


def _detached_experiment_for_policy(
    payload: Mapping[str, object], segment: str
) -> Experiment:
    intervention_type = str(payload["intervention_type"])
    control, treatment = _configs_for_payload(payload)
    return Experiment(
        id=f"eval-policy-{segment}-{intervention_type}",
        merchant_id=TECHBAZAAR_PROFILE.merchant_id,
        hypothesis_id="eval-baseline-hypothesis",
        opportunity_id="eval-baseline-opportunity",
        name=f"evaluation-{segment}-{intervention_type}",
        segment=segment,
        intervention_type=intervention_type,
        control_config=control,
        treatment_config=treatment,
        traffic_split_treatment_pct=0.10,
        primary_metric="conversion_rate",
        guardrail_metrics=["captured_gmv", "failure_rate", "abandonment_rate"],
        min_sample_per_variant=200,
        max_duration_hours=72,
        status="proposed",
    )


def _baseline_policy_gate(
    payload: Mapping[str, object], snapshot: _BaselineSnapshot
) -> _SelectedIntervention:
    policy = MerchantPolicy(
        id="policy_eval_detached",
        merchant_id=TECHBAZAAR_PROFILE.merchant_id,
        max_experiment_exposure_pct=0.10,
        max_discount_pct=0.15,
        min_margin_pct=0.05,
        max_concurrent_experiments=3,
        max_experiment_duration_hours=168,
        min_sample_size=30,
        max_financial_exposure=50000,
        allowed_interventions=list(ALLOWED_INTERVENTION_TYPES),
    )
    evaluation = evaluate_policy(
        experiment=_detached_experiment_for_policy(payload, snapshot.segment),
        policy=policy,
        active_experiments=[],
        estimated_segment_attempts=snapshot.segment_attempts,
        average_attempt_amount_paise=snapshot.average_attempt_amount_paise,
    )
    proposed = _json_copy(payload)
    deployed = proposed if evaluation.decision == "APPROVE" else None
    return _SelectedIntervention(
        proposed=proposed,
        policy_decision=evaluation.decision,
        deployed=deployed,
    )


def _autopilot_selection(segment: str) -> tuple[_BaselineSnapshot, _SelectedIntervention]:
    """Run Tasks 07–10 in a fresh temporary database for one segment."""
    engine, db = _new_baseline_database()
    try:
        snapshot = _snapshot_for_segment(db, segment)
        opportunity = _opportunity_for_segment(db, snapshot)
        evidence_catalog = build_evidence_catalog(opportunity)
        policy = db.scalar(
            select(MerchantPolicy).where(
                MerchantPolicy.merchant_id == TECHBAZAAR_PROFILE.merchant_id
            )
        )
        if policy is None:
            raise RuntimeError("canonical evaluation policy was not seeded")

        proposal = evaluation_proposal_from_evidence(
            evidence_catalog, policy.allowed_interventions
        )
        # This is the same deterministic validation used at the production
        # diagnosis boundary, followed by the same persistence path.
        proposal = validate_proposal(
            proposal, evidence_catalog, set(policy.allowed_interventions)
        )
        hypothesis = persist_hypothesis(
            db,
            opportunity=opportunity,
            proposal=proposal,
            ai_model="deterministic-evaluation-adapter",
        )
        experiment = plan_experiment(db, hypothesis.id)
        decision = evaluate_experiment_policy(db, experiment.id)
        db.commit()

        proposed = _intervention_payload(
            proposal.intervention_type, proposal.intervention_params
        )
        if decision.decision == "APPROVE":
            deployed = _intervention_payload(
                experiment.intervention_type,
                _treatment_params_from_plan(experiment),
            )
        else:
            deployed = None
        return snapshot, _SelectedIntervention(
            proposed=proposed,
            policy_decision=decision.decision,
            deployed=deployed,
        )
    finally:
        db.close()
        engine.dispose()


def _treatment_params_from_plan(experiment: Experiment) -> dict[str, object]:
    treatment = dict(experiment.treatment_config or {})
    if experiment.intervention_type == "payment_method_config":
        methods = treatment.get("payment_methods")
        if not isinstance(methods, Mapping):
            raise ValueError("malformed planned payment method treatment")
        return dict(methods)
    return treatment


def _selection_table(
    config: BenchmarkConfig,
) -> dict[tuple[int, str], dict[str, _SelectedIntervention]]:
    """Select every strategy before any evaluation cohort is scored."""
    selected: dict[tuple[int, str], dict[str, _SelectedIntervention]] = {}
    for segment in config.segments:
        snapshot, autopilot = _autopilot_selection(segment)
        rule = _baseline_policy_gate(
            choose_rule_based_intervention(snapshot.rule_evidence), snapshot
        )
        for seed in config.seeds:
            random_choice = _baseline_policy_gate(
                choose_random_intervention(seed, segment), snapshot
            )
            selected[(seed, segment)] = {
                "NO_OPTIMIZATION": _SelectedIntervention(None, "N/A", None),
                "RANDOM_INTERVENTION": random_choice,
                "RULE_BASED": rule,
                "AUTOPILOT": autopilot,
            }
    return selected


# ---------------------------------------------------------------------------
# Paired causal scoring (the only hidden-world access)
# ---------------------------------------------------------------------------


def _context_fingerprint(contexts: Sequence[PaymentContext]) -> str:
    material = [asdict(context) for context in contexts]
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _spec_from_payload(
    payload: Mapping[str, object] | None,
) -> InterventionSpec | None:
    if payload is None:
        return None
    intervention_type = str(payload["intervention_type"])
    params = payload.get("params")
    if not isinstance(params, Mapping):
        raise ValueError("intervention payload params must be an object")
    return InterventionSpec(intervention_type=intervention_type, params=dict(params))


def _score_pair(
    *,
    seed: int,
    segment: str,
    contexts: Sequence[PaymentContext],
    selections: Mapping[str, _SelectedIntervention],
) -> list[EvaluationRunResult]:
    # Compute control exactly once.  Every no-deployment strategy reuses these
    # observable outcomes, and every delta is relative to these same outcomes.
    control_outcomes = [
        simulate_outcome(context=context, intervention=None, seed=seed)
        for context in contexts
    ]
    control_captured = sum(outcome.status == "captured" for outcome in control_outcomes)
    control_rate = _rate(control_captured, len(contexts))
    control_gmv = sum(
        context.amount
        for context, outcome in zip(contexts, control_outcomes, strict=True)
        if outcome.status == "captured"
    )
    context_fp = _context_fingerprint(contexts)

    results: list[EvaluationRunResult] = []
    for strategy in STRATEGIES:
        selection = selections[strategy]
        deployed_spec = _spec_from_payload(selection.deployed)
        if deployed_spec is None:
            outcomes = control_outcomes
        else:
            outcomes = [
                simulate_outcome(
                    context=context,
                    intervention=deployed_spec,
                    seed=seed,
                )
                for context in contexts
            ]
        captured = sum(outcome.status == "captured" for outcome in outcomes)
        conversion_rate = _rate(captured, len(contexts))
        captured_gmv = sum(
            context.amount
            for context, outcome in zip(contexts, outcomes, strict=True)
            if outcome.status == "captured"
        )
        delta = conversion_rate - control_rate if deployed_spec is not None else 0.0
        gmv_delta = captured_gmv - control_gmv if deployed_spec is not None else 0
        discount_exposure = 0
        if (
            selection.deployed is not None
            and selection.deployed.get("intervention_type") == "offer_discount"
        ):
            params = selection.deployed.get("params")
            if isinstance(params, Mapping):
                discount = float(params.get("discount_pct", 0.0))
                discount_exposure = int(round(captured_gmv * discount))

        results.append(
            EvaluationRunResult(
                seed=seed,
                segment=segment,
                strategy=strategy,
                proposed_intervention=deepcopy(selection.proposed),
                policy_decision=selection.policy_decision,
                deployed_intervention=deepcopy(selection.deployed),
                attempts=len(contexts),
                captured=captured,
                conversion_rate=conversion_rate,
                captured_gmv_paise=captured_gmv,
                control_conversion_rate=control_rate,
                absolute_conversion_delta_vs_control=delta,
                control_captured_gmv_paise=control_gmv,
                captured_gmv_delta_vs_control_paise=gmv_delta,
                discount_exposure_paise=discount_exposure,
                paired_context_fingerprint=context_fp,
            )
        )
    return results


def _aggregate_results(
    runs: Sequence[EvaluationRunResult], config: BenchmarkConfig
) -> list[EvaluationAggregate]:
    aggregates: list[EvaluationAggregate] = []
    for segment in config.segments:
        for strategy in STRATEGIES:
            grouped = [
                run
                for run in runs
                if run.segment == segment and run.strategy == strategy
            ]
            gmv_deltas = [run.captured_gmv_delta_vs_control_paise for run in grouped]
            aggregates.append(
                EvaluationAggregate(
                    strategy=strategy,
                    segment=segment,
                    runs=len(grouped),
                    mean_conversion_rate=_rate(
                        sum(run.conversion_rate for run in grouped), len(grouped)
                    ),
                    mean_absolute_delta=_rate(
                        sum(
                            run.absolute_conversion_delta_vs_control
                            for run in grouped
                        ),
                        len(grouped),
                    ),
                    mean_gmv_delta_paise=_rate(sum(gmv_deltas), len(gmv_deltas)),
                    total_gmv_delta_paise=sum(gmv_deltas),
                    positive_seed_count=sum(
                        run.absolute_conversion_delta_vs_control > 0
                        for run in grouped
                    ),
                    negative_seed_count=sum(
                        run.absolute_conversion_delta_vs_control < 0
                        for run in grouped
                    ),
                )
            )
    return aggregates


def run_benchmark(
    *,
    seeds: Iterable[int] = DEFAULT_SEEDS,
    segments: Iterable[str] = CANONICAL_SEGMENTS,
    customers_per_segment: int = EVALUATION_CUSTOMERS_PER_SEGMENT,
    config: BenchmarkConfig | None = None,
) -> BenchmarkReport:
    """Run the deterministic four-strategy benchmark.

    The selection table is built completely before the first causal outcome is
    requested.  A cohort is then generated once for each seed/segment and
    passed to all four strategies, with the control outcome cached once.
    """
    if config is not None:
        if any(
            value != default
            for value, default in (
                (tuple(seeds), DEFAULT_SEEDS),
                (tuple(segments), CANONICAL_SEGMENTS),
                (customers_per_segment, EVALUATION_CUSTOMERS_PER_SEGMENT),
            )
        ):
            raise ValueError("pass either config or individual benchmark arguments, not both")
        resolved = config.validated()
    else:
        resolved = BenchmarkConfig(
            seeds=tuple(seeds),
            segments=tuple(segments),
            customers_per_segment=customers_per_segment,
        ).validated()

    # No simulate_outcome call occurs in this selection phase.
    selections = _selection_table(resolved)
    scored_runs: list[EvaluationRunResult] = []
    for seed in resolved.seeds:
        for segment in resolved.segments:
            contexts = build_paired_contexts(
                seed, segment, resolved.customers_per_segment
            )
            scored_runs.extend(
                _score_pair(
                    seed=seed,
                    segment=segment,
                    contexts=contexts,
                    selections=selections[(seed, segment)],
                )
            )

    return BenchmarkReport(
        causal_model_fingerprint=causal_model_fingerprint(),
        seeds=list(resolved.seeds),
        customers_per_segment=resolved.customers_per_segment,
        runs=scored_runs,
        aggregates=_aggregate_results(scored_runs, resolved),
    )


run_evaluation = run_benchmark
# Descriptive aliases used by callers that want to emphasize that contexts
# are cohorts rather than runtime experiment traffic.
generate_paired_contexts = build_paired_contexts
generate_evaluation_cohort = build_paired_contexts


__all__ = [
    "DEFAULT_SEEDS",
    "CANONICAL_SEGMENTS",
    "EVALUATION_CUSTOMERS_PER_SEGMENT",
    "BenchmarkConfig",
    "build_paired_contexts",
    "generate_paired_contexts",
    "generate_evaluation_cohort",
    "evaluation_proposal_from_evidence",
    "run_benchmark",
    "run_evaluation",
]
