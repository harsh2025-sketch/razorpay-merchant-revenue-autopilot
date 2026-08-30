import pytest
from app.db.models import Experiment, MerchantPolicy, PolicyDecision, Merchant
from app.engines.policy import (
    evaluate_policy,
    evaluate_experiment_policy,
    PolicyEvaluationError,
    VIOLATION_INTERVENTION_NOT_ALLOWED,
    VIOLATION_TREATMENT_EXPOSURE_EXCEEDED,
    VIOLATION_DISCOUNT_LIMIT_EXCEEDED,
    VIOLATION_MIN_MARGIN_VIOLATED,
    VIOLATION_FINANCIAL_EXPOSURE_EXCEEDED,
    VIOLATION_MIN_SAMPLE_NOT_MET,
    VIOLATION_DURATION_EXCEEDED,
    VIOLATION_CONCURRENT_EXPERIMENT_LIMIT,
    VIOLATION_SEGMENT_EXPERIMENT_CONFLICT,
    VIOLATION_INVALID_EXPERIMENT_CONFIG,
)
from app.schemas.policy import PolicyEvaluation

def get_base_experiment():
    return Experiment(
        id="exp-base",
        merchant_id="m-1",
        opportunity_id="o-1",
        hypothesis_id="h-1",
        name="test",
        segment="desktop",
        intervention_type="offer_discount",
        control_config={"offer": None},
        treatment_config={"discount_pct": 0.05},
        traffic_split_treatment_pct=0.10,
        primary_metric="cv",
        min_sample_per_variant=200,
        max_duration_hours=168,
        status="proposed"
    )

def get_base_policy():
    return MerchantPolicy(
        merchant_id="m-1",
        allowed_interventions=["offer_discount", "payment_method_config", "partial_payment", "expiry_config"],
        max_experiment_exposure_pct=0.10,
        max_discount_pct=0.15,
        min_margin_pct=0.05,
        max_concurrent_experiments=3,
        max_experiment_duration_hours=168,
        min_sample_size=30,
        max_financial_exposure=50000,
    )

def test_1_to_4_normal_plan_approves():
    exp = get_base_experiment()
    pol = get_base_policy()
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[], estimated_segment_attempts=100, average_attempt_amount_paise=100000.0)
    assert res.decision == "APPROVE"
    assert res.violations == []
    assert res.final_params == res.original_params

def test_5_to_8_discount_limit_rejects():
    exp = get_base_experiment()
    exp.treatment_config = {"discount_pct": 0.20}
    pol = get_base_policy()
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[], estimated_segment_attempts=0, average_attempt_amount_paise=0.0)
    assert res.decision == "REJECT"
    assert VIOLATION_DISCOUNT_LIMIT_EXCEEDED in res.violations
    assert res.final_params is None

def test_9_to_11_treatment_exposure():
    exp = get_base_experiment()
    exp.traffic_split_treatment_pct = 0.15
    pol = get_base_policy()
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[], estimated_segment_attempts=0, average_attempt_amount_paise=0.0)
    assert VIOLATION_TREATMENT_EXPOSURE_EXCEEDED in res.violations

    exp.traffic_split_treatment_pct = 0.10
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[], estimated_segment_attempts=0, average_attempt_amount_paise=0.0)
    assert VIOLATION_TREATMENT_EXPOSURE_EXCEEDED not in res.violations

    exp.traffic_split_treatment_pct = 0
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[], estimated_segment_attempts=0, average_attempt_amount_paise=0.0)
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG in res.violations

def test_12_to_14_intervention_allowlist():
    exp = get_base_experiment()
    exp.intervention_type = "payment_method_config"
    exp.treatment_config = {"payment_methods": {"card": True}}
    pol = get_base_policy()
    pol.allowed_interventions = ["offer_discount"]
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INTERVENTION_NOT_ALLOWED in res.violations

    pol.allowed_interventions = []
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INTERVENTION_NOT_ALLOWED in res.violations
    
    pol.allowed_interventions = None # malformed
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INTERVENTION_NOT_ALLOWED in res.violations

def test_15_to_16_min_sample():
    exp = get_base_experiment()
    exp.min_sample_per_variant = 20
    pol = get_base_policy()
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_MIN_SAMPLE_NOT_MET in res.violations

    exp.min_sample_per_variant = 30
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_MIN_SAMPLE_NOT_MET not in res.violations

def test_17_to_19_duration():
    exp = get_base_experiment()
    exp.max_duration_hours = 200
    pol = get_base_policy()
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_DURATION_EXCEEDED in res.violations

    exp.max_duration_hours = 168
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_DURATION_EXCEEDED not in res.violations

    exp.max_duration_hours = 0
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG in res.violations

