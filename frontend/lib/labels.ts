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
  RUN_EXPERIMENT_BATCH: "Run Experiment",
  EVALUATE_EXPERIMENT: "Run Experiment",
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
  RUN_EXPERIMENT_BATCH: "Running to fixed horizon…",
  EVALUATE_EXPERIMENT: "Evaluating fixed horizon…",
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
  RUN_EXPERIMENT_BATCH: "Runtime + Statistics",
  EVALUATE_EXPERIMENT: "Runtime + Statistics",
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
      return "Policy approved. The treatment is ready for Razorpay Test Mode deployment.";
    case "DEPLOYMENT_BLOCKED":
      return "Deployment is blocked because this intervention cannot yet be safely mapped to a verified Razorpay resource.";
    case "POLICY_REJECTED":
      return "Merchant policy rejected the proposed experiment.";
    case "RUNNING":
      return "The approved experiment is ready to run to its fixed sample horizon and evaluate in one action.";
    case "EVALUATION_PENDING":
      return "Both cohorts reached the fixed sample horizon. Run Experiment will record the statistical decision without adding more traffic.";
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
      done.push("detector", "ai", "planner", "policy", "razorpay");
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

export function actorLabel(actor: string): string {
  return ACTOR_LABELS[actor] ?? actor;
}

/** Actor groups behind the Audit Log client-side filters. */
export const AUDIT_FILTERS: { id: string; label: string; actors: string[] }[] = [
  { id: "all", label: "All", actors: [] },
  { id: "detector", label: "Detector", actors: ["detector"] },
  { id: "ai", label: "AI", actors: ["ai"] },
  { id: "policy", label: "Policy", actors: ["policy"] },
  { id: "razorpay", label: "Razorpay", actors: ["razorpay_executor"] },
  { id: "statistics", label: "Statistics", actors: ["statistics"] },
];

// ---------------------------------------------------------------------------
// Audit event names + one-line summaries
// ---------------------------------------------------------------------------

export const EVENT_LABELS: Record<string, string> = {
  OPPORTUNITY_DETECTED: "Opportunity detected",
  AI_DIAGNOSIS_CREATED: "AI diagnosis created",
  HYPOTHESIS_PROPOSED: "Hypothesis proposed",
  EXPERIMENT_PLANNED: "Experiment planned",
  POLICY_APPROVED: "Policy approved",
  POLICY_REJECTED: "Policy rejected",
  RAZORPAY_RESOURCE_CREATED: "Razorpay resource created",
  EXPERIMENT_STARTED: "Experiment started",
  EXPERIMENT_COMPLETED: "Experiment completed",
  TREATMENT_PROMOTED: "Treatment promoted",
  EXPERIMENT_ROLLED_BACK: "Experiment rolled back",
  RAZORPAY_RESOURCE_CANCELLED: "Razorpay resource cancelled",
};

export function eventLabel(eventType: string): string {
  return EVENT_LABELS[eventType] ?? humanizeUpper(eventType);
}

