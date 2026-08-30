/**
 * Typed mirrors of the backend API response models (backend/app/api/schemas.py).
 *
 * These are the only shapes the dashboard may ever see. Datetimes arrive as
 * ISO-8601 strings over JSON, so every timestamp is typed `string` - no
 * client-side Date objects are constructed for API data except at the
 * formatting boundary in lib/format.ts.
 */

export type AutopilotState =
  | "IDLE"
  | "HYPOTHESIS_PENDING"
  | "EXPERIMENT_PENDING"
  | "POLICY_REVIEW_PENDING"
  | "DEPLOYMENT_PENDING"
  | "DEPLOYMENT_BLOCKED"
  | "POLICY_REJECTED"
  | "RUNNING"
  | "EVALUATION_PENDING"
  | "COMPLETED";

export type AutopilotNextAction =
  | "DETECT_OPPORTUNITIES"
  | "DIAGNOSE_OPPORTUNITY"
  | "PLAN_EXPERIMENT"
  | "EVALUATE_POLICY"
  | "DEPLOY_TREATMENT"
  | "CONFIGURE_OFFER_MAPPING"
  | "RUN_EXPERIMENT_BATCH"
  | "EVALUATE_EXPERIMENT"
  | "ROLLBACK_TREATMENT"
  | "STOP"
  | "DONE";

export type AutopilotStepName =
  | "OPPORTUNITY_DETECTED"
  | "HYPOTHESIS_PROPOSED"
  | "EXPERIMENT_PLANNED"
  | "POLICY_APPROVED"
  | "POLICY_REJECTED"
  | "RESOURCE_DEPLOYED"
  | "DEPLOYMENT_BLOCKED"
  | "EXPERIMENT_BATCH_RUN"
  | "EXPERIMENT_EVALUATED"
  | "RESOURCE_ROLLED_BACK"
  | "COMPLETED"
  | "NO_ACTION";

export type AutopilotEntityType =
  | "merchant"
  | "opportunity"
  | "hypothesis"
  | "experiment";

export type ExperimentStatus =
  | "proposed"
  | "approved"
  | "running"
  | "rejected"
  | "completed"
  | "rolled_back"
  | "cancelled";

export type PolicyVerdict = "APPROVE" | "REJECT";
export type StatisticalDecision = "KEEP" | "ROLLBACK" | "INCONCLUSIVE";
export type ActorId =
  | "detector"
  | "ai"
  | "planner"
  | "policy"
  | "runtime"
  | "statistics"
  | "razorpay_executor"
  | "system";

// ---------------------------------------------------------------------------
// Merchant / metrics
// ---------------------------------------------------------------------------

export interface MerchantSummary {
  merchant_id: string;
  name: string;
  category: string | null;
  monthly_gmv_paise: number | null;
  created_at: string | null;
}

export interface ConversionMetrics {
  attempts: number;
  captured: number;
  failed: number;
  abandoned: number;
  conversion_rate: number | null;
}

export interface SegmentMetrics {
  segment: string;
  attempts: number;
  captured: number;
  failed: number;
  abandoned: number;
  conversion_rate: number | null;
  gmv_paise: number;
  captured_gmv_paise: number;
  average_captured_order_value_paise: number | null;
}

export interface PaymentMethodMetrics {
  payment_method: string;
  attempts: number;
  captured: number;
  failed: number;
  abandoned: number;
  success_rate: number | null;
}

// ---------------------------------------------------------------------------
// Lifecycle entities
// ---------------------------------------------------------------------------

export interface Opportunity {
  id: string;
  merchant_id: string;
  type: string;
  segment: string | null;
  severity: number;
  detected_metric: string;
  detected_value: number | null;
  baseline_value: number | null;
  status: string;
  created_at: string;
  evidence: Record<string, unknown>;
}

export interface Hypothesis {
  id: string;
  opportunity_id: string;
  merchant_id: string;
  ai_model: string | null;
  hypothesis_text: string;
  intervention_type: string;
  intervention_params: Record<string, unknown>;
  confidence: string | null;
  reasoning_summary: string | null;
  evidence_refs: string[];
  status: string;
  created_at: string;
}

export interface Experiment {
  id: string;
  merchant_id: string;
  hypothesis_id: string;
  opportunity_id: string;
  name: string;
  segment: string;
  intervention_type: string;
  control_config: Record<string, unknown>;
  treatment_config: Record<string, unknown>;
  traffic_split_treatment_pct: number;
  primary_metric: string;
  guardrail_metrics: string[];
  min_sample_per_variant: number;
  max_duration_hours: number;
  status: ExperimentStatus;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
}

export interface ExperimentProgress {
  experiment_id: string;
  control_attempts: number;
  treatment_attempts: number;
  sample_target_per_variant: number;
  control_remaining: number;
  treatment_remaining: number;
  sample_target_reached: boolean;
}

export interface PolicyDecision {
  id: string;
  experiment_id: string;
  merchant_id: string;
  decision: PolicyVerdict;
  violations: string[];
  original_params: Record<string, unknown>;
  final_params: Record<string, unknown> | null;
  evaluated_at: string;
}

export interface ExperimentResult {
  experiment_id: string;
  control_count: number;
  treatment_count: number;
  control_conversions: number;
  treatment_conversions: number;
  control_rate: number | null;
  treatment_rate: number | null;
  absolute_lift: number | null;
  relative_lift: number | null;
  p_value: number | null;
  confidence_interval_lower: number | null;
  confidence_interval_upper: number | null;
  is_significant: boolean | null;
  decision: StatisticalDecision | null;
  decided_at: string | null;
}

export interface RazorpayResource {
  id: string;
  experiment_id: string | null;
  variant: string | null;
  resource_type: string;
  razorpay_id: string;
  status: "active" | "cancelled";
  created_at: string;
}

