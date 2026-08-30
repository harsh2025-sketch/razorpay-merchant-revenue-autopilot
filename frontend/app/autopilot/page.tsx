import Link from "next/link";
import { getOpportunities, getOverview } from "@/lib/api";
import { MERCHANT_ID } from "@/lib/constants";
import { describeApiError } from "@/lib/errors";
import {
  AutopilotCycleList,
  AutopilotEmptyState,
} from "@/components/autopilot-cycle-list";
import { PageHeader } from "@/components/page-header";
import { InlineError } from "@/components/inline-error";
import { RetryRefresh } from "@/components/retry-refresh";
import type { MerchantOverview, Opportunity } from "@/lib/types";

export const dynamic = "force-dynamic";

function currentOpportunityId(
  opportunities: Opportunity[],
  overview: MerchantOverview | null,
): string | null {
  const active = opportunities.filter(
    (opportunity) =>
      opportunity.status === "detected" || opportunity.status === "investigating",
  );
  if (active.length === 0) return null;

  // Once a cycle has an experiment, that foreign key is the strongest
  // persisted signal of which opportunity the backend is driving. Ignore it
  // if the opportunity has already been resolved by a new-cycle rollover.
  const experimentOpportunityId = overview?.latest_experiment?.opportunity_id;
  if (
    experimentOpportunityId &&
    active.some((opportunity) => opportunity.id === experimentOpportunityId)
  ) {
    return experimentOpportunityId;
  }

  // Waiting opportunities have not reached planning yet. Mirror the backend's
  // deterministic active-opportunity ordering for that stage rather than using
  // "newest inserted", which can point at a lower-severity row from the same
  // detector pass.
  return [...active].sort((left, right) => {
    if (left.severity !== right.severity) return right.severity - left.severity;
    if (left.created_at !== right.created_at) {
      return right.created_at.localeCompare(left.created_at);
    }
    return left.id.localeCompare(right.id);
  })[0]?.id ?? null;
}

export default async function AutopilotPage() {
  const [opportunitiesResult, overviewResult] = await Promise.allSettled([
    getOpportunities(MERCHANT_ID),
    getOverview(MERCHANT_ID),
  ]);

  if (opportunitiesResult.status === "rejected") {
    return (
      <>
        <PageHeader
          title="Autopilot"
          subtitle="Revenue optimization cycles detected from merchant payment performance."
        />
        <InlineError error={describeApiError(opportunitiesResult.reason)} />
        <div className="mt-4">
          <RetryRefresh />
        </div>
      </>
    );
  }

  const opportunities: Opportunity[] = opportunitiesResult.value;
  const overview: MerchantOverview | null =
    overviewResult.status === "fulfilled" ? overviewResult.value : null;
  const currentId = currentOpportunityId(opportunities, overview);
  const currentOwnsLatestExperiment =
    currentId != null && overview?.latest_experiment?.opportunity_id === currentId;

  return (
    <>
      <PageHeader
        title="Autopilot"
        subtitle="Revenue optimization cycles detected from merchant payment performance."
        right={
          <Link
            href="/overview"
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-[13px] font-medium text-gray-700 hover:bg-gray-50"
          >
            Return to Overview
          </Link>
        }
      />
      {opportunities.length === 0 ? (
        <AutopilotEmptyState />
      ) : (
        <AutopilotCycleList
          opportunities={opportunities}
          latestOpportunityId={currentId}
          state={overview?.autopilot_status.state ?? null}
          decision={
            currentOwnsLatestExperiment
              ? overview?.autopilot_status.latest_statistical_decision ?? null
              : null
          }
        />
      )}
    </>
  );
}
