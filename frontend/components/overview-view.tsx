"use client";

import { useCallback, useEffect, useState } from "react";
import {
  advanceAutopilot,
  getDetectionReadiness,
  getMerchantAudit,
  getOverview,
  runExperimentToDecision,
  startNewAutopilotCycle,
} from "@/lib/api";
import { DEFAULT_MERCHANT_ID, RECENT_ACTIVITY_LIMIT } from "@/lib/constants";
import { describeApiError, type DescribedError } from "@/lib/errors";
import { formatInrPaise, formatInt, formatPercent } from "@/lib/format";
import type { AuditEvent, AutopilotState, MerchantOverview } from "@/lib/types";
import { AutopilotStatus } from "./autopilot-status";
import { InlineError } from "./inline-error";
import { LoadingButton } from "./loading-button";
import { MetricCell } from "./metric-cell";
import { PaymentMethodTable } from "./payment-method-table";
import { RecentActivity } from "./recent-activity";
import { SegmentConversionChart } from "./segment-conversion-chart";

function isOneClickExperimentAction(action: string | null): boolean {
  return action === "RUN_EXPERIMENT_BATCH" || action === "EVALUATE_EXPERIMENT";
}

/**
 * Overview client state container.
 *
 * The server sends the critical merchant Overview first. Audit preview and
 * detector readiness are hydrated afterwards so slow auxiliary reads never
 * delay the useful first paint. Mutations still perform a full uncached refresh
 * before the next user action is exposed.
 */
export function OverviewView({
  initialOverview,
  initialAudit,
  initialDetectionReady,
}: {
  initialOverview: MerchantOverview;
  initialAudit?: AuditEvent[];
  initialDetectionReady?: boolean;
}) {
  const merchantId = initialOverview.merchant.merchant_id;
  const [overview, setOverview] = useState(initialOverview);
  const [audit, setAudit] = useState<AuditEvent[]>(initialAudit ?? []);
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
    if (initialAudit !== undefined && initialDetectionReady !== undefined) return;

    let active = true;
    void Promise.allSettled([
      getMerchantAudit(merchantId, RECENT_ACTIVITY_LIMIT),
      getDetectionReadiness(merchantId),
    ]).then(([auditResult, readinessResult]) => {
      if (!active) return;
      if (auditResult.status === "fulfilled") setAudit(auditResult.value);
      if (readinessResult.status === "fulfilled") {
        setDetectionReady(readinessResult.value.ready);
      }
    });

    return () => {
      active = false;
    };
  }, [initialAudit, initialDetectionReady, merchantId]);

  const status = overview.autopilot_status;
  const waitingForData =
    status.next_action === "DETECT_OPPORTUNITIES" && !detectionReady;
  const waitingForLiveOutcomes =
    merchantId !== DEFAULT_MERCHANT_ID &&
    isOneClickExperimentAction(status.next_action);
  const effectiveNextAction =
    waitingForData || waitingForLiveOutcomes ? null : status.next_action;

  const refreshOverviewAndAudit = useCallback(async () => {
    const [freshOverview, freshAudit, freshReadiness] = await Promise.all([
      getOverview(merchantId),
      getMerchantAudit(merchantId, RECENT_ACTIVITY_LIMIT),
      getDetectionReadiness(merchantId),
    ]);
    setOverview(freshOverview);
    setAudit(freshAudit);
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
      await refreshOverviewAndAudit();
    } catch (caught) {
      setError(describeApiError(caught));
      setErrorKind("refresh");
    } finally {
      setActionLoading(false);
    }
  }, [merchantId, refreshOverviewAndAudit, status]);

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
      await refreshOverviewAndAudit();
    } catch (caught) {
      setError(describeApiError(caught));
      setErrorKind("refresh");
    } finally {
      setRestartLoading(false);
    }
  }, [merchantId, refreshOverviewAndAudit]);

  const refreshAll = useCallback(async () => {
    setError(null);
    setRefreshing(true);
    try {
      await refreshOverviewAndAudit();
    } catch (caught) {
      setError(describeApiError(caught));
      setErrorKind("refresh");
    } finally {
      setRefreshing(false);
    }
  }, [refreshOverviewAndAudit]);

  const metrics = overview.metrics;
  const weakest = [...overview.segment_metrics]
    .filter((s) => typeof s.conversion_rate === "number")
    .sort((a, b) => (a.conversion_rate ?? 1) - (b.conversion_rate ?? 1))[0];

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
        className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-slate-200 bg-slate-200 shadow-[0_10px_35px_rgba(15,23,42,0.05)] lg:grid-cols-4"
      >
        <div className="bg-gradient-to-br from-white to-indigo-50/35">
          <MetricCell
            label="Baseline Conversion"
            value={formatPercent(metrics.conversion_rate)}
            sub={`${formatInt(metrics.captured)} of ${formatInt(metrics.attempts)} captured`}
          />
        </div>
        <div className="bg-gradient-to-br from-white to-emerald-50/30">
          <MetricCell
            label="Captured GMV"
            value={formatInrPaise(overview.captured_gmv_paise)}
            sub={`of ${formatInrPaise(overview.attempted_gmv_paise)} attempted`}
          />
        </div>
        <div className="bg-gradient-to-br from-white to-amber-50/30">
          <MetricCell
            label="Weakest Segment"
            value={weakest ? formatPercent(weakest.conversion_rate) : "-"}
            sub={weakest ? weakest.segment : "No segment data"}
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

      <section
        aria-label="Autopilot trust chain"
        className="overflow-hidden rounded-xl border border-slate-200 bg-gradient-to-r from-slate-950 via-indigo-950 to-slate-950 px-4 py-3 shadow-[0_10px_30px_rgba(30,41,59,0.12)]"
      >
        <div className="flex flex-wrap items-center justify-center gap-x-3 gap-y-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-300 sm:justify-between">
          <span className="text-white">Evidence</span>
          <span className="text-indigo-400">→</span>
          <span>AI proposes</span>
          <span className="text-indigo-400">→</span>
          <span>Policy authorizes</span>
          <span className="text-indigo-400">→</span>
          <span>Razorpay executes</span>
          <span className="text-indigo-400">→</span>
          <span className="text-white">Statistics decides</span>
        </div>
      </section>

      <div className="grid gap-5 xl:grid-cols-5">
        <div className="xl:col-span-3">
          <SegmentConversionChart segments={overview.segment_metrics} />
        </div>
        <div className="xl:col-span-2">
          <PaymentMethodTable methods={overview.payment_method_metrics} />
        </div>
      </div>

      <RecentActivity events={audit} chainValid={overview.audit_chain_valid} />

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
