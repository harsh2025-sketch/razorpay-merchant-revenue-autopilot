"use client";

import { useCallback, useEffect, useState } from "react";
import {
  advanceAutopilot,
  getDetectionReadiness,
  getOverview,
  runExperimentToDecision,
  startNewAutopilotCycle,
} from "@/lib/api";
import { DEFAULT_MERCHANT_ID } from "@/lib/constants";
import { describeApiError, type DescribedError } from "@/lib/errors";
import { formatInrPaise, formatInt, formatPercent } from "@/lib/format";
import type { AutopilotState, MerchantOverview } from "@/lib/types";
import { AutopilotStatus } from "./autopilot-status";
import { InlineError } from "./inline-error";
import { LoadingButton } from "./loading-button";
import { MetricCell } from "./metric-cell";
import { SegmentConversionChart } from "./segment-conversion-chart";

function isOneClickExperimentAction(action: string | null): boolean {
  return action === "RUN_EXPERIMENT_BATCH" || action === "EVALUATE_EXPERIMENT";
}

/**
 * Focused Overview client state container.
 *
 * The Overview intentionally shows only the merchant's current Autopilot state,
 * three decision-useful metrics and segment conversion evidence. Detailed
 * payment-method evidence and the full audit ledger remain available on their
 * dedicated product surfaces instead of being duplicated here.
 *
 * `initialAudit` remains accepted for compatibility with existing callers and
 * tests, but it is intentionally not rendered or refetched on this page.
 */
