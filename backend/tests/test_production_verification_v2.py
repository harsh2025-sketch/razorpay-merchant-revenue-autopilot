from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location(
    "verify_production_v2", SCRIPTS / "verify_production_v2.py"
)
assert spec is not None and spec.loader is not None
v2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v2)


def _intelligence(*, outcome: str = "INCONCLUSIVE") -> dict:
    return {
        "memory": {
            "merchant_id": "merchant_techbazaar",
            "trial_count": 1,
            "completed_result_count": 1,
            "policy_rejection_count": 0,
            "keep_count": int(outcome == "KEEP"),
            "rollback_count": int(outcome == "ROLLBACK"),
            "inconclusive_count": int(outcome == "INCONCLUSIVE"),
            "knowledge": [],
            "records": [
                {
                    "experiment_id": "exp-1",
                    "opportunity_id": "opp-1",
                    "segment": "android_budget",
                    "intervention_type": "partial_payment",
                    "treatment_config": {"partial_payment": True},
                    "statistical_decision": outcome,
                    "policy_decision": "APPROVE",
                }
            ],
        },
        "champion": {
            "merchant_id": "merchant_techbazaar",
            "version": 2 if outcome == "KEEP" else 1,
            "promotion_count": 1 if outcome == "KEEP" else 0,
            "latest_promotion_experiment_id": "exp-1" if outcome == "KEEP" else None,
            "configs": (
                [
                    {
                        "intervention_type": "partial_payment",
                        "config": {"partial_payment": True},
                        "source_experiment_id": "exp-1",
                        "promoted_at": "2026-08-30T00:00:00Z",
                        "absolute_lift": 0.04,
                        "p_value": 0.01,
                    }
                ]
                if outcome == "KEEP"
                else []
            ),
        },
        "portfolio": {
            "merchant_id": "merchant_techbazaar",
            "next_best_opportunity_id": "opp-new",
            "opportunities": [
                {
                    "rank": 1,
                    "opportunity_id": "opp-new",
                    "policy_feasible": True,
                    "priority_index": 1.0,
                    "estimated_recoverable_gmv_paise": 1000,
                    "history_adjusted_gmv_proxy_paise": 500,
                }
            ],
        },
    }


def test_material_evidence_change_honors_exact_two_pp_boundary():
    prior = {
        "evidence": {
            "absolute_gap": 0.10,
            "segment_conversion_rate": 0.45,
            "comparison_conversion_rate": 0.55,
            "segment_attempts": 100,
        }
    }
    current = {
        "evidence": {
            "absolute_gap": 0.12,
            "segment_conversion_rate": 0.45,
            "comparison_conversion_rate": 0.55,
            "segment_attempts": 100,
        }
    }
    assert v2._material_evidence_changed(current, prior) is True


def test_material_evidence_change_requires_both_sample_growth_thresholds():
    prior = {"evidence": {"segment_attempts": 1000}}
    assert v2._material_evidence_changed(
        {"evidence": {"segment_attempts": 1099}}, prior
    ) is False
    assert v2._material_evidence_changed(
        {"evidence": {"segment_attempts": 1150}}, prior
    ) is False
    assert v2._material_evidence_changed(
        {"evidence": {"segment_attempts": 1200}}, prior
    ) is True


def test_champion_source_must_be_a_keep_record():
    intelligence = _intelligence(outcome="KEEP")
    memory = v2._assert_memory_shape(intelligence, min_trials=1)
    champion = v2._assert_champion_shape(intelligence, memory)
    assert champion["version"] == 2

    intelligence["memory"]["records"][0]["statistical_decision"] = "INCONCLUSIVE"
    intelligence["memory"]["keep_count"] = 0
    intelligence["memory"]["inconclusive_count"] = 1
    memory = v2._assert_memory_shape(intelligence, min_trials=1)
    with pytest.raises(v2.VerificationFailure, match="non-KEEP"):
        v2._assert_champion_shape(intelligence, memory)


def test_portfolio_next_best_must_match_first_feasible_rank():
    intelligence = _intelligence()
    portfolio = v2._assert_portfolio_shape(intelligence)
    assert portfolio["next_best_opportunity_id"] == "opp-new"

    intelligence["portfolio"]["next_best_opportunity_id"] = "opp-other"
    with pytest.raises(v2.VerificationFailure, match="next_best"):
        v2._assert_portfolio_shape(intelligence)


def test_stale_unchanged_inconclusive_semantic_repeat_is_rejected(monkeypatch):
    prior_cycle = {
        "opportunity": {
            "id": "opp-1",
            "segment": "android_budget",
            "evidence": {
                "absolute_gap": 0.10,
                "segment_conversion_rate": 0.45,
                "comparison_conversion_rate": 0.55,
                "segment_attempts": 1000,
            },
        },
        "hypothesis": {
            "intervention_type": "partial_payment",
            "intervention_params": {"min_first_payment_pct": 0.25},
        },
    }
    current_cycle = {
        "opportunity": {
            "id": "opp-new",
            "segment": "android_budget",
            "evidence": {
                "absolute_gap": 0.105,
                "segment_conversion_rate": 0.451,
                "comparison_conversion_rate": 0.556,
                "segment_attempts": 1050,
            },
        },
        "hypothesis": {
            "intervention_type": "partial_payment",
            "intervention_params": {"min_first_payment_pct": 0.25},
        },
    }
    monkeypatch.setattr(v2, "_cycle", lambda _base, _opp: prior_cycle)

    with pytest.raises(v2.VerificationFailure, match="unchanged exact INCONCLUSIVE"):
        v2._assert_no_blocked_stale_repeat(
            "https://example.invalid",
            current_cycle=current_cycle,
            prior_memory_records=[
                {
                    "experiment_id": "exp-1",
                    "opportunity_id": "opp-1",
                    "segment": "android_budget",
                    "intervention_type": "partial_payment",
                    "statistical_decision": "INCONCLUSIVE",
                    "policy_decision": "APPROVE",
                }
            ],
        )


def test_same_inconclusive_proposal_is_allowed_after_material_evidence_change(monkeypatch):
    prior_cycle = {
        "opportunity": {
            "id": "opp-1",
            "segment": "android_budget",
            "evidence": {"absolute_gap": 0.10, "segment_attempts": 1000},
        },
        "hypothesis": {
            "intervention_type": "partial_payment",
            "intervention_params": {"min_first_payment_pct": 0.25},
        },
    }
    current_cycle = {
        "opportunity": {
            "id": "opp-new",
            "segment": "android_budget",
            "evidence": {"absolute_gap": 0.13, "segment_attempts": 1000},
        },
        "hypothesis": {
            "intervention_type": "partial_payment",
            "intervention_params": {"min_first_payment_pct": 0.25},
        },
    }
    monkeypatch.setattr(v2, "_cycle", lambda _base, _opp: prior_cycle)

    v2._assert_no_blocked_stale_repeat(
        "https://example.invalid",
        current_cycle=current_cycle,
        prior_memory_records=[
            {
                "experiment_id": "exp-1",
                "opportunity_id": "opp-1",
                "segment": "android_budget",
                "intervention_type": "partial_payment",
                "statistical_decision": "INCONCLUSIVE",
                "policy_decision": "APPROVE",
            }
        ],
    )
