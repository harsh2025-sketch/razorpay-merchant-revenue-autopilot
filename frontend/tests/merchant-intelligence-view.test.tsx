import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MerchantIntelligenceView } from "@/components/merchant-intelligence-view";
import type { MerchantIntelligence } from "@/lib/types";

const intelligence: MerchantIntelligence = {
  merchant: { merchant_id: "merchant_techbazaar", name: "TechBazaar Electronics", category: "electronics", monthly_gmv_paise: null, created_at: null },
  portfolio: {
    merchant_id: "merchant_techbazaar",
    next_best_opportunity_id: "opp-live",
    opportunities: [{
      rank: 1, opportunity_id: "opp-live", segment: "android_budget", status: "detected", detector_severity: 0.12,
      detected_conversion_rate: 0.47, comparison_conversion_rate: 0.58, conversion_gap: 0.11, segment_attempts: 1000,
      average_captured_order_value_paise: 250000, estimated_incremental_captures: 110, estimated_recoverable_gmv_paise: 27500000,
      prior_terminal_trials: 1, history_factor: 0.5, allowed_intervention_count: 2, previously_tried_interventions: ["offer_discount"],
      untried_allowed_interventions: ["partial_payment"], policy_feasible: true, history_adjusted_gmv_proxy_paise: 13750000, priority_index: 1,
    }],
  },
  champion: {
    merchant_id: "merchant_techbazaar", version: 2, promotion_count: 1, latest_promotion_experiment_id: "exp-keep",
    configs: [{ intervention_type: "offer_discount", config: { discount_pct: 0.05 }, source_experiment_id: "exp-keep", promoted_at: "2026-08-30T10:00:00Z", absolute_lift: 0.04, p_value: 0.01 }],
  },
  memory: {
    merchant_id: "merchant_techbazaar", trial_count: 2, completed_result_count: 2, policy_rejection_count: 0, keep_count: 1, rollback_count: 0, inconclusive_count: 1,
    knowledge: [{ segment: "android_budget", intervention_type: "offer_discount", trial_count: 2, approved_count: 2, rejected_count: 0, completed_result_count: 2, keep_count: 1, rollback_count: 0, inconclusive_count: 1, latest_outcome: "statistical_keep", latest_experiment_id: "exp-keep", latest_treatment_config: { discount_pct: 0.05 }, latest_treatment_config_fingerprint: "abc", latest_absolute_lift: 0.04, latest_p_value: 0.01 }],
    records: [{ experiment_id: "exp-keep", opportunity_id: "opp-old", segment: "android_budget", intervention_type: "offer_discount", treatment_config: { discount_pct: 0.05 }, treatment_config_fingerprint: "abc", experiment_status: "completed", policy_decision: "APPROVE", policy_violations: [], statistical_decision: "KEEP", control_rate: 0.45, treatment_rate: 0.49, absolute_lift: 0.04, relative_lift: 0.088, p_value: 0.01, confidence_interval_lower: 0.01, confidence_interval_upper: 0.07, is_significant: true, treatment_resource_status: "active", terminal_reason: "statistical_keep", created_at: "2026-08-30T09:00:00Z", started_at: "2026-08-30T09:00:00Z", ended_at: "2026-08-30T10:00:00Z" }],
  },
};

describe("Merchant Intelligence view", () => {
  it("renders portfolio, champion and persisted experiment memory without fake recovery claims", () => {
    render(<MerchantIntelligenceView intelligence={intelligence} />);

    expect(screen.getByText("Opportunity Portfolio")).toBeInTheDocument();
    expect(screen.getByText("Champion v2")).toBeInTheDocument();
    expect(screen.getByText("Learned Intervention History")).toBeInTheDocument();
    expect(screen.getByText("Recent Terminal Trials")).toBeInTheDocument();
    expect(screen.getByText("Next")).toBeInTheDocument();
    expect(screen.getByText("Partial payment")).toBeInTheDocument();
    expect(screen.getAllByText("Offer discount").length).toBeGreaterThan(0);
    expect(screen.getByText("+4.0pp")).toBeInTheDocument();
    expect(screen.getByText(/not a forecast, booked revenue, or causal claim/i)).toBeInTheDocument();
    expect(screen.queryByText(/revenue recovered/i)).toBeNull();
  });
});