export function OverviewView({
  initialOverview,
  initialDetectionReady,
}: {
  initialOverview: MerchantOverview;
  initialDetectionReady?: boolean;
  initialAudit?: unknown;
}) {
  const merchantId = initialOverview.merchant.merchant_id;
  const [overview, setOverview] = useState(initialOverview);
  const [detectionReady, setDetectionReady] = useState(
    initialDetectionReady ?? true,
  );
  const [actionLoading, setActionLoading] = useState(false);
  const [restartLoading, setRestartLoading] = useState(false);
  const [restartAvailable, setRestartAvailable] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<DescribedError | null>(null);
  const [errorKind, setErrorKind] = useState<"action" | "restart" | "refresh">(
    "action",
  );
  const [stepMessage, setStepMessage] = useState<string | null>(null);
  const [viewCycleHref, setViewCycleHref] = useState<string | null>(null);

  useEffect(() => {
    if (initialDetectionReady !== undefined) return;

    let active = true;
    void getDetectionReadiness(merchantId).then(
      (readiness) => {
        if (active) setDetectionReady(readiness.ready);
      },
      () => {
        // Overview data remains useful even if this auxiliary readiness read
        // fails; the next explicit action will still be validated by backend.
      },
    );

    return () => {
      active = false;
    };
  }, [initialDetectionReady, merchantId]);

  const status = overview.autopilot_status;
  const waitingForData =
    status.next_action === "DETECT_OPPORTUNITIES" && !detectionReady;
  const waitingForLiveOutcomes =
    merchantId !== DEFAULT_MERCHANT_ID &&
    isOneClickExperimentAction(status.next_action);
  const effectiveNextAction =
    waitingForData || waitingForLiveOutcomes ? null : status.next_action;

  const refreshOverviewState = useCallback(async () => {
    const [freshOverview, freshReadiness] = await Promise.all([
      getOverview(merchantId),
      getDetectionReadiness(merchantId),
    ]);
    setOverview(freshOverview);
    setDetectionReady(freshReadiness.ready);
    return freshOverview;
  }, [merchantId]);

  const handleAction = useCallback(async () => {
    setError(null);
    setActionLoading(true);

    try {
      if (isOneClickExperimentAction(status.next_action)) {
        const experimentId = status.latest_experiment_id;
        if (!experimentId) {
          throw new Error("No active experiment is available to run.");
        }
        const result = await runExperimentToDecision(experimentId);
        setRestartAvailable(false);
        setStepMessage(
          `Experiment reached ${result.control_attempts}/${result.sample_target_per_variant} control and ${result.treatment_attempts}/${result.sample_target_per_variant} treatment observations. Decision ${result.decision}.`,
        );
        setViewCycleHref(
          status.latest_opportunity_id
            ? `/autopilot/${status.latest_opportunity_id}`
            : null,
        );
      } else {
        const step = await advanceAutopilot(merchantId);
        setRestartAvailable(step.step === "DEPLOYMENT_BLOCKED");
        setStepMessage(step.message);
        setViewCycleHref(
          step.entity_type === "opportunity" && step.entity_id
            ? `/autopilot/${step.entity_id}`
            : null,
        );
      }
    } catch (caught) {
      setStepMessage(null);
      setError(describeApiError(caught));
      setErrorKind("action");
      setActionLoading(false);
      return;
    }

    try {
      await refreshOverviewState();
    } catch (caught) {
      setError(describeApiError(caught));
      setErrorKind("refresh");
    } finally {
      setActionLoading(false);
    }
  }, [merchantId, refreshOverviewState, status]);

  const handleStartNewCycle = useCallback(async () => {
    setError(null);
    setRestartLoading(true);
    let nextOpportunity: Awaited<ReturnType<typeof startNewAutopilotCycle>>;
    try {
      nextOpportunity = await startNewAutopilotCycle(merchantId);
    } catch (caught) {
      setError(describeApiError(caught));
      setErrorKind("restart");
      setRestartLoading(false);
      return;
    }

    setRestartAvailable(false);
    if (nextOpportunity) {
      setStepMessage(
        `New optimization cycle ready for ${nextOpportunity.segment ?? "the next detected"} segment.`,
      );
      setViewCycleHref(`/autopilot/${nextOpportunity.id}`);
    } else {
      setStepMessage(
        "Previous cycle closed. Add new payment data before another detection pass.",
      );
      setViewCycleHref(null);
    }

    try {
      await refreshOverviewState();
    } catch (caught) {
      setError(describeApiError(caught));
      setErrorKind("refresh");
    } finally {
      setRestartLoading(false);
    }
  }, [merchantId, refreshOverviewState]);

  const refreshAll = useCallback(async () => {
    setError(null);
    setRefreshing(true);
    try {
      await refreshOverviewState();
    } catch (caught) {
      setError(describeApiError(caught));
      setErrorKind("refresh");
    } finally {
      setRefreshing(false);
    }
  }, [refreshOverviewState]);

  const metrics = overview.metrics;

  const activeCycleValue = waitingForData
    ? "Awaiting data"
    : waitingForLiveOutcomes
      ? "Awaiting outcomes"
      : lifecycleLabel(status.state, status.latest_experiment_status);
  const activeCycleSub = waitingForData
    ? "Append new transactions before another scan"
    : waitingForLiveOutcomes
      ? "Real assigned payment outcomes required"
      : status.state === "COMPLETED" && status.latest_statistical_decision != null
        ? `Decision ${status.latest_statistical_decision}`
        : `${formatInt(status.active_experiment_count)} active experiment${
            status.active_experiment_count === 1 ? "" : "s"
          }`;

  return (
    <div className="space-y-5">
      <AutopilotStatus
        state={status.state}
        nextAction={effectiveNextAction}
        latestDecision={status.latest_statistical_decision}
        waitingForData={waitingForData}
        waitingForLiveOutcomes={waitingForLiveOutcomes}
        loading={actionLoading}
        restartLoading={restartLoading}
        restartAvailable={restartAvailable}
        stepMessage={stepMessage}
        viewCycleHref={viewCycleHref}
        onAction={
          waitingForData || waitingForLiveOutcomes ? undefined : handleAction
        }
        onStartNewCycle={handleStartNewCycle}
      />

      {error && (
        <InlineError
          error={error}
          onRetry={
            errorKind === "action"
              ? handleAction
              : errorKind === "restart"
                ? handleStartNewCycle
                : refreshAll
          }
        />
      )}

      <section
        aria-label="Merchant metrics"
        className="grid overflow-hidden rounded-xl border border-slate-200 bg-slate-200 shadow-[0_10px_35px_rgba(15,23,42,0.05)] sm:grid-cols-3 sm:gap-px"
      >
        <div className="border-b border-slate-200 bg-gradient-to-br from-white to-indigo-50/35 sm:border-b-0">
          <MetricCell
            label="Baseline Conversion"
            value={formatPercent(metrics.conversion_rate)}
            sub={`${formatInt(metrics.captured)} of ${formatInt(metrics.attempts)} captured`}
          />
        </div>
        <div className="border-b border-slate-200 bg-gradient-to-br from-white to-emerald-50/30 sm:border-b-0">
          <MetricCell
            label="Captured GMV"
            value={formatInrPaise(overview.captured_gmv_paise)}
            sub={`of ${formatInrPaise(overview.attempted_gmv_paise)} attempted`}
          />
        </div>
        <div className="bg-gradient-to-br from-white to-sky-50/40">
          <MetricCell
            label="Active Cycle"
            value={activeCycleValue}
            sub={activeCycleSub}
          />
        </div>
      </section>

      <SegmentConversionChart segments={overview.segment_metrics} />

      <div className="flex justify-end">
        <LoadingButton
          variant="outline"
          loading={refreshing}
          loadingLabel="Refreshing…"
          onClick={refreshAll}
          className="text-[12.5px]"
        >
          Refresh data
        </LoadingButton>
      </div>
    </div>
  );
}

function lifecycleLabel(
  state: AutopilotState,
  latestExperimentStatus: string | null,
): string {
  switch (state) {
    case "IDLE":
      return "Ready";
    case "HYPOTHESIS_PENDING":
      return "Detected";
    case "EXPERIMENT_PENDING":
      return "Hypothesis";
    case "POLICY_REVIEW_PENDING":
      return "Planned";
    case "DEPLOYMENT_PENDING":
      return "Approved";
    case "DEPLOYMENT_BLOCKED":
      return "Blocked";
    case "POLICY_REJECTED":
      return "Rejected";
    case "RUNNING":
      return "Ready to run";
    case "EVALUATION_PENDING":
      return "Ready to evaluate";
    case "COMPLETED":
      return latestExperimentStatus
        ? formatStatusLabel(latestExperimentStatus)
        : "Completed";
  }
}

function formatStatusLabel(status: string): string {
  switch (status) {
    case "proposed":
      return "Planned";
    case "approved":
      return "Approved";
    case "running":
      return "Running";
    case "rejected":
      return "Rejected";
    case "completed":
      return "Completed";
    case "rolled_back":
      return "Rolled back";
    case "cancelled":
      return "Cancelled";
    default:
      return status;
  }
}
