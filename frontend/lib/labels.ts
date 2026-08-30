import type {
  ActorId,
  AutopilotNextAction,
  AutopilotState,
  ExperimentStatus,
  StatisticalDecision,
} from "./types";

/**
 * The stable Autopilot vocabulary rendered as merchant-facing text.
 * Backend state/action values are never string-matched ad hoc in components -
 * every mapping lives here.
 */

// ---------------------------------------------------------------------------
// Primary action labels (context-aware)
// ---------------------------------------------------------------------------

export const ACTION_LABELS: Record<AutopilotNextAction, string> = {
  DETECT_OPPORTUNITIES: "Scan for Opportunities",
  DIAGNOSE_OPPORTUNITY: "Generate Diagnosis",
  PLAN_EXPERIMENT: "Plan Experiment",
  EVALUATE_POLICY: "Run Policy Check",
  DEPLOY_TREATMENT: "Deploy Treatment",
  CONFIGURE_OFFER_MAPPING: "Deployment Blocked",
  RUN_EXPERIMENT_BATCH: "Run Next Batch",
  EVALUATE_EXPERIMENT: "Evaluate Results",
  ROLLBACK_TREATMENT: "Roll Back Treatment",
  STOP: "",
  DONE: "Cycle Complete",
};

export const ACTION_LOADING_LABELS: Partial<
  Record<AutopilotNextAction, string>
> = {
  DETECT_OPPORTUNITIES: "Scanning…",
  DIAGNOSE_OPPORTUNITY: "Generating diagnosis…",
  PLAN_EXPERIMENT: "Planning…",
  EVALUATE_POLICY: "Checking policy…",
  DEPLOY_TREATMENT: "Deploying…",
  CONFIGURE_OFFER_MAPPING: "Checking deployment…",
  RUN_EXPERIMENT_BATCH: "Running batch…",
  EVALUATE_EXPERIMENT: "Evaluating…",
  ROLLBACK_TREATMENT: "Rolling back…",
  DONE: "Completing…",
};

/** Which system actor performs the next action (Overview "Next step" line). */
export const ACTION_ACTORS: Record<AutopilotNextAction, string | null> = {
  DETECT_OPPORTUNITIES: "Detector",
  DIAGNOSE_OPPORTUNITY: "AI Diagnosis",
  PLAN_EXPERIMENT: "Planner",
  EVALUATE_POLICY: "Policy Engine",
  DEPLOY_TREATMENT: "Razorpay Executor",
  CONFIGURE_OFFER_MAPPING: "Razorpay Executor",
  RUN_EXPERIMENT_BATCH: "Experiment Runtime",
  EVALUATE_EXPERIMENT: "Statistical Engine",
  ROLLBACK_TREATMENT: "Razorpay Executor",
  STOP: null,
  DONE: null,
};

/**
 * Actions the merchant can actually trigger. Blocked and terminal actions
 * render as disabled states instead of mutation buttons.
 */
export const ACTION_DISABLED: Partial<Record<AutopilotNextAction, boolean>> = {
  CONFIGURE_OFFER_MAPPING: true,
  DONE: true,
  STOP: true,
};

export function actionLabel(action: AutopilotNextAction | null): string | null {
  if (!action || action === "STOP") return null;
  return ACTION_LABELS[action];
}

export function actionLoadingLabel(
  action: AutopilotNextAction | null,
): string | null {
  if (!action) return null;
  return ACTION_LOADING_LABELS[action] ?? `${ACTION_LABELS[action]}…`;
}

// ---------------------------------------------------------------------------
// Autopilot status sentences (Overview hero)
// ---------------------------------------------------------------------------

export function autopilotStatusSentence(
  state: AutopilotState,
  decision: StatisticalDecision | null,
): string {
  switch (state) {
    case "IDLE":
      return "Ready to analyze payment performance for optimization opportunities.";
    case "HYPOTHESIS_PENDING":
      return "An actionable conversion opportunity is ready for AI diagnosis.";
    case "EXPERIMENT_PENDING":
      return "A structured hypothesis is ready for deterministic experiment planning.";
    case "POLICY_REVIEW_PENDING":
      return "The proposed experiment is awaiting merchant policy authorization.";
    case "DEPLOYMENT_PENDING":
      return "Policy approved. The treatment is ready for the configured execution boundary.";
    case "DEPLOYMENT_BLOCKED":
      return "Deployment is blocked because this intervention cannot yet be safely mapped to a verified Razorpay resource.";
    case "POLICY_REJECTED":
      return "Merchant policy rejected the proposed experiment.";
    case "RUNNING":
      return "The experiment is running toward its fixed sample horizon.";
    case "EVALUATION_PENDING":
      return "Both cohorts reached the sample target. Statistical evaluation is ready.";
    case "COMPLETED":
      switch (decision) {
        case "KEEP":
          return "Cycle complete. The statistical decision kept the treatment.";
        case "ROLLBACK":
          return "Cycle complete. The statistical decision rolled back the treatment.";
        case "INCONCLUSIVE":
          return "Cycle complete. The result was inconclusive - insufficient evidence to keep or roll back the treatment.";
        default:
          return "Cycle complete.";
      }
    default:
      return "Autopilot status unavailable.";
  }
}

