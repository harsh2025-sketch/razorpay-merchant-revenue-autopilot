import copy
import math
from typing import Any
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.db.models import Experiment, MerchantPolicy, PolicyDecision, PaymentAttempt
from app.schemas.policy import PolicyEvaluation
from app.services.audit import (
    ACTOR_POLICY,
    ENTITY_EXPERIMENT,
    POLICY_APPROVED,
    POLICY_REJECTED,
    record_audit_event_once,
)

class PolicyEvaluationError(Exception):
    """Exception raised for lifecycle errors or missing policy during evaluation."""
    pass

VIOLATION_INTERVENTION_NOT_ALLOWED = "INTERVENTION_NOT_ALLOWED"
VIOLATION_TREATMENT_EXPOSURE_EXCEEDED = "TREATMENT_EXPOSURE_EXCEEDED"
VIOLATION_DISCOUNT_LIMIT_EXCEEDED = "DISCOUNT_LIMIT_EXCEEDED"
VIOLATION_MIN_MARGIN_VIOLATED = "MIN_MARGIN_VIOLATED"
VIOLATION_FINANCIAL_EXPOSURE_EXCEEDED = "FINANCIAL_EXPOSURE_EXCEEDED"
VIOLATION_MIN_SAMPLE_NOT_MET = "MIN_SAMPLE_NOT_MET"
VIOLATION_DURATION_EXCEEDED = "DURATION_EXCEEDED"
VIOLATION_CONCURRENT_EXPERIMENT_LIMIT = "CONCURRENT_EXPERIMENT_LIMIT"
VIOLATION_SEGMENT_EXPERIMENT_CONFLICT = "SEGMENT_EXPERIMENT_CONFLICT"
VIOLATION_INVALID_EXPERIMENT_CONFIG = "INVALID_EXPERIMENT_CONFIG"

def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)

def _validate_config(intervention_type: str, control: dict[str, object], treatment: dict[str, object]) -> bool:
    if intervention_type == "payment_method_config":
        if control != {"payment_methods": "merchant_default"}:
            return False
        
        if "payment_methods" not in treatment or not isinstance(treatment["payment_methods"], dict):
            return False
        
        allowed_keys = {"card", "upi", "netbanking", "wallet"}
        methods = treatment["payment_methods"]
        if not methods:
            return False
        
        if set(treatment.keys()) != {"payment_methods"}:
            return False
            
        for k, v in methods.items():
            if k not in allowed_keys or not isinstance(v, bool):
                return False
        return True

    elif intervention_type == "offer_discount":
        if control != {"offer": None}:
            return False
            
        if "discount_pct" not in treatment or not _is_finite_number(treatment["discount_pct"]):
            return False
            
        discount = treatment["discount_pct"]
        if discount <= 0 or discount > 0.50:
            return False
            
        allowed_keys = {"discount_pct", "estimated_margin_pct"}
        if not set(treatment.keys()).issubset(allowed_keys):
            return False
            
        if "estimated_margin_pct" in treatment:
            if not _is_finite_number(treatment["estimated_margin_pct"]):
                return False
                
        return True

    elif intervention_type == "partial_payment":
        if control != {"accept_partial": False}:
            return False
            
        if "accept_partial" not in treatment or not isinstance(treatment["accept_partial"], bool):
            return False
            
        allowed_keys = {"accept_partial", "first_min_partial_amount_pct"}
        if not set(treatment.keys()).issubset(allowed_keys):
            return False
            
        if "first_min_partial_amount_pct" in treatment:
            pct = treatment["first_min_partial_amount_pct"]
            if not _is_finite_number(pct) or pct <= 0 or pct > 1 or not treatment["accept_partial"]:
                return False
                
        return True

    elif intervention_type == "expiry_config":
        if control != {"expiry_hours": "merchant_default"}:
            return False
            
        if "expiry_hours" not in treatment or not _is_finite_number(treatment["expiry_hours"]):
            return False
            
        if set(treatment.keys()) != {"expiry_hours"}:
            return False
            
        h = treatment["expiry_hours"]
        if h <= 0 or h > 4320:
            return False
            
        return True

    return False

