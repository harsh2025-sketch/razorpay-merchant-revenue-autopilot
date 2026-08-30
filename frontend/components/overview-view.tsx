"use client";

import { useCallback, useState } from "react";
import {
  advanceAutopilot,
  getDetectionReadiness,
  getMerchantAudit,
  getOverview,
  startNewAutopilotCycle,
} from "@/lib/api";
import { RECENT_ACTIVITY_LIMIT } from "@/lib/constants";
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

/**
 * Overview client state container. Task 21B tracks detector readiness beside
 * the lifecycle read model so an exhausted historical revision becomes an
 * explicit wait-for-data product state rather than another Scan button.
 */
export function OverviewView({
  initialOverview,
  initialAudit,
  initialDetectionReady,
}: {
  initialOverview: MerchantOverview;
  initialAudit: AuditEvent[];
  initialDetectionReady: boolean;
}) {
  const merchantId = initialOverview.merchant.merchant_id;
  const [overview, setOverview] = useState(initialOverview);
  const [audit, setAudit] = useState(initialAudit);
  const [detectionReady, setDetectionReady] = useState(initialDetectionReady);
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

  const status = overview.autopilot_status;
  const waitingForData =
    status.next_action === "DETECT_OPPORTUNITIES" && !detectionReady;
  const effectiveNextAction = waitingForData ? null : status.next_action;

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
    let step: Awaited<ReturnType<typeof advanceAutopilot>>;
    try {
      step = await advanceAutopilot(merchantId);
    } catch (caught) {
      setStepMessage(null);
      setError(describeApiError(caught));
      setErrorKind("action");
      setActionLoading(false);
      return;
    }
    setRestartAvailable(step.step === "DEPLOYMENT_BLOCKED");
    setStepMessage(step.message);
    setViewCycleHref(
      step.entity_type === "opportunity" && step.entity_id
        ? `/autopilot/${step.entity_id}`
        : null,
    );
    try {
      await refreshOverviewAndAudit();
    } catch (caught) {
      setError(describeApiError(caught));
      setErrorKind("refresh");
    } finally {
      setActionLoading(false);
    }
  }, [merchantId, refreshOverviewAndAudit]);

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
    : lifecycleLabel(status.state, status.latest_experiment_status);
  const activeCycleSub = waitingForData
    ? "Append new transactions before another scan"
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
        loading={actionLoading}
        restartLoading={restartLoading}
        restartAvailable={restartAvailable}
        stepMessage={stepMessage}
        viewCycleHref={viewCycleHref}
        onAction={waitingForData ? undefined : handleAction}
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
        className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-gray-200 bg-gray-100 lg:grid-cols-4"
      >
        <div className="bg-white">
          <MetricCell
            label="Baseline Conversion"
            value={formatPercent(metrics.conversion_rate)}
            sub={`${formatInt(metrics.captured)} of ${formatInt(metrics.attempts)} captured`}
          />
        </div>
        <div className="bg-white">
          <MetricCell
            label="Captured GMV"
            value={formatInrPaise(overview.captured_gmv_paise)}
            sub={`of ${formatInrPaise(overview.attempted_gmv_paise)} attempted`}
          />
        </div>
        <div className="bg-white">
          <MetricCell
            label="Weakest Segment"
            value={weakest ? formatPercent(weakest.conversion_rate) : "-"}
            sub={weakest ? weakest.segment : "No segment data"}
          />
        </div>
        <div className="bg-white">
          <MetricCell
            label="Active Cycle"
            value={activeCycleValue}
            sub={activeCycleSub}
          />
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
      return "Running";
    case "EVALUATION_PENDING":
      return "Evaluating";
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
