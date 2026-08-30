"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { ArrowLeft } from "lucide-react";
import {
  advanceAutopilot,
  getCycle,
  getOverview,
  rollbackExperiment,
  runExperimentToDecision,
} from "@/lib/api";
import { describeApiError, type DescribedError } from "@/lib/errors";
import { shortId } from "@/lib/format";
import type {
  AutopilotCycle,
  AutopilotNextAction,
  MerchantOverview,
  StatisticalDecision,
} from "@/lib/types";
import { AIDiagnosis } from "./ai-diagnosis";
import { AIAnalysisBoundary } from "./ai-analysis-boundary";
import { CycleStageStrip, deriveCycleStages } from "./cycle-stage-strip";
import { ExperimentPlan } from "./experiment-plan";
import { ExperimentProgress } from "./experiment-progress";
import { ObservedEvidence } from "./observed-evidence";
import { PolicyDecision } from "./policy-decision";
import { PrimaryAutopilotAction } from "./primary-autopilot-action";
import {
  DeploymentBlockedPanel,
  RazorpayResourcePanel,
} from "./razorpay-resource-panel";
import { RecentActivity } from "./recent-activity";
import {
  InconclusiveNote,
  StatisticalResult,
  TreatmentRetainedNote,
} from "./statistical-result";
import { StatusBadge, type BadgeTone } from "./badges";
import { InlineError } from "./inline-error";

function isOneClickExperimentAction(action: AutopilotNextAction | null): boolean {
  return action === "RUN_EXPERIMENT_BATCH" || action === "EVALUATE_EXPERIMENT";
}

function cycleStatusBadge(
  cycle: AutopilotCycle,
): { label: string; tone: BadgeTone } {
  if (cycle.result?.decision) {
    const decision = cycle.result.decision;
    const tone: BadgeTone =
      decision === "KEEP" ? "green" : decision === "ROLLBACK" ? "red" : "amber";
    return { label: decision, tone };
  }
  const experiment = cycle.experiment;
  if (experiment) {
    switch (experiment.status) {
      case "running":
        return { label: "Running", tone: "blue" };
      case "approved":
        return { label: "Approved", tone: "blue" };
      case "rejected":
        return { label: "Rejected", tone: "red" };
      case "rolled_back":
      case "cancelled":
        return { label: "Rolled back", tone: "red" };
      case "completed":
        return { label: "Completed", tone: "green" };
      default:
        return { label: "Planned", tone: "gray" };
    }
  }
  if (cycle.hypothesis) return { label: "Diagnosed", tone: "gray" };
  return { label: "Detected", tone: "gray" };
}

/**
 * Cycle detail state container. The page is a chronological decision record:
 * evidence → (boundary) → AI → plan → policy → Razorpay → progress → result.
 * Task 21C combines only the runtime + statistics user interaction into one
 * backend run-to-decision request after the treatment resource exists.
 */
