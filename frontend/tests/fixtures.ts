import type {
  AuditEvent,
  AutopilotCycle,
  AutopilotStatus,
  Experiment,
  ExperimentProgress,
  ExperimentResult,
  Hypothesis,
  MerchantOverview,
  MerchantPolicyPublic,
  Opportunity,
  PaymentMethodMetrics,
  PolicyDecision,
  RazorpayResource,
  SegmentMetrics,
} from "@/lib/types";

/** API-shaped fixtures mirroring backend/app/api/schemas.py. */

export const segmentMetrics: SegmentMetrics[] = ([
  ["android_budget", 2141, 1023, 0.4778],
  ["android_mid", 1522, 801, 0.5263],
  ["web_general", 917, 459, 0.5005],
  ["ios_premium", 763, 441, 0.5778],
  ["returning_high_value", 769, 448, 0.5826],
] as [string, number, number, number][]).map(
  ([segment, attempts, captured, rate]) => ({
    segment,
    attempts,
    captured,
    failed: Math.round(attempts * 0.2),
    abandoned: attempts - captured - Math.round(attempts * 0.2),
    conversion_rate: rate,
    gmv_paise: captured * 250000,
    captured_gmv_paise: captured * 250000,
    average_captured_order_value_paise: 250000,
  }),
);

export const paymentMethodMetrics: PaymentMethodMetrics[] = [
  {
    payment_method: "upi",
    attempts: 2630,
    captured: 1105,
    failed: 940,
    abandoned: 585,
    success_rate: 0.42,
  },
  {
    payment_method: "card",
    attempts: 1530,
    captured: 800,
    failed: 430,
    abandoned: 300,
    success_rate: 0.5229,
  },
  {
    payment_method: "netbanking",
    attempts: 1000,
    captured: 520,
    failed: 280,
    abandoned: 200,
    success_rate: 0.52,
  },
  {
    payment_method: "wallet",
    attempts: 952,
    captured: 470,
    failed: 300,
    abandoned: 182,
    success_rate: 0.4937,
  },
];

export function makeAutopilotStatus(
  overrides: Partial<AutopilotStatus> = {},
): AutopilotStatus {
  return {
    merchant_id: "merchant_techbazaar",
    opportunity_count: 1,
    experiment_count: 1,
    active_opportunity_count: 1,
    active_experiment_count: 1,
    latest_opportunity_id: "6f1cad54-0000-4000-8000-00000000e301",
    latest_experiment_id: "exp-1",
    latest_experiment_status: "running",
    latest_decision: "APPROVE",
    latest_statistical_decision: null,
    latest_resource_status: "none",
    state: "RUNNING",
    next_action: "RUN_EXPERIMENT_BATCH",
    audit_chain_valid: true,
    progress: null,
    ...overrides,
  };
}

export function makeOverview(
  overrides: Partial<MerchantOverview> = {},
): MerchantOverview {
  return {
    merchant: {
      merchant_id: "merchant_techbazaar",
      name: "TechBazaar Electronics",
      category: "consumer_electronics",
      monthly_gmv_paise: 500000000,
      created_at: "2026-07-27T00:00:00+00:00",
    },
    metrics: {
      attempts: 6112,
      captured: 2895,
      failed: 1950,
      abandoned: 1267,
      conversion_rate: 0.4737,
    },
    segment_metrics: segmentMetrics,
    payment_method_metrics: paymentMethodMetrics,
    attempted_gmv_paise: 1528000000,
    captured_gmv_paise: 723750000,
    active_opportunity_count: 1,
    active_experiment_count: 1,
    latest_experiment: null,
    latest_result: null,
    audit_chain_valid: true,
    autopilot_status: makeAutopilotStatus(),
    ...overrides,
  };
}

export function makeOpportunity(
  overrides: Partial<Opportunity> = {},
): Opportunity {
  return {
    id: "6f1cad54-0000-4000-8000-00000000e301",
    merchant_id: "merchant_techbazaar",
    type: "segment_underperformance",
    segment: "android_budget",
    severity: 0.281,
    detected_metric: "conversion_rate",
    detected_value: 0.4778,
    baseline_value: 0.5861,
    status: "detected",
    created_at: "2026-08-27T10:00:00+00:00",
    evidence: {
      segment: "android_budget",
      segment_attempts: 2141,
      segment_captured: 1023,
      segment_conversion_rate: 0.4778,
      comparison_attempts: 3971,
      comparison_captured: 2327,
      comparison_conversion_rate: 0.5861,
      absolute_gap: 0.1083,
      payment_method_metrics: {
        upi: {
          attempts: 1180,
          captured: 480,
          failed: 420,
          abandoned: 280,
          success_rate: 0.4068,
        },
        card: {
          attempts: 320,
          captured: 175,
          failed: 90,
          abandoned: 55,
          success_rate: 0.5469,
        },
      },
      failure_reasons: {
        insufficient_funds: 210,
        bank_declined: 160,
        network_error: 90,
      },
    },
    ...overrides,
  };
}

