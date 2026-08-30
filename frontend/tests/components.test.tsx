import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AutopilotStatus } from "@/components/autopilot-status";
import { CycleStageStrip, deriveCycleStages } from "@/components/cycle-stage-strip";
import { ObservedEvidence } from "@/components/observed-evidence";
import { AIAnalysisBoundary } from "@/components/ai-analysis-boundary";
import { AIDiagnosis } from "@/components/ai-diagnosis";
import { ExperimentPlan } from "@/components/experiment-plan";
import { PolicyDecision } from "@/components/policy-decision";
import {
  DeploymentBlockedPanel,
  RazorpayResourcePanel,
} from "@/components/razorpay-resource-panel";
import { ExperimentProgress } from "@/components/experiment-progress";
import {
  StatisticalResult,
  TreatmentRetainedNote,
} from "@/components/statistical-result";
import { PaymentMethodTable } from "@/components/payment-method-table";
import { SegmentConversionChart } from "@/components/segment-conversion-chart";
import { AuditTimeline } from "@/components/audit-timeline";
import { IntegrityBadge } from "@/components/badges";
import { InlineError } from "@/components/inline-error";
import {
  makeAuditEvent,
  makeCycle,
  makeExperiment,
  makeHypothesis,
  makePolicy,
  makePolicyDecision,
  makeProgress,
  makeResult,
  makeResource,
  paymentMethodMetrics,
  segmentMetrics,
} from "./fixtures";

describe("autopilot status actions", () => {
  it("IDLE shows Scan for Opportunities", () => {
    render(
      <AutopilotStatus state="IDLE" nextAction="DETECT_OPPORTUNITIES" latestDecision={null} />,
    );
    expect(
      screen.getByRole("button", { name: "Scan for Opportunities" }),
    ).toBeEnabled();
  });

  it("POLICY_REVIEW_PENDING shows Run Policy Check", () => {
    render(
      <AutopilotStatus
        state="POLICY_REVIEW_PENDING"
        nextAction="EVALUATE_POLICY"
        latestDecision={null}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Run Policy Check" }),
    ).toBeEnabled();
  });

  it("DEPLOYMENT_PENDING shows Deploy Treatment", () => {
    render(
      <AutopilotStatus
        state="DEPLOYMENT_PENDING"
        nextAction="DEPLOY_TREATMENT"
        latestDecision={null}
      />,
    );
    expect(screen.getByRole("button", { name: "Deploy Treatment" })).toBeEnabled();
  });

  it("RUNNING shows Run Experiment", () => {
    render(
      <AutopilotStatus
        state="RUNNING"
        nextAction="RUN_EXPERIMENT_BATCH"
        latestDecision={null}
      />,
    );
    expect(screen.getByRole("button", { name: "Run Experiment" })).toBeEnabled();
  });

  it("EVALUATION_PENDING shows Run Experiment", () => {
    render(
      <AutopilotStatus
        state="EVALUATION_PENDING"
        nextAction="EVALUATE_EXPERIMENT"
        latestDecision={null}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Run Experiment" }),
    ).toBeEnabled();
  });

  it("a completed cycle hides the mutation and DONE renders disabled", () => {
    render(
      <AutopilotStatus state="COMPLETED" nextAction="DONE" latestDecision="KEEP" />,
    );
    const button = screen.getByRole("button", { name: "Cycle Complete" });
    expect(button).toBeDisabled();
  });

  it("a blocked deployment renders a disabled action, not a mutation", () => {
    render(
      <AutopilotStatus
        state="DEPLOYMENT_BLOCKED"
        nextAction="CONFIGURE_OFFER_MAPPING"
        latestDecision={null}
      />,
    );
    expect(
      screen.getByRole("button", { name: /Deployment Blocked/ }),
    ).toBeDisabled();
  });

  it("STOP renders no button at all", () => {
    render(
      <AutopilotStatus state="POLICY_REJECTED" nextAction="STOP" latestDecision={null} />,
    );
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("Merchant policy rejected the proposed experiment.")).toBeInTheDocument();
  });
});

describe("overview data surfaces", () => {
  it("renders the five segments in the segment chart", () => {
    const { container } = render(<SegmentConversionChart segments={segmentMetrics} />);
    expect(container.querySelectorAll(".recharts-bar-rectangle")).toHaveLength(5);
    expect(screen.getAllByText("android_budget").length).toBeGreaterThan(0);
    expect(screen.getAllByText("returning_high_value").length).toBeGreaterThan(0);
  });

  it("renders the payment method table", () => {
    render(<PaymentMethodTable methods={paymentMethodMetrics} />);
    expect(screen.getByText("UPI")).toBeInTheDocument();
    expect(screen.getByText("Netbanking")).toBeInTheDocument();
    expect(screen.getByText("Success Rate")).toBeInTheDocument();
    expect(screen.getAllByText("2,630")).toHaveLength(1);
  });
});