export function CycleView({
  initialCycle,
  initialOverview,
}: {
  initialCycle: AutopilotCycle;
  initialOverview: MerchantOverview | null;
}) {
  const merchantId = initialCycle.opportunity.merchant_id;
  const [cycle, setCycle] = useState(initialCycle);
  const [overview, setOverview] = useState(initialOverview);
  const [actionLoading, setActionLoading] = useState(false);
  const [rolling, setRolling] = useState(false);
  const [error, setError] = useState<DescribedError | null>(null);

  const opportunityId = cycle.opportunity.id;
  const status = overview?.autopilot_status ?? null;
  const isCurrentCycle = status?.latest_opportunity_id === opportunityId;
  const nextAction: AutopilotNextAction | null = isCurrentCycle
    ? (status?.next_action ?? null)
    : null;
  const deploymentBlocked =
    isCurrentCycle && status?.state === "DEPLOYMENT_BLOCKED";

  const refetch = useCallback(async () => {
    const [freshCycle, freshOverview] = await Promise.all([
      getCycle(opportunityId),
      getOverview(merchantId).catch(() => null),
    ]);
    setCycle(freshCycle);
    if (freshOverview) setOverview(freshOverview);
  }, [merchantId, opportunityId]);

  const handleStep = useCallback(async () => {
    setError(null);
    setActionLoading(true);
    try {
      if (isOneClickExperimentAction(nextAction)) {
        const experimentId = cycle.experiment?.id;
        if (!experimentId) {
          throw new Error("No experiment is available to run.");
        }
        await runExperimentToDecision(experimentId);
      } else {
        await advanceAutopilot(merchantId);
      }
      await refetch();
    } catch (caught) {
      setError(describeApiError(caught));
    } finally {
      setActionLoading(false);
    }
  }, [cycle.experiment?.id, merchantId, nextAction, refetch]);

  const handleRollback = useCallback(async () => {
    const experimentId = cycle.experiment?.id;
    if (!experimentId) return;
    setError(null);
    setRolling(true);
    try {
      await rollbackExperiment(experimentId);
      await refetch();
    } catch (caught) {
      setError(describeApiError(caught));
    } finally {
      setRolling(false);
    }
  }, [cycle.experiment?.id, refetch]);

  const badge = cycleStatusBadge(cycle);
  const stages = deriveCycleStages(cycle, deploymentBlocked);
  const result = cycle.result;
  const decision: StatisticalDecision | null = result?.decision ?? null;

  const headerAction =
    nextAction === "ROLLBACK_TREATMENT" || nextAction === "STOP"
      ? null
      : nextAction;

  return (
    <div className="space-y-5">
      <div>
        <Link
          href="/autopilot"
          className="inline-flex items-center gap-1 text-[13px] font-medium text-gray-500 hover:text-gray-800"
        >
          <ArrowLeft size={13} aria-hidden />
          Autopilot
        </Link>
        <div className="mt-2 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
          <div className="min-w-0">
            <h1 className="text-[26px] font-semibold leading-tight tracking-tight text-gray-900">
              {cycle.opportunity.segment ?? "Unknown segment"} optimization cycle
            </h1>
            <p className="mt-1 text-[13px] text-gray-500">
              Opportunity{" "}
              <span className="font-mono text-gray-600">
                {shortId(cycle.opportunity.id)}
              </span>{" "}
              · detected{" "}
              {cycle.opportunity.created_at
                ? new Date(cycle.opportunity.created_at).toISOString().slice(0, 10)
                : ""}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <StatusBadge tone={badge.tone}>{badge.label}</StatusBadge>
            {headerAction && (
              <PrimaryAutopilotAction
                action={headerAction}
                loading={actionLoading}
                onAction={handleStep}
                size="compact"
              />
            )}
          </div>
        </div>
        <CycleStageStrip stages={stages} className="mt-3" />
      </div>

      {error && (
        <InlineError
          error={error}
          onRetry={
            error.code === "EXECUTION_STATE_CONFLICT" ? undefined : handleStep
          }
        />
      )}

      <ObservedEvidence opportunity={cycle.opportunity} />

      {cycle.hypothesis && <AIAnalysisBoundary />}

      {cycle.hypothesis && (
        <AIDiagnosis
          hypothesis={cycle.hypothesis}
          modelName={cycle.hypothesis.ai_model}
        />
      )}

      {cycle.experiment && <ExperimentPlan experiment={cycle.experiment} />}

      {cycle.policy_decision && cycle.experiment && (
        <PolicyDecision
          decision={cycle.policy_decision}
          experiment={cycle.experiment}
          policy={cycle.merchant_policy}
        />
      )}

      {cycle.razorpay_resource && (
        <RazorpayResourcePanel resource={cycle.razorpay_resource} />
      )}
      {!cycle.razorpay_resource && deploymentBlocked && (
        <DeploymentBlockedPanel
          interventionLabel={
            cycle.experiment
              ? interventionDisplayName(cycle.experiment.intervention_type)
              : "unknown intervention"
          }
        />
      )}

      {cycle.progress && !result && (
        <ExperimentProgress progress={cycle.progress} />
      )}

      {result && decision && (
        <>
          <StatisticalResult
            result={result}
            resource={cycle.razorpay_resource}
            onRollback={
              cycle.razorpay_resource?.status === "active"
                ? handleRollback
                : undefined
            }
            rolling={rolling}
          />
          {decision === "KEEP" && (
            <TreatmentRetainedNote resource={cycle.razorpay_resource} />
          )}
          {decision === "INCONCLUSIVE" && <InconclusiveNote />}
        </>
      )}

      <RecentActivity
        events={cycle.audit_events}
        chainValid={cycle.audit_chain_valid}
        title="Cycle Activity"
        href="/audit"
        linkLabel="View full Audit Log"
      />
    </div>
  );
}

function interventionDisplayName(type: string): string {
  switch (type) {
    case "payment_method_config":
      return "Payment method configuration";
    case "offer_discount":
      return "Checkout offer discount";
    case "partial_payment":
      return "Partial payment";
    case "expiry_config":
      return "Payment link expiry";
    default:
      return type;
  }
}