def test_20_to_22_concurrent_experiments():
    exp = get_base_experiment()
    pol = get_base_policy()
    pol.max_concurrent_experiments = 1
    act = [Experiment(id="act1", segment="other", status="running")]
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=act)
    assert VIOLATION_CONCURRENT_EXPERIMENT_LIMIT in res.violations
    
    pol.max_concurrent_experiments = 2
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=act)
    assert VIOLATION_CONCURRENT_EXPERIMENT_LIMIT not in res.violations

def test_23_to_25_segment_conflict():
    exp = get_base_experiment()
    pol = get_base_policy()
    
    act_diff = [Experiment(id="act1", segment="other", status="running")]
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=act_diff)
    assert VIOLATION_SEGMENT_EXPERIMENT_CONFLICT not in res.violations

    act_same = [Experiment(id="act2", segment="desktop", status="approved")]
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=act_same)
    assert VIOLATION_SEGMENT_EXPERIMENT_CONFLICT in res.violations

def test_26_to_27_payment_method_config():
    exp = get_base_experiment()
    exp.intervention_type = "payment_method_config"
    exp.control_config = {"payment_methods": "merchant_default"}
    pol = get_base_policy()
    
    exp.treatment_config = {"invalid": "shape"}
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG in res.violations

    exp.treatment_config = {"payment_methods": {"card": True, "upi": False}}
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG not in res.violations

def test_28_to_29_offer_config():
    exp = get_base_experiment()
    pol = get_base_policy()
    
    exp.treatment_config = {"discount_pct": 0.60}
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG in res.violations

    exp.treatment_config = {"discount_pct": 0.50}
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG not in res.violations

def test_30_to_31_partial_payment():
    exp = get_base_experiment()
    exp.intervention_type = "partial_payment"
    exp.control_config = {"accept_partial": False}
    pol = get_base_policy()
    
    exp.treatment_config = {"accept_partial": "yes"}
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG in res.violations

    exp.treatment_config = {"accept_partial": True, "first_min_partial_amount_pct": 0.5}
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG not in res.violations

def test_32_to_33_expiry_config():
    exp = get_base_experiment()
    exp.intervention_type = "expiry_config"
    exp.control_config = {"expiry_hours": "merchant_default"}
    pol = get_base_policy()
    
    exp.treatment_config = {"expiry_hours": -1}
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG in res.violations

    exp.treatment_config = {"expiry_hours": 24}
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG not in res.violations

def test_34_to_35_min_margin_proxy():
    exp = get_base_experiment()
    pol = get_base_policy()
    
    # Below policy min
    exp.treatment_config = {"discount_pct": 0.05, "estimated_margin_pct": 0.02}
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_MIN_MARGIN_VIOLATED in res.violations
    
    # Missing, shouldn't flag
    exp.treatment_config = {"discount_pct": 0.05}
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_MIN_MARGIN_VIOLATED not in res.violations

def test_36_to_40_financial_exposure():
    exp = get_base_experiment()
    pol = get_base_policy()
    
    res = evaluate_policy(
        experiment=exp,
        policy=pol,
        active_experiments=[],
        estimated_segment_attempts=100,
        average_attempt_amount_paise=100000.0,
    )
    # 0.05 * 100 * 100000.0 * 0.10 = 50000 (<= 50000)
    assert VIOLATION_FINANCIAL_EXPOSURE_EXCEEDED not in res.violations

    res = evaluate_policy(
        experiment=exp,
        policy=pol,
        active_experiments=[],
        estimated_segment_attempts=200,
        average_attempt_amount_paise=100000.0,
    )
    # 0.05 * 200 * 100000.0 * 0.10 = 100000 (> 50000)
    assert VIOLATION_FINANCIAL_EXPOSURE_EXCEEDED in res.violations

def test_boolean_numeric_safety():
    pol = get_base_policy()
    
    exp = get_base_experiment()
    exp.treatment_config = {"discount_pct": True}
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG in res.violations

    exp = get_base_experiment()
    exp.traffic_split_treatment_pct = True
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG in res.violations

    exp = get_base_experiment()
    exp.intervention_type = "expiry_config"
    exp.control_config = {"expiry_hours": "merchant_default"}
    exp.treatment_config = {"expiry_hours": True}
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG in res.violations

    exp = get_base_experiment()
    exp.intervention_type = "partial_payment"
    exp.control_config = {"accept_partial": False}
    exp.treatment_config = {"accept_partial": True, "first_min_partial_amount_pct": True}
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG in res.violations

    exp = get_base_experiment()
    exp.min_sample_per_variant = True
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG in res.violations

    exp = get_base_experiment()
    exp.max_duration_hours = True
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG in res.violations

    exp = get_base_experiment()
    exp.treatment_config = {"discount_pct": 0.20} # DISCOUNT_LIMIT_EXCEEDED
    exp.traffic_split_treatment_pct = 0.50 # TREATMENT_EXPOSURE_EXCEEDED
    exp.max_duration_hours = 500 # DURATION_EXCEEDED
    pol = get_base_policy()
    
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert len(res.violations) >= 3
    assert VIOLATION_DISCOUNT_LIMIT_EXCEEDED in res.violations
    assert VIOLATION_TREATMENT_EXPOSURE_EXCEEDED in res.violations
    assert VIOLATION_DURATION_EXCEEDED in res.violations