describe("cycle detail sections", () => {
  it("opportunity-only cycle renders evidence only (no AI, no boundary)", () => {
    render(
      <>
        <ObservedEvidence opportunity={makeCycle().opportunity} />
      </>,
    );
    expect(screen.getByText("Observed Evidence")).toBeInTheDocument();
    expect(screen.queryByText("AI Diagnosis")).not.toBeInTheDocument();
    expect(
      screen.queryByText("AI Analysis · Generated by LLM"),
    ).not.toBeInTheDocument();
  });

  it("renders observed evidence values and readable sections", () => {
    render(<ObservedEvidence opportunity={makeCycle().opportunity} />);
    expect(screen.getAllByText("47.8%").length).toBeGreaterThan(0);
    expect(screen.getAllByText("58.6%").length).toBeGreaterThan(0);
    expect(screen.getAllByText("−10.8pp").length).toBeGreaterThan(0);
    expect(screen.getByText("Comparison Conversion")).toBeInTheDocument();
    expect(screen.getByText("Insufficient funds")).toBeInTheDocument();
  });

  it("the trust boundary renders between evidence and AI", () => {
    render(<AIAnalysisBoundary />);
    expect(
      screen.getByText("AI Analysis · Generated by LLM"),
    ).toBeInTheDocument();
  });

  it("AI diagnosis renders proposal fields without fake percentages", () => {
    render(<AIDiagnosis hypothesis={makeHypothesis()} />);
    expect(screen.getByText("AI Diagnosis", { selector: "p" })).toBeInTheDocument();
    expect(screen.getByText("Medium", { ignore: "p" })).toBeInTheDocument();
    expect(screen.getByText(/UPI enabled · Card disabled/)).toBeInTheDocument();
    expect(screen.getByText("payment_method.upi.success_rate")).toBeInTheDocument();
  });

  it("experiment plan renders control vs treatment and plan limits", () => {
    render(<ExperimentPlan experiment={makeExperiment()} />);
    expect(screen.getByText("Control")).toBeInTheDocument();
    expect(screen.getByText("Treatment")).toBeInTheDocument();
    expect(screen.getByText("Merchant default payment methods")).toBeInTheDocument();
    expect(screen.getByText("UPI enabled")).toBeInTheDocument();
    expect(screen.getByText("Card disabled")).toBeInTheDocument();
    expect(screen.getByText("10%")).toBeInTheDocument();
    expect(screen.getByText("conversion_rate")).toBeInTheDocument();
    expect(screen.getByText("200")).toBeInTheDocument();
    expect(screen.getByText("72h")).toBeInTheDocument();
  });

  it("policy approval renders authorized copy and policy limits", () => {
    render(
      <PolicyDecision
        decision={makePolicyDecision()}
        experiment={makeExperiment()}
        policy={makePolicy()}
      />,
    );
    expect(screen.getByText("Approved")).toBeInTheDocument();
    expect(
      screen.getByText("Authorized by deterministic merchant policy."),
    ).toBeInTheDocument();
    expect(screen.getByText("Treatment exposure")).toBeInTheDocument();
    expect(screen.getByText("Allowed max 10%")).toBeInTheDocument();
  });

  it("merchant max discount renders when the intervention carries one", () => {
    render(
      <PolicyDecision
        decision={makePolicyDecision()}
        experiment={makeExperiment({
          intervention_type: "offer_discount",
          treatment_config: { discount_pct: 0.05 },
        })}
        policy={makePolicy()}
      />,
    );
    expect(screen.getByText("Merchant max 15%")).toBeInTheDocument();
    expect(screen.getByText("Discount")).toBeInTheDocument();
  });

  it("policy rejection renders violation codes, proposed vs max, and no override", () => {
    const experiment = makeExperiment({
      intervention_type: "offer_discount",
      treatment_config: { discount_pct: 0.2 },
    });
    render(
      <PolicyDecision
        decision={makePolicyDecision({
          decision: "REJECT",
          violations: ["DISCOUNT_LIMIT_EXCEEDED", "MIN_MARGIN_VIOLATED"],
        })}
        experiment={experiment}
        policy={makePolicy()}
      />,
    );
    expect(screen.getByText("Rejected")).toBeInTheDocument();
    expect(
      screen.getByText("The AI proposal exceeded merchant-defined constraints."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Discount exceeds merchant limit"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Minimum margin requirement violated"),
    ).toBeInTheDocument();
    expect(screen.getByText("Merchant max 15%")).toBeInTheDocument();
    // The override affordance must never exist.
    const override = screen.queryByText(/override/i);
    expect(override).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("razorpay resource renders id, copy affordance and test mode statement", () => {
    render(<RazorpayResourcePanel resource={makeResource()} />);
    expect(screen.getByText("plink_Q8xKmPq2vWxYz1234")).toBeInTheDocument();
    expect(
      screen.getByText(
        "One real Razorpay Test Mode treatment resource is deployed. Experimental customer traffic is simulated separately.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByTitle("Copy Razorpay ID")).toBeInTheDocument();
  });

  it("razorpay id is copyable via the clipboard", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn();
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    render(<RazorpayResourcePanel resource={makeResource()} />);
    await user.click(screen.getByTitle("Copy Razorpay ID"));
    expect(writeText).toHaveBeenCalledWith("plink_Q8xKmPq2vWxYz1234");
    expect(screen.getByText("Copied")).toBeInTheDocument();
  });

  it("deployment blocked state renders the fail-closed copy", () => {
    render(<DeploymentBlockedPanel interventionLabel="Checkout offer discount" />);
    expect(screen.getByText("Deployment Blocked")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Automated deployment is disabled until this semantic discount is mapped to a verified pre-created Razorpay Offer.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("The system fails closed rather than guessing an Offer ID."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("running progress shows cohort targets and never a p-value", () => {
    render(<ExperimentProgress progress={makeProgress()} />);
    expect(screen.getByText("Control")).toBeInTheDocument();
    expect(screen.getByText("Treatment")).toBeInTheDocument();
    expect(screen.getByText("120 / 200")).toBeInTheDocument();
    expect(screen.getByText("104 / 200")).toBeInTheDocument();
    expect(screen.queryByText(/p-value/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/lift/i)).not.toBeInTheDocument();
  });

  it("KEEP result renders decision, statistics and the mandatory explanation", () => {
    render(
      <>
        <StatisticalResult result={makeResult()} resource={makeResource()} />
        <TreatmentRetainedNote resource={makeResource()} />
      </>,
    );
    expect(screen.getByText("KEEP")).toBeInTheDocument();
    expect(screen.getByText("p-value")).toBeInTheDocument();
    expect(screen.getByText("0.0012")).toBeInTheDocument();
    expect(screen.getByText("+14.5pp")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Decision generated by fixed-horizon statistical evaluation. The LLM does not participate in this decision.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /roll back/i })).toBeNull();
    expect(screen.getByText(/Treatment retained/)).toBeInTheDocument();
  });

  it("ROLLBACK result offers the rollback button only with an active resource", () => {
    const result = makeResult({ decision: "ROLLBACK", absolute_lift: -0.12 });
    const { rerender } = render(
      <StatisticalResult result={result} resource={makeResource()} />,
    );
    expect(screen.getByRole("button", { name: "Roll Back Treatment" })).toBeEnabled();

    rerender(
      <StatisticalResult
        result={result}
        resource={makeResource({ status: "cancelled" })}
      />,
    );
    expect(screen.queryByRole("button", { name: /roll back/i })).toBeNull();

    rerender(<StatisticalResult result={result} resource={null} />);
    expect(screen.queryByRole("button", { name: /roll back/i })).toBeNull();
  });

  it("INCONCLUSIVE renders without any action", () => {
    render(
      <StatisticalResult
        result={makeResult({ decision: "INCONCLUSIVE", is_significant: false })}
        resource={makeResource()}
      />,
    );
    expect(screen.getByText("INCONCLUSIVE")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("cycle stage strip marks a rejected policy decision", () => {
    const cycle = makeCycle({
      hypothesis: makeHypothesis(),
      experiment: makeExperiment({ status: "rejected" }),
      policy_decision: makePolicyDecision({ decision: "REJECT" }),
    });
    const stages = deriveCycleStages(cycle, false);
    render(<CycleStageStrip stages={stages} />);
    expect(screen.getByText("Policy")).toBeInTheDocument();
    expect(stages.find((s) => s.id === "policy")?.status).toBe("rejected");
    expect(stages.find((s) => s.id === "razorpay")?.status).toBe("pending");
  });
});

describe("audit log", () => {
  it("renders events with the VERIFIED integrity badge", () => {
    render(
      <AuditTimeline
        events={[makeAuditEvent(), makeAuditEvent({ event_type: "POLICY_APPROVED", actor: "policy" })]}
        chainValid={true}
      />,
    );
    expect(screen.getByText("Verified")).toBeInTheDocument();
    expect(screen.getByText("Opportunity detected")).toBeInTheDocument();
    expect(screen.getByText("Policy approved")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "All" })).toBeInTheDocument();
  });

  it("renders the failure banner while keeping events visible", () => {
    render(<AuditTimeline events={[makeAuditEvent()]} chainValid={false} />);
    expect(
      screen.getByText(
        "Audit verification failed. Event records may have been modified.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Verification failed")).toBeInTheDocument();
    expect(screen.getByText("Opportunity detected")).toBeInTheDocument();
  });

  it("integrity badge switches between verified and failed", () => {
    const { rerender } = render(<IntegrityBadge valid={true} />);
    expect(screen.getByText("Verified")).toBeInTheDocument();
    rerender(<IntegrityBadge valid={false} />);
    expect(screen.getByText("Verification failed")).toBeInTheDocument();
  });
});

describe("global inline error", () => {
  it("renders mapped copy and a single retry control", () => {
    const { rerender } = render(
      <InlineError
        error={{
          title: "Unable to connect to Revenue Autopilot.",
          detail: null,
          code: null,
          tone: "red",
        }}
        onRetry={() => {}}
      />,
    );
    expect(
      screen.getByText("Unable to connect to Revenue Autopilot."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();

    rerender(
      <InlineError
        error={{
          title: "The previous external operation is still unresolved. Automatic retry is disabled.",
          detail: null,
          code: null,
          tone: "amber",
        }}
      />,
    );
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });
});