export function makeHypothesis(
  overrides: Partial<Hypothesis> = {},
): Hypothesis {
  return {
    id: "hyp-1",
    opportunity_id: "6f1cad54-0000-4000-8000-00000000e301",
    merchant_id: "merchant_techbazaar",
    ai_model: "gpt-4.1-mini",
    hypothesis_text:
      "The android_budget segment converts materially below the rest of the merchant's traffic, driven by UPI failures and insufficient-funds declines. Enabling UI-first payment methods with partial payment support should reduce drop-off at checkout.",
    intervention_type: "payment_method_config",
    intervention_params: { upi: true, card: false },
    confidence: "medium",
    reasoning_summary:
      "UPI is the dominant method in this segment but converts worst; a UPI-first configuration is the least invasive change with the widest reach.",
    evidence_refs: [
      "payment_method.upi.success_rate",
      "failure_reason.insufficient_funds",
      "segment_conversion_rate",
    ],
    status: "proposed",
    created_at: "2026-08-27T10:05:00+00:00",
    ...overrides,
  };
}

export function makeExperiment(
  overrides: Partial<Experiment> = {},
): Experiment {
  return {
    id: "exp-1",
    merchant_id: "merchant_techbazaar",
    hypothesis_id: "hyp-1",
    opportunity_id: "6f1cad54-0000-4000-8000-00000000e301",
    name: "android_budget-payment_method_config",
    segment: "android_budget",
    intervention_type: "payment_method_config",
    control_config: { payment_methods: "merchant_default" },
    treatment_config: { payment_methods: { upi: true, card: false } },
    traffic_split_treatment_pct: 0.1,
    primary_metric: "conversion_rate",
    guardrail_metrics: ["failure_rate"],
    min_sample_per_variant: 200,
    max_duration_hours: 72,
    status: "running",
    started_at: "2026-08-27T11:00:00+00:00",
    ended_at: null,
    created_at: "2026-08-27T10:10:00+00:00",
    ...overrides,
  };
}

export function makePolicy(
  overrides: Partial<MerchantPolicyPublic> = {},
): MerchantPolicyPublic {
  return {
    merchant_id: "merchant_techbazaar",
    max_experiment_exposure_pct: 0.1,
    max_discount_pct: 0.15,
    min_margin_pct: 0.05,
    max_concurrent_experiments: 3,
    max_experiment_duration_hours: 168,
    min_sample_size: 30,
    max_financial_exposure: 50000,
    allowed_interventions: [
      "payment_method_config",
      "offer_discount",
      "partial_payment",
      "expiry_config",
    ],
    ...overrides,
  };
}

export function makePolicyDecision(
  overrides: Partial<PolicyDecision> = {},
): PolicyDecision {
  return {
    id: "pol-1",
    experiment_id: "exp-1",
    merchant_id: "merchant_techbazaar",
    decision: "APPROVE",
    violations: [],
    original_params: {},
    final_params: null,
    evaluated_at: "2026-08-27T10:12:00+00:00",
    ...overrides,
  };
}

export function makeProgress(
  overrides: Partial<ExperimentProgress> = {},
): ExperimentProgress {
  return {
    experiment_id: "exp-1",
    control_attempts: 120,
    treatment_attempts: 104,
    sample_target_per_variant: 200,
    control_remaining: 80,
    treatment_remaining: 96,
    sample_target_reached: false,
    ...overrides,
  };
}

export function makeResult(
  overrides: Partial<ExperimentResult> = {},
): ExperimentResult {
  return {
    experiment_id: "exp-1",
    control_count: 200,
    treatment_count: 200,
    control_conversions: 92,
    treatment_conversions: 121,
    control_rate: 0.46,
    treatment_rate: 0.605,
    absolute_lift: 0.145,
    relative_lift: 0.3152,
    p_value: 0.0012,
    confidence_interval_lower: 0.058,
    confidence_interval_upper: 0.232,
    is_significant: true,
    decision: "KEEP",
    decided_at: "2026-08-27T12:00:00+00:00",
    ...overrides,
  };
}

export function makeResource(
  overrides: Partial<RazorpayResource> = {},
): RazorpayResource {
  return {
    id: "res-1",
    experiment_id: "exp-1",
    variant: "treatment",
    resource_type: "payment_link",
    razorpay_id: "plink_Q8xKmPq2vWxYz1234",
    status: "active",
    created_at: "2026-08-27T10:20:00+00:00",
    ...overrides,
  };
}

let auditSeq = 0;
export function makeAuditEvent(
  overrides: Partial<AuditEvent> = {},
): AuditEvent {
  auditSeq += 1;
  return {
    id: `evt-${auditSeq}`,
    event_type: "OPPORTUNITY_DETECTED",
    actor: "detector",
    entity_type: "opportunity",
    entity_id: "6f1cad54-0000-4000-8000-00000000e301",
    data: { segment: "android_budget", severity: 0.281 },
    created_at: "2026-08-27T10:00:00+00:00",
    prev_hash: null,
    event_hash: "a".repeat(64),
    ...overrides,
  };
}

export function makeCycle(
  overrides: Partial<AutopilotCycle> = {},
): AutopilotCycle {
  return {
    opportunity: makeOpportunity(),
    hypothesis: null,
    experiment: null,
    policy_decision: null,
    merchant_policy: makePolicy(),
    razorpay_resource: null,
    progress: null,
    result: null,
    audit_events: [makeAuditEvent()],
    audit_chain_valid: true,
    ...overrides,
  };
}
