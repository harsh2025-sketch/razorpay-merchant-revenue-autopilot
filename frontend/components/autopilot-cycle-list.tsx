import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { formatPercent, formatPp, formatUtcDateTime } from "@/lib/format";
import type {
  AutopilotState,
  Opportunity,
  StatisticalDecision,
} from "@/lib/types";
import { StatusBadge, type BadgeTone } from "./badges";

function opportunityStage(
  opportunity: Opportunity,
  isLatest: boolean,
  state: AutopilotState | null,
  decision: StatisticalDecision | null,
): { label: string; tone: BadgeTone } {
  if (isLatest && state) {
    switch (state) {
      case "IDLE":
        return { label: "Detected", tone: "gray" };
      case "HYPOTHESIS_PENDING":
        return { label: "Awaiting AI diagnosis", tone: "gray" };
      case "EXPERIMENT_PENDING":
        return { label: "Awaiting plan", tone: "gray" };
      case "POLICY_REVIEW_PENDING":
        return { label: "Policy review", tone: "amber" };
      case "DEPLOYMENT_PENDING":
        return { label: "Ready to deploy", tone: "blue" };
      case "DEPLOYMENT_BLOCKED":
        return { label: "Deployment blocked", tone: "amber" };
      case "POLICY_REJECTED":
        return { label: "Rejected", tone: "red" };
      case "RUNNING":
        return { label: "Running", tone: "blue" };
      case "EVALUATION_PENDING":
        return { label: "Evaluating", tone: "blue" };
      case "COMPLETED":
        if (decision === "KEEP") return { label: "Completed · KEEP", tone: "green" };
        if (decision === "ROLLBACK") return { label: "Completed · ROLLBACK", tone: "red" };
        if (decision === "INCONCLUSIVE")
          return { label: "Completed · Inconclusive", tone: "amber" };
        return { label: "Completed", tone: "gray" };
    }
  }
  switch (opportunity.status) {
    case "detected":
      return { label: "Detected", tone: "gray" };
    case "investigating":
      return { label: "Active", tone: "blue" };
    default:
      return { label: "Completed", tone: "gray" };
  }
}

function focusBadge(state: AutopilotState | null): string {
  if (
    state === "COMPLETED" ||
    state === "POLICY_REJECTED" ||
    state === "DEPLOYMENT_BLOCKED"
  ) {
    return "Latest cycle";
  }
  return "Current cycle";
}

/**
 * One row per persisted opportunity (= one lifecycle cycle). Restrained
 * bordered rows, no kanban, no pipeline columns.
 */
export function AutopilotCycleList({
  opportunities,
  latestOpportunityId,
  state,
  decision,
}: {
  opportunities: Opportunity[];
  latestOpportunityId: string | null;
  state: AutopilotState | null;
  decision: StatisticalDecision | null;
}) {
  return (
    <section
      aria-label="Optimization cycles"
      className="divide-y divide-gray-100 rounded-lg border border-gray-200 bg-white"
    >
      {opportunities.map((opportunity) => {
        const isLatest = opportunity.id === latestOpportunityId;
        const stage = opportunityStage(opportunity, isLatest, state, decision);
        const gap =
          opportunity.detected_value != null &&
          opportunity.baseline_value != null
            ? opportunity.detected_value - opportunity.baseline_value
            : null;
        return (
          <div
            key={opportunity.id}
            className="flex flex-col gap-2 px-5 py-4 md:flex-row md:items-center md:gap-6"
          >
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[14px] font-semibold text-gray-900">
                  {opportunity.segment ?? "Unknown segment"} segment
                </span>
                {isLatest && <StatusBadge tone="indigo">{focusBadge(state)}</StatusBadge>}
                <StatusBadge tone={stage.tone}>{stage.label}</StatusBadge>
              </div>
              <p className="mt-1 text-[12.5px] text-gray-500">
                Conversion {formatPercent(opportunity.detected_value)} vs
                comparison {formatPercent(opportunity.baseline_value)}
                {gap != null && (
                  <>
                    {" "}
                    · gap{" "}
                    <span className="font-medium text-gray-700">
                      {formatPp(gap)}
                    </span>
                  </>
                )}{" "}
                · severity {opportunity.severity.toFixed(2)}
              </p>
            </div>
            <span className="shrink-0 text-[12px] text-gray-400 tabular-nums md:text-right">
              {formatUtcDateTime(opportunity.created_at)}
            </span>
            <Link
              href={`/autopilot/${opportunity.id}`}
              className="inline-flex shrink-0 items-center gap-1 text-[13px] font-medium text-indigo-600 hover:text-indigo-700"
            >
              View cycle
              <ArrowRight size={13} aria-hidden />
            </Link>
          </div>
        );
      })}
    </section>
  );
}

/** Intentional empty state - no illustrations, no fake data. */
export function AutopilotEmptyState() {
  return (
    <section className="rounded-lg border border-gray-200 bg-white px-5 py-10 text-center">
      <p className="text-[15px] font-medium text-gray-900">
        No optimization cycles yet.
      </p>
      <p className="mx-auto mt-1 max-w-md text-[13px] text-gray-500">
        Run detection from Overview to identify a measurable conversion
        opportunity.
      </p>
      <div className="mt-5">
        <Link
          href="/overview"
          className="inline-flex items-center rounded-md border border-gray-300 bg-white px-3.5 py-2 text-[13px] font-medium text-gray-700 hover:bg-gray-50"
        >
          Go to Overview
        </Link>
      </div>
    </section>
  );
}
