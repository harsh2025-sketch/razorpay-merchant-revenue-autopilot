import { describe, expect, it } from "vitest";
import {
  ACTION_LABELS,
  ACTOR_LABELS,
  actorStripStages,
  actionLabel,
  actionLoadingLabel,
  auditEventSummary,
  autopilotStatusSentence,
  eventLabel,
  violationLabel,
} from "@/lib/labels";
import { formatInrPaise, formatPercent, formatPp, formatPValue } from "@/lib/format";
import { parseEvidence } from "@/lib/evidence";
import { makeOpportunity } from "./fixtures";

describe("context-aware action labels", () => {
  it("maps every lifecycle action to its specified label", () => {
    expect(ACTION_LABELS.DETECT_OPPORTUNITIES).toBe("Scan for Opportunities");
    expect(ACTION_LABELS.DIAGNOSE_OPPORTUNITY).toBe("Generate Diagnosis");
    expect(ACTION_LABELS.PLAN_EXPERIMENT).toBe("Plan Experiment");
    expect(ACTION_LABELS.EVALUATE_POLICY).toBe("Run Policy Check");
    expect(ACTION_LABELS.DEPLOY_TREATMENT).toBe("Deploy Treatment");
    expect(ACTION_LABELS.CONFIGURE_OFFER_MAPPING).toBe("Deployment Blocked");
    expect(ACTION_LABELS.RUN_EXPERIMENT_BATCH).toBe("Run Next Batch");
    expect(ACTION_LABELS.EVALUATE_EXPERIMENT).toBe("Evaluate Results");
    expect(ACTION_LABELS.ROLLBACK_TREATMENT).toBe("Roll Back Treatment");
    expect(ACTION_LABELS.DONE).toBe("Cycle Complete");
  });

  it("returns no label for STOP and loading labels for active actions", () => {
    expect(actionLabel("STOP")).toBeNull();
    expect(actionLabel("DETECT_OPPORTUNITIES")).toBe("Scan for Opportunities");
    expect(actionLoadingLabel("DETECT_OPPORTUNITIES")).toBe("Scanning…");
    expect(actionLoadingLabel("DIAGNOSE_OPPORTUNITY")).toBe(
      "Generating diagnosis…",
    );
    expect(actionLoadingLabel("ROLLBACK_TREATMENT")).toBe("Rolling back…");
  });
});

describe("autopilot status sentences", () => {
  it("uses the frozen sentences for each state", () => {
    expect(autopilotStatusSentence("IDLE", null)).toBe(
      "Ready to analyze payment performance for optimization opportunities.",
    );
    expect(autopilotStatusSentence("HYPOTHESIS_PENDING", null)).toBe(
      "An actionable conversion opportunity is ready for AI diagnosis.",
    );
    expect(autopilotStatusSentence("POLICY_REVIEW_PENDING", null)).toBe(
      "The proposed experiment is awaiting merchant policy authorization.",
    );
    expect(autopilotStatusSentence("DEPLOYMENT_PENDING", null)).toBe(
      "Policy approved. The treatment is ready for the configured execution boundary.",
    );
    expect(autopilotStatusSentence("DEPLOYMENT_BLOCKED", null)).toBe(
      "Deployment is blocked because this intervention cannot yet be safely mapped to a verified Razorpay resource.",
    );
    expect(autopilotStatusSentence("RUNNING", null)).toBe(
      "The experiment is running toward its fixed sample horizon.",
    );
    expect(autopilotStatusSentence("EVALUATION_PENDING", null)).toBe(
      "Both cohorts reached the sample target. Statistical evaluation is ready.",
    );
  });

  it("reflects the statistical decision for COMPLETED", () => {
    expect(autopilotStatusSentence("COMPLETED", "KEEP")).toContain("kept");
    expect(autopilotStatusSentence("COMPLETED", "ROLLBACK")).toContain(
      "rolled back",
    );
    expect(autopilotStatusSentence("COMPLETED", "INCONCLUSIVE")).toContain(
      "inconclusive",
    );
  });
});