def test_finite_number_safety():
    pol = get_base_policy()
    
    # 1-2. discount_pct = nan / inf
    exp = get_base_experiment()
    exp.treatment_config = {"discount_pct": float("nan")}
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG in res.violations

    exp = get_base_experiment()
    exp.treatment_config = {"discount_pct": float("inf")}
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG in res.violations

    # 3-4. traffic_split_treatment_pct = nan / inf
    exp = get_base_experiment()
    exp.traffic_split_treatment_pct = float("nan")
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG in res.violations

    exp = get_base_experiment()
    exp.traffic_split_treatment_pct = float("inf")
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG in res.violations

    # 5-6. expiry_hours = nan / inf
    exp = get_base_experiment()
    exp.intervention_type = "expiry_config"
    exp.control_config = {"expiry_hours": "merchant_default"}
    exp.treatment_config = {"expiry_hours": float("nan")}
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG in res.violations

    exp = get_base_experiment()
    exp.intervention_type = "expiry_config"
    exp.control_config = {"expiry_hours": "merchant_default"}
    exp.treatment_config = {"expiry_hours": float("inf")}
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG in res.violations

    # 7. first_min_partial_amount_pct = nan
    exp = get_base_experiment()
    exp.intervention_type = "partial_payment"
    exp.control_config = {"accept_partial": False}
    exp.treatment_config = {"accept_partial": True, "first_min_partial_amount_pct": float("nan")}
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG in res.violations

    # 8. estimated_margin_pct = nan
    exp = get_base_experiment()
    exp.treatment_config = {"discount_pct": 0.05, "estimated_margin_pct": float("nan")}
    res = evaluate_policy(experiment=exp, policy=pol, active_experiments=[])
    assert VIOLATION_INVALID_EXPERIMENT_CONFIG in res.violations

    # 9-11. financial exposure historical inputs not finite -> skips calculation, may APPROVE if otherwise safe
    exp = get_base_experiment()
    res = evaluate_policy(
        experiment=exp, 
        policy=pol, 
        active_experiments=[],
        estimated_segment_attempts=100,
        average_attempt_amount_paise=float("nan")
    )
    assert res.decision == "APPROVE"
    assert VIOLATION_FINANCIAL_EXPOSURE_EXCEEDED not in res.violations

    res = evaluate_policy(
        experiment=exp, 
        policy=pol, 
        active_experiments=[],
        estimated_segment_attempts=100,
        average_attempt_amount_paise=float("inf")
    )
    assert res.decision == "APPROVE"
    assert VIOLATION_FINANCIAL_EXPOSURE_EXCEEDED not in res.violations

    res = evaluate_policy(
        experiment=exp, 
        policy=pol, 
        active_experiments=[],
        estimated_segment_attempts=float("nan"),
        average_attempt_amount_paise=100000.0
    )
    assert res.decision == "APPROVE"
    assert VIOLATION_FINANCIAL_EXPOSURE_EXCEEDED not in res.violations

def test_45_to_55_db_wrapper():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.base import Base
    
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    
    with pytest.raises(PolicyEvaluationError, match="not found"):
        evaluate_experiment_policy(db, "no")
        
    merchant = Merchant(id="m-1", name="M1")
    db.add(merchant)
    
    exp = get_base_experiment()
    db.add(exp)
    db.commit()
    
    with pytest.raises(PolicyEvaluationError, match="MerchantPolicy"):
        evaluate_experiment_policy(db, exp.id)
        
    pol = get_base_policy()
    db.add(pol)
    db.commit()
    
    decision = evaluate_experiment_policy(db, exp.id)
    assert decision.decision == "APPROVE"
    assert exp.status == "approved"
    
    decision2 = evaluate_experiment_policy(db, exp.id)
    assert decision.id == decision2.id
    
    # Invalid lifecycle
    exp.status = "running"
    db.commit()
    db.delete(decision)
    db.commit()
    with pytest.raises(PolicyEvaluationError, match="invalid status"):
        evaluate_experiment_policy(db, exp.id)

def test_no_commit_in_engine():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.base import Base
    
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    
    merchant = Merchant(id="m-1", name="M1")
    db.add(merchant)
    
    pol = get_base_policy()
    db.add(pol)
    
    exp = get_base_experiment()
    db.add(exp)
    db.commit() # setup commit
    
    # monkeypatch commit
    def fake_commit():
        raise AssertionError("commit was called!")
    
    original_commit = db.commit
    db.commit = fake_commit
    
    decision = evaluate_experiment_policy(db, exp.id)
    assert decision.decision == "APPROVE"
    
    # restore and double check we can actually commit
    db.commit = original_commit
    db.commit()
