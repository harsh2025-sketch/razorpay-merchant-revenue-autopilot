"""Task 12 tests: deterministic mathematics and persistence boundary."""
from datetime import datetime, timezone, timedelta
import math
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.db.models import Merchant, Hypothesis, Opportunity, Experiment, PaymentAttempt, ExperimentResult
from app.engines.statistics import evaluate_conversion_experiment, evaluate_experiment_results, StatisticalEvaluationError


def test_decisions_and_math():
    assert evaluate_conversion_experiment(experiment_id="x", control_count=1000, control_conversions=500, treatment_count=1000, treatment_conversions=600).decision == "KEEP"
    assert evaluate_conversion_experiment(experiment_id="x", control_count=1000, control_conversions=600, treatment_count=1000, treatment_conversions=500).decision == "ROLLBACK"
    r = evaluate_conversion_experiment(experiment_id="x", control_count=100, control_conversions=50, treatment_count=100, treatment_conversions=50)
    assert r.decision == "INCONCLUSIVE" and r.p_value == 1.0
    r = evaluate_conversion_experiment(experiment_id="x", control_count=10000, control_conversions=5000, treatment_count=10000, treatment_conversions=5180)
    assert r.decision == "INCONCLUSIVE"  # significant, but below two points
    r = evaluate_conversion_experiment(experiment_id="x", control_count=20, control_conversions=8, treatment_count=20, treatment_conversions=14)
    assert r.absolute_lift > .02 and r.p_value > .05 and r.decision == "INCONCLUSIVE"
    assert 0 <= r.p_value <= 1 and r.confidence_interval_lower <= r.confidence_interval_upper
    assert r.is_significant == (r.p_value < .05)
    assert math.isclose(r.relative_lift, .75)
    assert evaluate_conversion_experiment(experiment_id="x", control_count=10, control_conversions=0, treatment_count=10, treatment_conversions=2).relative_lift is None
    assert evaluate_conversion_experiment(experiment_id="x", control_count=10, control_conversions=0, treatment_count=10, treatment_conversions=0).p_value == 1
    assert evaluate_conversion_experiment(experiment_id="x", control_count=10, control_conversions=10, treatment_count=10, treatment_conversions=10).p_value == 1

@pytest.mark.parametrize("kwargs", [
    dict(control_count=0), dict(control_conversions=-1), dict(control_conversions=11),
    dict(control_count=True), dict(alpha=0), dict(alpha=1), dict(alpha=float('nan')), dict(alpha=float('inf')),
    dict(practical_absolute_lift=-1), dict(practical_absolute_lift=float('nan')), dict(practical_absolute_lift=float('inf')),
])
def test_invalid_inputs(kwargs):
    args = dict(experiment_id="x", control_count=10, control_conversions=2, treatment_count=10, treatment_conversions=3)
    args.update(kwargs)
    with pytest.raises(StatisticalEvaluationError): evaluate_conversion_experiment(**args)

def test_known_formula_and_determinism():
    args = dict(experiment_id="x", control_count=100, control_conversions=20, treatment_count=120, treatment_conversions=36)
    a = evaluate_conversion_experiment(**args); b = evaluate_conversion_experiment(**args)
    pooled=56/220; se=math.sqrt(pooled*(1-pooled)*(1/100+1/120)); expected=math.erfc(abs(.10/se)/math.sqrt(2))
    assert math.isclose(a.p_value, expected, rel_tol=1e-12)
    assert a == b

def test_old_small_treatment_demo_is_not_tiny():
    r=evaluate_conversion_experiment(experiment_id="x", control_count=180, control_conversions=92, treatment_count=20, treatment_conversions=13)
    assert r.p_value > .10

@pytest.fixture
def db(tmp_path):
    e=create_engine(f"sqlite:///{tmp_path/'x.db'}"); Base.metadata.create_all(e); s=sessionmaker(bind=e)()
    m=Merchant(id="m",name="m"); s.add(m); o=Opportunity(merchant_id="m",type="x",severity=1,detected_metric="x",evidence={}); s.add(o); s.flush()
    h=Hypothesis(opportunity_id=o.id,merchant_id="m",hypothesis_text="x",intervention_type="x",intervention_params={},evidence_refs=[]); s.add(h); s.flush()
    x=Experiment(merchant_id="m",hypothesis_id=h.id,opportunity_id=o.id,name="x",segment="x",intervention_type="x",control_config={},treatment_config={},traffic_split_treatment_pct=.5,primary_metric="conversion_rate",guardrail_metrics=[],min_sample_per_variant=2,max_duration_hours=1,status="running"); s.add(x); s.flush()
    yield s,x
    s.close(); e.dispose()

def test_persistence_duplicate_counts_denominator_and_no_commit(db, monkeypatch):
    s,x=db; base=datetime(2020,1,1,tzinfo=timezone.utc)
    for i,(v,status) in enumerate([("control","captured"),("control","failed"),("treatment","captured"),("treatment","abandoned")]):
        s.add(PaymentAttempt(merchant_id="m",experiment_id=x.id,variant=v,status=status,amount=1,created_at=base+timedelta(seconds=i)))
    s.add(PaymentAttempt(merchant_id="m",variant="control",status="captured",amount=1,created_at=base+timedelta(days=1)))
    s.flush(); calls=[]; monkeypatch.setattr(s,"commit",lambda: calls.append(1))
    r=evaluate_experiment_results(s,x.id); assert calls==[] and r.control_count==2 and r.treatment_count==2 and r.control_conversions==1
    assert x.status=="completed" and x.ended_at==base+timedelta(seconds=3)
    assert evaluate_experiment_results(s,x.id).id==r.id and s.query(ExperimentResult).count()==1

def test_insufficient_does_not_finalize(db):
    s,x=db; s.add(PaymentAttempt(merchant_id="m",experiment_id=x.id,variant="control",status="captured",amount=1)); s.flush()
    with pytest.raises(StatisticalEvaluationError): evaluate_experiment_results(s,x.id)
    assert x.status=="running" and s.query(ExperimentResult).count()==0

def test_invalid_variant_and_missing_nonrunning(db):
    s,x=db; s.add(PaymentAttempt(merchant_id="m",experiment_id=x.id,variant="bogus",status="failed",amount=1)); s.flush()
    with pytest.raises(StatisticalEvaluationError): evaluate_experiment_results(s,x.id)
    with pytest.raises(StatisticalEvaluationError): evaluate_experiment_results(s,"missing")
    x.status="completed"; s.flush()
    with pytest.raises(StatisticalEvaluationError): evaluate_experiment_results(s,x.id)

def test_statistics_has_no_forbidden_imports():
    text=Path(__file__).parents[1].joinpath("app/engines/statistics.py").read_text()
    for forbidden in ("causal_model", "OpenAI", "RazorpayClient", "engines.policy", "simulate_outcome"):
        assert forbidden not in text