def evaluate_policy(
    *,
    experiment: Experiment,
    policy: MerchantPolicy,
    active_experiments: list[Experiment],
    estimated_segment_attempts: int = 0,
    average_attempt_amount_paise: float = 0.0,
) -> PolicyEvaluation:
    violations = []
    
    # 1. Config Validity
    if experiment.intervention_type not in ["payment_method_config", "offer_discount", "partial_payment", "expiry_config"]:
        violations.append(VIOLATION_INVALID_EXPERIMENT_CONFIG)
    else:
        if not _validate_config(experiment.intervention_type, experiment.control_config, experiment.treatment_config):
            violations.append(VIOLATION_INVALID_EXPERIMENT_CONFIG)
                
    if not _is_finite_number(experiment.traffic_split_treatment_pct) or experiment.traffic_split_treatment_pct <= 0:
        if VIOLATION_INVALID_EXPERIMENT_CONFIG not in violations:
            violations.append(VIOLATION_INVALID_EXPERIMENT_CONFIG)
            
    if not _is_finite_number(experiment.max_duration_hours) or experiment.max_duration_hours <= 0:
        if VIOLATION_INVALID_EXPERIMENT_CONFIG not in violations:
            violations.append(VIOLATION_INVALID_EXPERIMENT_CONFIG)

    if not isinstance(experiment.min_sample_per_variant, int) or isinstance(experiment.min_sample_per_variant, bool) or experiment.min_sample_per_variant <= 0:
        if VIOLATION_INVALID_EXPERIMENT_CONFIG not in violations:
            violations.append(VIOLATION_INVALID_EXPERIMENT_CONFIG)
    else:
        # 6. MIN_SAMPLE_NOT_MET
        if experiment.min_sample_per_variant < policy.min_sample_size:
            violations.append(VIOLATION_MIN_SAMPLE_NOT_MET)

    # 1. INTERVENTION_NOT_ALLOWED
    allowed = policy.allowed_interventions
    if not isinstance(allowed, list) or experiment.intervention_type not in allowed:
        violations.append(VIOLATION_INTERVENTION_NOT_ALLOWED)

    # 2. TREATMENT_EXPOSURE_EXCEEDED
    if _is_finite_number(experiment.traffic_split_treatment_pct) and experiment.traffic_split_treatment_pct > 0:
        if experiment.traffic_split_treatment_pct > policy.max_experiment_exposure_pct:
            violations.append(VIOLATION_TREATMENT_EXPOSURE_EXCEEDED)

    # 3. DISCOUNT_LIMIT_EXCEEDED & 4. MIN_MARGIN_VIOLATED & 5. FINANCIAL_EXPOSURE_EXCEEDED
    treatment = experiment.treatment_config
    if experiment.intervention_type == "offer_discount":
        if "discount_pct" in treatment and _is_finite_number(treatment["discount_pct"]):
            discount = treatment["discount_pct"]
            if discount > policy.max_discount_pct:
                violations.append(VIOLATION_DISCOUNT_LIMIT_EXCEEDED)
                
            if "estimated_margin_pct" in treatment and _is_finite_number(treatment["estimated_margin_pct"]):
                margin = treatment["estimated_margin_pct"]
                if margin < policy.min_margin_pct:
                    violations.append(VIOLATION_MIN_MARGIN_VIOLATED)
                    
            if _is_finite_number(experiment.traffic_split_treatment_pct) and experiment.traffic_split_treatment_pct > 0:
                traffic = experiment.traffic_split_treatment_pct
                if _is_finite_number(average_attempt_amount_paise) and _is_finite_number(estimated_segment_attempts):
                    exposure = discount * estimated_segment_attempts * average_attempt_amount_paise * traffic
                    if exposure > policy.max_financial_exposure:
                        violations.append(VIOLATION_FINANCIAL_EXPOSURE_EXCEEDED)

    # 7. DURATION_EXCEEDED
    if _is_finite_number(experiment.max_duration_hours) and experiment.max_duration_hours > 0:
        if experiment.max_duration_hours > policy.max_experiment_duration_hours:
            violations.append(VIOLATION_DURATION_EXCEEDED)

    # 8. CONCURRENT_EXPERIMENT_LIMIT
    if len(active_experiments) >= policy.max_concurrent_experiments:
        violations.append(VIOLATION_CONCURRENT_EXPERIMENT_LIMIT)

    # 9. SEGMENT_EXPERIMENT_CONFLICT
    for act in active_experiments:
        if act.segment == experiment.segment:
            violations.append(VIOLATION_SEGMENT_EXPERIMENT_CONFLICT)
            break
            
    original_params = {
        "intervention_type": experiment.intervention_type,
        "treatment_config": copy.deepcopy(experiment.treatment_config),
        "traffic_split_treatment_pct": experiment.traffic_split_treatment_pct,
        "min_sample_per_variant": experiment.min_sample_per_variant,
        "max_duration_hours": experiment.max_duration_hours,
    }

    if violations:
        ordered_violations = list(dict.fromkeys(violations))
        return PolicyEvaluation(
            decision="REJECT",
            violations=ordered_violations,
            original_params=original_params,
            final_params=None
        )
    
    return PolicyEvaluation(
        decision="APPROVE",
        violations=[],
        original_params=original_params,
        final_params=copy.deepcopy(original_params)
    )

