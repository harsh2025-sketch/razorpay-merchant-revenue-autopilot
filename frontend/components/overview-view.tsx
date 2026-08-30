"use client";

import { useCallback, useState } from "react";
import {
  advanceAutopilot,
  getMerchantAudit,
  getOverview,
  startNewAutopilotCycle,
} from "@/lib/api";
import { MERCHANT_ID, RECENT_ACTIVITY_LIMIT } from "@/lib/constants";
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
 * Overview client state container: holds the fetched overview + recent
 * audit events, performs exactly one Autopilot step per click, then refetches.
 * Terminal cycles can be explicitly rolled forward without deleting their
 * historical opportunity, experiment, result, resource, or audit records.
 */
export function OverviewView({
  initialOverview,
  initialAudit,
}: {
  initialOverview: MerchantOverview;
  initialAudit: AuditEvent[];
}) {
  const [overview, setOverview] = useState(initialOverview);
  const [audit, setAudit] = useState(initialAudit);
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

  const refreshOverviewAndAudit = useCallback(async () => {
    const [freshOverview, freshAudit] = await Promise.all([
      getOverview(MERCHANT_ID),
      getMerchantAudit(MERCHANT_ID, RECENT_ACTIVITY_LIMIT),
    ]);
    setOverview(freshOverview);
    setAudit(freshAudit);
    return freshOverview;
  }, []);

  const handleAction = useCallback(async () => {
    setError(null);
    setActionLoading(true);
    let step: Awaited<ReturnType<typeof advanceAutopilot>>;
    try {
      // Exactly one lifecycle transition per user action.
      step = await advanceAutopilot(MERCHANT_ID);
    } catch (caught) {
      setStepMessage(null);
      setError(describeApiError(caught));
      setErrorKind("action");
      setActionLoading(false);
      return;
    }
    // A deployment-blocked response is intentionally visible even though the
    // persisted approved/no-resource state resolves back to DEPLOY after the
    // refresh. Keep the explicit abandon/start-new-cycle option until the user
    // either retries a normal step or rolls the cycle forward.
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
      // The step succeeded; only the reads are stale. Retry must refresh,
      // never advance the lifecycle a second time.
      setError(describeApiError(caught));
      setErrorKind("refresh");
    } finally {
      setActionLoading(false);
    }
  }, [refreshOverviewAndAudit]);

  const handleStartNewCycle = useCallback(async () => {
    setError(null);
    setRestartLoading(true);
    let nextOpportunity: Awaited<ReturnType<typeof startNewAutopilotCycle>>;
    try {
      nextOpportunity = await startNewAutopilotCycle(MERCHANT_ID);
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
        "Previous cycle closed. No current opportunity meets the detector threshold.",
      );
      setViewCycleHref(null);
    }

    try {
      await refreshOverviewAndAudit();
    } catch (caught) {
      // Rollover already succeeded. Never call it a second time merely because
      // a follow-up GET failed; the retry path below performs reads only.
      setError(describeApiError(caught));
      setErrorKind("refresh");
    } finally {
      setRestartLoading(false);
    }
  }, [refreshOverviewAndAudit]);

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

  const activeCycleValue = lifecycleLabel(status.state, status.latest_experiment_status);
  const activeCycleSub =
    status.state === "COMPLETED" && status.latest_statistical_decision != null
      ? `Decision ${status.latest_statistical_decision}`
      : `${formatInt(status.active_experiment_count)} active experiment${
          status.active_experiment_count === 1 ? "" : "s"
        }`;

  return (
    <div className="space-y-5">
      <AutopilotStatus
        state={status.state}
        nextAction={status.next_action}
        latestDecision={status.latest_statistical_decision}
        loading={actionLoading}
        restartLoading={restartLoading}
        restartAvailable={restartAvailable}
        stepMessage={stepMessage}
        viewCycleHref={viewCycleHref}
        onAction={handleAction}
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

      {/* Metric strip */}
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

      {/* Chart 1 + payment method table */}
      <div className="grid gap-5 xl:grid-cols-5">
        <div className="xl:col-span-3">
          <SegmentConversionChart segments={overview.segment_metrics} />
        </div>
        <div className="xl:col-span-2">
          <PaymentMethodTable methods={overview.payment_method_metrics} />
        </div>
      </div>

      <RecentActivity events={audit} chainValid={overview.audit_chain_valid} />

      {/* Quiet manual refresh - never blocks the page */}
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