// ---------------------------------------------------------------------------
// Actor strip (Detector → AI → Planner → Policy → Razorpay → Statistics)
// ---------------------------------------------------------------------------

export type StripActorId =
  | "detector"
  | "ai"
  | "planner"
  | "policy"
  | "razorpay"
  | "statistics";

export const STRIP_ACTORS: { id: StripActorId; label: string }[] = [
  { id: "detector", label: "Detector" },
  { id: "ai", label: "AI" },
  { id: "planner", label: "Planner" },
  { id: "policy", label: "Policy" },
  { id: "razorpay", label: "Razorpay" },
  { id: "statistics", label: "Statistics" },
];

export interface StripStage {
  id: StripActorId;
  label: string;
  status: "completed" | "current" | "pending";
  tone?: "active" | "blocked" | "rejected";
}

/**
 * Compact progress over the six system actors, derived from persisted
 * Autopilot state only - never guessed from labels or timestamps.
 */
export function actorStripStages(
  state: AutopilotState,
  nextAction: AutopilotNextAction | null,
): StripStage[] {
  const done: StripActorId[] = [];
  switch (state) {
    case "IDLE":
      break;
    case "HYPOTHESIS_PENDING":
      done.push("detector");
      break;
    case "EXPERIMENT_PENDING":
      done.push("detector", "ai");
      break;
    case "POLICY_REVIEW_PENDING":
      done.push("detector", "ai", "planner");
      break;
    case "DEPLOYMENT_PENDING":
    case "DEPLOYMENT_BLOCKED":
      done.push("detector", "ai", "planner", "policy");
      break;
    case "POLICY_REJECTED":
      done.push("detector", "ai", "planner");
      break;
    case "RUNNING":
      done.push("detector", "ai", "planner", "policy");
      break;
    case "EVALUATION_PENDING":
      done.push("detector", "ai", "planner", "policy", "razorpay");
      break;
    case "COMPLETED":
      done.push("detector", "ai", "planner", "policy", "razorpay", "statistics");
      break;
  }

  const doneSet = new Set<StripActorId>(done);

  let current: StripActorId | null = null;
  let tone: StripStage["tone"] = "active";
  switch (state) {
    case "IDLE":
      current = "detector";
      break;
    case "HYPOTHESIS_PENDING":
      current = "ai";
      break;
    case "EXPERIMENT_PENDING":
      current = "planner";
      break;
    case "POLICY_REVIEW_PENDING":
      current = "policy";
      break;
    case "DEPLOYMENT_PENDING":
      current = "razorpay";
      break;
    case "DEPLOYMENT_BLOCKED":
      current = "razorpay";
      tone = "blocked";
      break;
    case "POLICY_REJECTED":
      current = "policy";
      tone = "rejected";
      break;
    case "RUNNING":
      current = nextAction === "EVALUATE_EXPERIMENT" ? "statistics" : "razorpay";
      break;
    case "EVALUATION_PENDING":
      current = "statistics";
      break;
    case "COMPLETED":
      current = null;
      break;
  }

  return STRIP_ACTORS.map(({ id, label }) => ({
    id,
    label,
    status: doneSet.has(id)
      ? ("completed" as const)
      : current === id
        ? ("current" as const)
        : ("pending" as const),
    tone: current === id ? tone : undefined,
  }));
}

// ---------------------------------------------------------------------------
// Audit actors
// ---------------------------------------------------------------------------

export const ACTOR_LABELS: Record<string, string> = {
  detector: "Detector",
  ai: "AI Diagnosis",
  planner: "Planner",
  policy: "Policy Engine",
  runtime: "Experiment Runtime",
  statistics: "Statistical Engine",
  razorpay_executor: "Razorpay Executor",
  system: "System",
};