export interface AuditEvent {
  id: string;
  event_type: string;
  actor: string;
  entity_type: string | null;
  entity_id: string | null;
  data: Record<string, unknown>;
  created_at: string;
  prev_hash: string | null;
  event_hash: string | null;
}

export interface MerchantPolicyPublic {
  merchant_id: string;
  max_experiment_exposure_pct: number;
  max_discount_pct: number;
  min_margin_pct: number;
  max_concurrent_experiments: number;
  max_experiment_duration_hours: number;
  min_sample_size: number;
  max_financial_exposure: number;
  allowed_interventions: string[];
}

// ---------------------------------------------------------------------------
// Aggregates
// ---------------------------------------------------------------------------

export interface AutopilotStatus {
  merchant_id: string;
  opportunity_count: number;
  experiment_count: number;
  active_opportunity_count: number;
  active_experiment_count: number;
  latest_opportunity_id: string | null;
  latest_experiment_id: string | null;
  latest_experiment_status: ExperimentStatus | null;
  latest_decision: PolicyVerdict | null;
  latest_statistical_decision: StatisticalDecision | null;
  latest_resource_status: "active" | "cancelled" | "none";
  state: AutopilotState;
  next_action: AutopilotNextAction | null;
  audit_chain_valid: boolean;
  progress: ExperimentProgress | null;
}

export interface MerchantOverview {
  merchant: MerchantSummary;
  metrics: ConversionMetrics;
  segment_metrics: SegmentMetrics[];
  payment_method_metrics: PaymentMethodMetrics[];
  attempted_gmv_paise: number;
  captured_gmv_paise: number;
  active_opportunity_count: number;
  active_experiment_count: number;
  latest_experiment: Experiment | null;
  latest_result: ExperimentResult | null;
  audit_chain_valid: boolean;
  autopilot_status: AutopilotStatus;
}

export interface AutopilotCycle {
  opportunity: Opportunity;
  hypothesis: Hypothesis | null;
  experiment: Experiment | null;
  policy_decision: PolicyDecision | null;
  merchant_policy: MerchantPolicyPublic | null;
  razorpay_resource: RazorpayResource | null;
  progress: ExperimentProgress | null;
  result: ExperimentResult | null;
  audit_events: AuditEvent[];
  audit_chain_valid: boolean;
}

export interface RankedOpportunityIntelligence {
  rank: number;
  opportunity_id: string;
  segment: string;
  status: string;
  detector_severity: number;
  detected_conversion_rate: number | null;
  comparison_conversion_rate: number | null;
  conversion_gap: number;
  segment_attempts: number;
  average_captured_order_value_paise: number | null;
  estimated_incremental_captures: number;
  estimated_recoverable_gmv_paise: number | null;
  prior_terminal_trials: number;
  history_factor: number;
  allowed_intervention_count: number;
  previously_tried_interventions: string[];
  untried_allowed_interventions: string[];
  policy_feasible: boolean;
  history_adjusted_gmv_proxy_paise: number | null;
  priority_index: number;
}

export interface OpportunityPortfolioIntelligence {
  merchant_id: string;
  next_best_opportunity_id: string | null;
  opportunities: RankedOpportunityIntelligence[];
}

export interface ChampionConfigIntelligence {
  intervention_type: string;
  config: Record<string, unknown>;
  source_experiment_id: string;
  promoted_at: string;
  absolute_lift: number;
  p_value: number;
}

export interface MerchantChampionIntelligence {
  merchant_id: string;
  version: number;
  promotion_count: number;
  configs: ChampionConfigIntelligence[];
  latest_promotion_experiment_id: string | null;
}

export interface InterventionKnowledge {
  segment: string;
  intervention_type: string;
  trial_count: number;
  approved_count: number;
  rejected_count: number;
  completed_result_count: number;
  keep_count: number;
  rollback_count: number;
  inconclusive_count: number;
  latest_outcome: string;
  latest_experiment_id: string;
  latest_treatment_config: Record<string, unknown>;
  latest_treatment_config_fingerprint: string;
  latest_absolute_lift: number | null;
  latest_p_value: number | null;
}

export interface ExperimentMemoryRecord {
  experiment_id: string;
  opportunity_id: string;
  segment: string;
  intervention_type: string;
  treatment_config: Record<string, unknown>;
  treatment_config_fingerprint: string;
  experiment_status: string;
  policy_decision: PolicyVerdict | null;
  policy_violations: string[];
  statistical_decision: StatisticalDecision | null;
  control_rate: number | null;
  treatment_rate: number | null;
  absolute_lift: number | null;
  relative_lift: number | null;
  p_value: number | null;
  confidence_interval_lower: number | null;
  confidence_interval_upper: number | null;
  is_significant: boolean | null;
  treatment_resource_status: string | null;
  terminal_reason: string;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
}

export interface MerchantExperimentMemory {
  merchant_id: string;
  trial_count: number;
  completed_result_count: number;
  policy_rejection_count: number;
  keep_count: number;
  rollback_count: number;
  inconclusive_count: number;
  knowledge: InterventionKnowledge[];
  records: ExperimentMemoryRecord[];
}

export interface MerchantIntelligence {
  merchant: MerchantSummary;
  portfolio: OpportunityPortfolioIntelligence;
  champion: MerchantChampionIntelligence;
  memory: MerchantExperimentMemory;
}

export interface AutopilotStep {
  merchant_id: string;
  step: AutopilotStepName;
  entity_type: AutopilotEntityType | null;
  entity_id: string | null;
  message: string;
  status: AutopilotState;
  next_action: AutopilotNextAction | null;
}

export interface ExperimentRollback {
  experiment_id: string;
  status: "rolled_back" | "no_active_resource";
  resource: RazorpayResource | null;
}