describe("actor strip derivation", () => {
  it("advances completed actors with the lifecycle", () => {
    const idle = actorStripStages("IDLE", "DETECT_OPPORTUNITIES");
    expect(idle[0]).toMatchObject({ id: "detector", status: "current" });
    expect(idle[5]).toMatchObject({ id: "statistics", status: "pending" });

    const policy = actorStripStages("POLICY_REVIEW_PENDING", "EVALUATE_POLICY");
    expect(policy.map((s) => s.status)).toEqual([
      "completed",
      "completed",
      "completed",
      "current",
      "pending",
      "pending",
    ]);

    const running = actorStripStages("RUNNING", "RUN_EXPERIMENT_BATCH");
    expect(running[4]).toMatchObject({ id: "razorpay", status: "current" });
    expect(running[0].status).toBe("completed");

    const evaluating = actorStripStages("EVALUATION_PENDING", "EVALUATE_EXPERIMENT");
    expect(evaluating[5]).toMatchObject({ id: "statistics", status: "current" });
    expect(evaluating[4]).toMatchObject({ id: "razorpay", status: "completed" });

    const rejected = actorStripStages("POLICY_REJECTED", "STOP");
    expect(rejected[3]).toMatchObject({ id: "policy", tone: "rejected" });
  });
});

describe("audit vocabulary", () => {
  it("maps actors and event types to readable names", () => {
    expect(ACTOR_LABELS.razorpay_executor).toBe("Razorpay Executor");
    expect(ACTOR_LABELS.ai).toBe("AI Diagnosis");
    expect(eventLabel("OPPORTUNITY_DETECTED")).toBe("Opportunity detected");
    expect(eventLabel("RAZORPAY_RESOURCE_CANCELLED")).toBe(
      "Razorpay resource cancelled",
    );
  });

  it("builds concise event summaries from safe payload fields", () => {
    expect(
      auditEventSummary("OPPORTUNITY_DETECTED", {
        segment: "android_budget",
        severity: 0.281,
      }),
    ).toContain("android_budget");
    expect(
      auditEventSummary("POLICY_REJECTED", {
        violations: ["DISCOUNT_LIMIT_EXCEEDED"],
      }),
    ).toContain("DISCOUNT_LIMIT_EXCEEDED");
    expect(
      auditEventSummary("RAZORPAY_RESOURCE_CREATED", {
        resource_type: "payment_link",
        razorpay_id: "plink_Q123",
      }),
    ).toContain("plink_Q123");
  });
});

describe("policy violation labels", () => {
  it("maps known violation codes to readable labels", () => {
    expect(violationLabel("DISCOUNT_LIMIT_EXCEEDED")).toBe(
      "Discount exceeds merchant limit",
    );
    expect(violationLabel("TREATMENT_EXPOSURE_EXCEEDED")).toBe(
      "Treatment exposure exceeds merchant limit",
    );
    expect(violationLabel("MIN_MARGIN_VIOLATED")).toBe(
      "Minimum margin requirement violated",
    );
  });
});

describe("formatting", () => {
  it("converts paise to Indian-formatted rupees", () => {
    expect(formatInrPaise(723750000)).toBe("₹72,37,500");
    expect(formatInrPaise(50000000)).toBe("₹5,00,000");
    expect(formatInrPaise(null)).toBe("-");
  });

  it("formats rates and percentage points with proper signs", () => {
    expect(formatPercent(0.4778)).toBe("47.8%");
    expect(formatPp(0.4778 - 0.5861)).toBe("−10.8pp");
    expect(formatPp(0.145)).toBe("+14.5pp");
    expect(formatPValue(0.0012)).toBe("0.0012");
    expect(formatPValue(0.00001)).toBe("< 0.001");
  });
});

describe("evidence parsing", () => {
  it("separates cohorts, payment methods and failure reasons", () => {
    const parsed = parseEvidence(makeOpportunity().evidence);
    expect(parsed.segment).toBe("android_budget");
    expect(parsed.segmentCohort).toEqual({
      attempts: 2141,
      captured: 1023,
      conversionRate: 0.4778,
    });
    expect(parsed.comparisonCohort.conversionRate).toBe(0.5861);
    expect(parsed.paymentMethods.map((m) => m.method)).toEqual(["upi", "card"]);
    expect(parsed.failureReasons[0]).toEqual({
      reason: "insufficient_funds",
      count: 210,
    });
    expect(parsed.additional).toHaveLength(0);
  });

  it("routes unknown sanitized keys to the additional area", () => {
    const parsed = parseEvidence({
      segment_conversion_rate: 0.5,
      custom_scalar: "hello",
    });
    expect(parsed.additional).toEqual([
      { key: "custom_scalar", value: "hello" },
    ]);
  });
});