export function eventLabel(eventType: string): string {
  const words = eventType.toLowerCase().split("_");
  return words
    .map((word, index) => (index === 0 ? word[0]?.toUpperCase() + word.slice(1) : word))
    .join(" ");
}

const VIOLATION_LABELS: Record<string, string> = {
  INTERVENTION_NOT_ALLOWED: "Intervention is not allowed for this merchant",
  TREATMENT_EXPOSURE_EXCEEDED: "Treatment exposure exceeds merchant limit",
  DISCOUNT_LIMIT_EXCEEDED: "Discount exceeds merchant limit",
  MIN_MARGIN_VIOLATED: "Minimum margin requirement violated",
  FINANCIAL_EXPOSURE_EXCEEDED: "Financial exposure exceeds merchant limit",
  SAMPLE_SIZE_TOO_SMALL: "Sample size is below merchant minimum",
  DURATION_LIMIT_EXCEEDED: "Experiment duration exceeds merchant limit",
  CONCURRENT_EXPERIMENT_LIMIT_REACHED: "Concurrent experiment limit reached",
  SEGMENT_CONFLICT: "Another active experiment already targets this segment",
  INVALID_INTERVENTION_CONFIG: "Intervention configuration is invalid",
  INVALID_CONTROL_CONFIG: "Control configuration is invalid",
};

export function violationLabel(code: string): string {
  return VIOLATION_LABELS[code] ?? eventLabel(code);
}

export function auditEventSummary(
  eventType: string,
  data: Record<string, unknown>,
): string {
  switch (eventType) {
    case "OPPORTUNITY_DETECTED": {
      const segment = typeof data.segment === "string" ? data.segment : "segment";
      const severity = typeof data.severity === "number" ? data.severity.toFixed(2) : "n/a";
      return `Segment ${segment} flagged for segment_conversion_divergence · severity ${severity}`;
    }
    case "AI_DIAGNOSIS_CREATED": {
      const model = typeof data.ai_model === "string" ? data.ai_model : "configured model";
      return `Validated LLM proposal via ${model}`;
    }
    case "HYPOTHESIS_PROPOSED": {
      const intervention =
        typeof data.intervention_type === "string" ? data.intervention_type : "intervention";
      const confidence = typeof data.confidence === "string" ? data.confidence : "unknown";
      return `Proposed ${intervention} intervention · confidence ${confidence}`;
    }
    case "EXPERIMENT_PLANNED": {
      const segment = typeof data.segment === "string" ? data.segment : "segment";
      const exposure =
        typeof data.traffic_split_treatment_pct === "number"
          ? `${(data.traffic_split_treatment_pct * 100).toFixed(0)}%`
          : "n/a";
      return `Experiment plan for ${segment} · ${exposure} treatment exposure`;
    }
    case "POLICY_APPROVED":
      return "Experiment authorized within merchant policy limits";
    case "POLICY_REJECTED": {
      const violations = Array.isArray(data.violations) ? data.violations.join(", ") : "policy violations";
      return `Experiment rejected · ${violations}`;
    }
    case "RAZORPAY_RESOURCE_CREATED": {
      const resource = typeof data.resource_type === "string" ? data.resource_type : "resource";
      const id = typeof data.razorpay_id === "string" ? data.razorpay_id : "unknown";
      const hostedDemo = id.startsWith("demo_");
      return hostedDemo
        ? `${resource} ${id} created in hosted demo mode (simulated)`
        : `${resource} ${id} created in Razorpay Test Mode`;
    }
    case "EXPERIMENT_STARTED": {
      const control = typeof data.control_target === "number" ? data.control_target : "n/a";
      const treatment = typeof data.treatment_target === "number" ? data.treatment_target : "n/a";
      return `Runtime started · targets ${control} control / ${treatment} treatment`;
    }
    case "EXPERIMENT_COMPLETED": {
      const decision = typeof data.decision === "string" ? data.decision : "unknown";
      const pValue = typeof data.p_value === "number" ? data.p_value.toFixed(4) : "n/a";
      return `Fixed-horizon decision ${decision} · p ${pValue}`;
    }
    case "EXPERIMENT_ROLLED_BACK":
      return "Experiment treatment rolled back after statistical decision";
    case "RAZORPAY_RESOURCE_CANCELLED": {
      const id = typeof data.razorpay_id === "string" ? data.razorpay_id : "resource";
      return `${id} cancelled`;
    }
    default:
      return eventLabel(eventType);
  }
}