function humanizeUpper(value: string): string {
  return value
    .toLowerCase()
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function asText(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return "";
}

/**
 * One concise, factual summary line per audit event, built only from the
 * sanitized payload the API already exposes.
 */
export function auditEventSummary(
  eventType: string,
  data: Record<string, unknown>,
): string {
  switch (eventType) {
    case "OPPORTUNITY_DETECTED": {
      const segment = asText(data.segment);
      const severity = typeof data.severity === "number" ? data.severity.toFixed(2) : null;
      const type = asText(data.type) || "conversion opportunity";
      return [
        segment ? `Segment ${segment}` : "Segment unavailable",
        `flagged for ${type}`,
        severity ? `· severity ${severity}` : "",
      ]
        .filter(Boolean)
        .join(" ");
    }
    case "AI_DIAGNOSIS_CREATED": {
      const model = asText(data.ai_model);
      return model ? `Validated LLM proposal via ${model}` : "Validated LLM proposal recorded";
    }
    case "HYPOTHESIS_PROPOSED": {
      const intervention = asText(data.intervention_type);
      const confidence = asText(data.confidence);
      return [
        intervention ? `Proposed ${intervention} intervention` : "Intervention proposed",
        confidence ? `· confidence ${confidence}` : "",
      ]
        .filter(Boolean)
        .join(" ");
    }
    case "EXPERIMENT_PLANNED": {
      const segment = asText(data.segment);
      const split =
        typeof data.traffic_split_treatment_pct === "number"
          ? `${(data.traffic_split_treatment_pct * 100).toFixed(0)}% treatment exposure`
          : "";
      return [segment ? `Experiment plan for ${segment}` : "Experiment plan created", split]
        .filter(Boolean)
        .join(" · ");
    }
    case "POLICY_APPROVED":
      return "Experiment authorized within merchant policy limits";
    case "POLICY_REJECTED": {
      const violations = Array.isArray(data.violations) ? data.violations : [];
      const codes = violations.filter((v): v is string => typeof v === "string");
      return codes.length
        ? `Rejected: ${codes.join(", ")}`
        : "Rejected by deterministic merchant policy";
    }
    case "RAZORPAY_RESOURCE_CREATED": {
      const id = asText(data.razorpay_id);
      const type = asText(data.resource_type);
      return [type, id, "created in Razorpay Test Mode"].filter(Boolean).join(" ");
    }
    case "EXPERIMENT_STARTED": {
      const control = asText(data.control_target);
      const treatment = asText(data.treatment_target);
      return control && treatment
        ? `Runtime started · targets ${control} control / ${treatment} treatment`
        : "Experiment runtime started";
    }
    case "EXPERIMENT_COMPLETED": {
      const decision = asText(data.decision);
      const p =
        typeof data.p_value === "number" && data.p_value < 0.001
          ? "< 0.001"
          : typeof data.p_value === "number"
            ? data.p_value.toFixed(4)
            : "";
      return [decision ? `Fixed-horizon decision ${decision}` : "Fixed-horizon evaluation recorded", p ? `· p ${p}` : ""]
        .filter(Boolean)
        .join(" ");
    }
    case "EXPERIMENT_ROLLED_BACK":
      return "Treatment cancelled after ROLLBACK decision";
    case "RAZORPAY_RESOURCE_CANCELLED": {
      const id = asText(data.razorpay_id);
      return id ? `Razorpay resource ${id} cancelled` : "Razorpay resource cancelled";
    }
    case "TREATMENT_PROMOTED":
      return "Treatment retained after KEEP decision";
    default: {
      // Unknown sanitized event: show safe scalar fields only.
      const scalars = Object.entries(data)
        .filter(([, v]) => ["string", "number", "boolean"].includes(typeof v))
        .slice(0, 3)
        .map(([k, v]) => `${k}: ${asText(v)}`);
      return scalars.length ? scalars.join(" · ") : "Lifecycle event recorded";
    }
  }
}

// ---------------------------------------------------------------------------
// Policy violation codes
// ---------------------------------------------------------------------------

export const VIOLATION_LABELS: Record<string, string> = {
  INTERVENTION_NOT_ALLOWED: "Intervention not allowed",
  TREATMENT_EXPOSURE_EXCEEDED: "Treatment exposure exceeds merchant limit",
  DISCOUNT_LIMIT_EXCEEDED: "Discount exceeds merchant limit",
  MIN_MARGIN_VIOLATED: "Minimum margin requirement violated",
  FINANCIAL_EXPOSURE_EXCEEDED: "Financial exposure exceeds merchant limit",
  MIN_SAMPLE_NOT_MET: "Experiment sample size below policy minimum",
  DURATION_EXCEEDED: "Experiment duration exceeds merchant limit",
  CONCURRENT_EXPERIMENT_LIMIT: "Concurrent experiment limit reached",
  SEGMENT_EXPERIMENT_CONFLICT: "Another experiment is active for this segment",
  INVALID_EXPERIMENT_CONFIG: "Experiment configuration invalid",
};

export function violationLabel(code: string): string {
  return VIOLATION_LABELS[code] ?? code;
}

// ---------------------------------------------------------------------------
// Status / stage vocabulary
// ---------------------------------------------------------------------------

export const EXPERIMENT_STATUS_LABELS: Record<ExperimentStatus, string> = {
  proposed: "Proposed",
  approved: "Approved",
  running: "Running",
  rejected: "Rejected",
  completed: "Completed",
  rolled_back: "Rolled back",
  cancelled: "Cancelled",
};

export function experimentStatusLabel(status: ExperimentStatus): string {
  return EXPERIMENT_STATUS_LABELS[status] ?? status;
}

export const DECISION_BADGES: Record<
  StatisticalDecision,
  { label: string; tone: "green" | "red" | "amber" }
> = {
  KEEP: { label: "KEEP", tone: "green" },
  ROLLBACK: { label: "ROLLBACK", tone: "red" },
  INCONCLUSIVE: { label: "INCONCLUSIVE", tone: "amber" },
};

/** Intervention type → merchant-readable name. */
export const INTERVENTION_LABELS: Record<string, string> = {
  payment_method_config: "Payment method configuration",
  offer_discount: "Checkout offer discount",
  partial_payment: "Partial payment",
  expiry_config: "Payment link expiry",
};

export function interventionLabel(type: string): string {
  return INTERVENTION_LABELS[type] ?? humanizeUpper(type);
}