def evaluate_experiment_policy(db: Session, experiment_id: str) -> PolicyDecision:
    experiment = db.execute(
        select(Experiment).where(Experiment.id == experiment_id)
    ).scalar_one_or_none()
    
    if not experiment:
        raise PolicyEvaluationError(f"Experiment {experiment_id} not found.")

    existing_decision = db.execute(
        select(PolicyDecision).where(PolicyDecision.experiment_id == experiment_id)
    ).scalars().first()
    
    if existing_decision:
        return existing_decision

    if experiment.status != "proposed":
        raise PolicyEvaluationError(f"Experiment {experiment_id} has invalid status {experiment.status}.")
        
    policy = db.execute(
        select(MerchantPolicy).where(MerchantPolicy.merchant_id == experiment.merchant_id)
    ).scalar_one_or_none()
    
    if not policy:
        raise PolicyEvaluationError(f"MerchantPolicy for merchant {experiment.merchant_id} not found.")

    active_experiments = list(db.execute(
        select(Experiment).where(
            Experiment.merchant_id == experiment.merchant_id,
            Experiment.status.in_(["approved", "running"]),
            Experiment.id != experiment_id
        )
    ).scalars())

    attempts_count = db.execute(
        select(func.count(PaymentAttempt.id)).where(
            PaymentAttempt.merchant_id == experiment.merchant_id,
            PaymentAttempt.segment == experiment.segment
        )
    ).scalar() or 0

    avg_amount = db.execute(
        select(func.avg(PaymentAttempt.amount)).where(
            PaymentAttempt.merchant_id == experiment.merchant_id,
            PaymentAttempt.segment == experiment.segment
        )
    ).scalar() or 0.0

    eval_result = evaluate_policy(
        experiment=experiment,
        policy=policy,
        active_experiments=active_experiments,
        estimated_segment_attempts=attempts_count,
        average_attempt_amount_paise=float(avg_amount),
    )

    decision = PolicyDecision(
        experiment_id=experiment_id,
        merchant_id=experiment.merchant_id,
        decision=eval_result.decision,
        violations=eval_result.violations,
        original_params=eval_result.original_params,
        final_params=eval_result.final_params,
    )
    
    db.add(decision)
    
    if eval_result.decision == "APPROVE":
        experiment.status = "approved"
        event_type = POLICY_APPROVED
    else:
        experiment.status = "rejected"
        event_type = POLICY_REJECTED

    db.flush()
    record_audit_event_once(
        db,
        merchant_id=experiment.merchant_id,
        event_type=event_type,
        entity_type=ENTITY_EXPERIMENT,
        entity_id=experiment.id,
        data={"violations": list(eval_result.violations)},
        actor=ACTOR_POLICY,
    )
    return decision

