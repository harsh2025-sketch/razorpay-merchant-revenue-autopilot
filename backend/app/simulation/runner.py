"""Deterministic experiment runtime for APPROVED experiments (Task 11).

Simulates merchant traffic locally. Does not call Razorpay, OpenAI, or
re-evaluate merchant policy. Does not compute statistics or decide winners.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
import hashlib
import random

from sqlalchemy.orm import Session

from app.db.models import Experiment, ExperimentAssignment, PaymentAttempt
from app.services.audit import (
    ACTOR_RUNTIME,
    ENTITY_EXPERIMENT,
    EXPERIMENT_STARTED,
    record_audit_event_once,
)
from app.simulation.causal_model import (
    InterventionSpec,
    PaymentContext,
    simulate_outcome,
)
from app.simulation.merchant import TECHBAZAAR_PROFILE, SegmentProfile

ALLOWED_RUNTIME_STATUSES = frozenset({"approved", "running"})
REJECTED_RUNTIME_STATUSES = frozenset(
    {"proposed", "rejected", "completed", "rolled_back", "cancelled"}
)
SUPPORTED_INTERVENTION_TYPES = frozenset(
    {
        "payment_method_config",
        "offer_discount",
        "partial_payment",
        "expiry_config",
    }
)
MAX_BATCH_SIZE = 5000
RUNTIME_ANCHOR = datetime(2026, 8, 27, 0, 0, 0, tzinfo=timezone.utc)
VARIANT_CONTROL = "control"
VARIANT_TREATMENT = "treatment"


class ExperimentRuntimeError(Exception):
    """Raised when an experiment cannot be executed by the Task 11 runtime."""


@dataclass(frozen=True)
class ExperimentRunSummary:
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
    status: str


def assign_variant(
    *,
    experiment_id: str,
    customer_ref: str,
    treatment_pct: float,
) -> Literal["control", "treatment"]:
    """Sticky, deterministic control/treatment assignment via SHA-256."""
    digest = hashlib.sha256(f"{experiment_id}:{customer_ref}".encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big") / 2**64
    if value < treatment_pct:
        return VARIANT_TREATMENT
    return VARIANT_CONTROL


def _segment_profile(segment: str) -> SegmentProfile:
    for seg in TECHBAZAAR_PROFILE.segments:
        if seg.name == segment:
            return seg
    raise ExperimentRuntimeError(
        f"segment {segment!r} is not part of the canonical TechBazaar profile"
    )


def _weighted_choice(rng: random.Random, weights: dict[str, float]) -> str:
    names = list(weights.keys())
    values = list(weights.values())
    return rng.choices(names, weights=values, k=1)[0]


def _event_rng(seed: int, experiment_id: str, seq: int) -> random.Random:
    material = f"{seed}|{experiment_id}|{seq}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return random.Random(int(digest, 16))


def _customer_ref(*, experiment_id: str, seq: int, segment: str, rng: random.Random) -> str:
    if segment == "repeat_buyer":
        pool_index = rng.randint(1, 40)
        return f"exp_{experiment_id}_cust_{pool_index:06d}"
    return f"exp_{experiment_id}_cust_{seq:06d}"


def _event_ref(experiment_id: str, seq: int) -> str:
    return f"exp_{experiment_id}_event_{seq:06d}"


def _attempt_id(experiment_id: str, seq: int) -> str:
    return f"exp_{experiment_id}_event_{seq:06d}"


def _assignment_id(experiment_id: str, customer_ref: str) -> str:
    digest = hashlib.sha256(f"{experiment_id}:{customer_ref}".encode("utf-8")).hexdigest()[:16]
    return f"asgn_{digest}"


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _treatment_intervention(experiment: Experiment) -> InterventionSpec:
    itype = experiment.intervention_type
    config = dict(experiment.treatment_config or {})
    if itype == "payment_method_config":
        methods = config.get("payment_methods")
        if not isinstance(methods, dict):
            raise ExperimentRuntimeError(
                "payment_method_config treatment_config must contain payment_methods object"
            )
        params = dict(methods)
    elif itype in ("offer_discount", "partial_payment", "expiry_config"):
        params = dict(config)
    else:
        raise ExperimentRuntimeError(f"unsupported intervention_type: {itype!r}")
    return InterventionSpec(intervention_type=itype, params=params)


def _validate_batch_size(batch_size: object) -> int:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int):
        raise ExperimentRuntimeError(
            f"batch_size must be a positive int, not {type(batch_size).__name__}"
        )
    if batch_size <= 0:
        raise ExperimentRuntimeError("batch_size must be > 0")
    if batch_size > MAX_BATCH_SIZE:
        raise ExperimentRuntimeError(
            f"batch_size {batch_size} exceeds maximum {MAX_BATCH_SIZE}"
        )
    return batch_size


def _count_attempts(db: Session, experiment_id: str, variant: str | None = None) -> int:
    q = db.query(PaymentAttempt).filter(PaymentAttempt.experiment_id == experiment_id)
    if variant is not None:
        q = q.filter(PaymentAttempt.variant == variant)
    return q.count()


def _count_captured(db: Session, experiment_id: str, variant: str) -> int:
    return (
        db.query(PaymentAttempt)
        .filter(
            PaymentAttempt.experiment_id == experiment_id,
            PaymentAttempt.variant == variant,
            PaymentAttempt.status == "captured",
        )
        .count()
    )


def _existing_assignment(
    db: Session, experiment_id: str, customer_ref: str
) -> ExperimentAssignment | None:
    return (
        db.query(ExperimentAssignment)
        .filter(
            ExperimentAssignment.experiment_id == experiment_id,
            ExperimentAssignment.customer_ref == customer_ref,
        )
        .one_or_none()
    )


def _restore_timezone_awareness(db: Session, experiment_id: str) -> None:
    """SQLite DateTime round-trips drop tzinfo; keep runtime objects UTC-aware."""
    for att in db.query(PaymentAttempt).filter(PaymentAttempt.experiment_id == experiment_id):
        att.created_at = _ensure_utc(att.created_at)  # type: ignore[assignment]
        att.completed_at = _ensure_utc(att.completed_at)
    experiment = db.get(Experiment, experiment_id)
    if experiment is not None:
        experiment.started_at = _ensure_utc(experiment.started_at)
    for row in db.query(ExperimentAssignment).filter(
        ExperimentAssignment.experiment_id == experiment_id
    ):
        row.assigned_at = _ensure_utc(row.assigned_at)  # type: ignore[assignment]


def _build_summary(
    db: Session,
    experiment: Experiment,
    generated: int,
) -> ExperimentRunSummary:
    target = int(experiment.min_sample_per_variant)
    control_attempts = _count_attempts(db, experiment.id, VARIANT_CONTROL)
    treatment_attempts = _count_attempts(db, experiment.id, VARIANT_TREATMENT)
    total_assignments = (
        db.query(ExperimentAssignment)
        .filter(ExperimentAssignment.experiment_id == experiment.id)
        .count()
    )
    return ExperimentRunSummary(
        experiment_id=experiment.id,
        generated_attempts=generated,
        control_attempts=control_attempts,
        treatment_attempts=treatment_attempts,
        control_captured=_count_captured(db, experiment.id, VARIANT_CONTROL),
        treatment_captured=_count_captured(db, experiment.id, VARIANT_TREATMENT),
        total_assignments=total_assignments,
        sample_target_per_variant=target,
        control_remaining=max(0, target - control_attempts),
        treatment_remaining=max(0, target - treatment_attempts),
        status=experiment.status,
    )


def run_experiment_batch(
    db: Session,
    experiment_id: str,
    *,
    batch_size: int = 100,
    seed: int = 20260827,
) -> ExperimentRunSummary:
    """Simulate a deterministic batch of experimental traffic. Flush only."""
    batch_size = _validate_batch_size(batch_size)

    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise ExperimentRuntimeError(f"Experiment not found: {experiment_id!r}")

    if experiment.status not in ALLOWED_RUNTIME_STATUSES:
        raise ExperimentRuntimeError(
            f"experiment {experiment_id!r} has status {experiment.status!r}; "
            f"runtime requires approved or running"
        )

    if experiment.merchant_id != TECHBAZAAR_PROFILE.merchant_id:
        raise ExperimentRuntimeError(
            "Task 11 runtime only supports the canonical TechBazaar merchant"
        )

    if experiment.intervention_type not in SUPPORTED_INTERVENTION_TYPES:
        raise ExperimentRuntimeError(
            f"unsupported intervention_type: {experiment.intervention_type!r}"
        )

    segment_profile = _segment_profile(experiment.segment)
    treatment_pct = float(experiment.traffic_split_treatment_pct)
    if isinstance(experiment.traffic_split_treatment_pct, bool) or not (
        0.0 < treatment_pct <= 1.0
    ):
        raise ExperimentRuntimeError(
            f"invalid traffic_split_treatment_pct: {experiment.traffic_split_treatment_pct!r}"
        )

    treatment_spec = _treatment_intervention(experiment)

    if experiment.status == "approved":
        experiment.status = "running"
        if experiment.started_at is None:
            experiment.started_at = RUNTIME_ANCHOR
        record_audit_event_once(
            db,
            merchant_id=experiment.merchant_id,
            event_type=EXPERIMENT_STARTED,
            entity_type=ENTITY_EXPERIMENT,
            entity_id=experiment.id,
            data={
                "control_target": int(experiment.min_sample_per_variant),
                "treatment_target": int(experiment.min_sample_per_variant),
            },
            actor=ACTOR_RUNTIME,
        )

    control_so_far = _count_attempts(db, experiment.id, VARIANT_CONTROL)
    treatment_so_far = _count_attempts(db, experiment.id, VARIANT_TREATMENT)
    target = int(experiment.min_sample_per_variant)

    if control_so_far >= target and treatment_so_far >= target:
        db.flush()
        _restore_timezone_awareness(db, experiment.id)
        return _build_summary(db, experiment, generated=0)

    existing_events = control_so_far + treatment_so_far
    generated = 0
    next_seq = existing_events + 1
    pending_assignments: dict[str, str] = {}

    while generated < batch_size:
        if control_so_far >= target and treatment_so_far >= target:
            break

        seq = next_seq
        next_seq += 1
        rng = _event_rng(seed, experiment.id, seq)

        min_step = segment_profile.min_amount_paise // 100
        max_step = segment_profile.max_amount_paise // 100
        amount = rng.randint(min_step, max_step) * 100
        payment_method = _weighted_choice(rng, segment_profile.payment_method_weights)
        device_type = rng.choices(
            list(segment_profile.device_types),
            weights=list(segment_profile.device_weights),
            k=1,
        )[0]
        source = _weighted_choice(rng, segment_profile.source_weights)
        customer_ref = _customer_ref(
            experiment_id=experiment.id,
            seq=seq,
            segment=experiment.segment,
            rng=rng,
        )
        event_ref = _event_ref(experiment.id, seq)

        if customer_ref in pending_assignments:
            variant = pending_assignments[customer_ref]
        else:
            existing = _existing_assignment(db, experiment.id, customer_ref)
            if existing is not None:
                variant = existing.variant
            else:
                variant = assign_variant(
                    experiment_id=experiment.id,
                    customer_ref=customer_ref,
                    treatment_pct=treatment_pct,
                )
                db.add(
                    ExperimentAssignment(
                        id=_assignment_id(experiment.id, customer_ref),
                        experiment_id=experiment.id,
                        customer_ref=customer_ref,
                        variant=variant,
                        assigned_at=RUNTIME_ANCHOR + timedelta(seconds=seq),
                    )
                )
            pending_assignments[customer_ref] = variant

        intervention = treatment_spec if variant == VARIANT_TREATMENT else None
        context = PaymentContext(
            event_ref=event_ref,
            merchant_id=experiment.merchant_id,
            customer_ref=customer_ref,
            segment=experiment.segment,
            amount=amount,
            currency=TECHBAZAAR_PROFILE.currency,
            payment_method=payment_method,
            device_type=device_type,
            source=source,
        )
        outcome = simulate_outcome(context=context, intervention=intervention, seed=seed)

        created_at = RUNTIME_ANCHOR + timedelta(seconds=seq)
        completed_at = None
        if outcome.completion_seconds is not None:
            completed_at = created_at + timedelta(seconds=outcome.completion_seconds)

        db.add(
            PaymentAttempt(
                id=_attempt_id(experiment.id, seq),
                merchant_id=experiment.merchant_id,
                customer_ref=customer_ref,
                internal_order_ref=None,
                razorpay_order_id=None,
                razorpay_payment_id=None,
                razorpay_payment_link_id=None,
                amount=amount,
                currency="INR",
                payment_method=payment_method,
                status=outcome.status,
                failure_reason=outcome.failure_reason,
                device_type=device_type,
                segment=experiment.segment,
                source=source,
                experiment_id=experiment.id,
                variant=variant,
                created_at=created_at,
                completed_at=completed_at,
                is_simulated=True,
            )
        )
        generated += 1
        if variant == VARIANT_CONTROL:
            control_so_far += 1
        else:
            treatment_so_far += 1

    db.flush()
    _restore_timezone_awareness(db, experiment.id)
    return _build_summary(db, experiment, generated=generated)
