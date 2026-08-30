import { getCycle, getOverview } from "@/lib/api";
import { describeApiError } from "@/lib/errors";
import { CycleView } from "@/components/cycle-view";
import { InlineError } from "@/components/inline-error";
import { RetryRefresh } from "@/components/retry-refresh";
import type { AutopilotCycle, MerchantOverview } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function CyclePage({
  params,
}: {
  params: { opportunityId: string };
}) {
  let cycle: AutopilotCycle;
  try {
    cycle = await getCycle(params.opportunityId);
  } catch (caught) {
    return (
      <div className="mx-auto max-w-2xl pt-10">
        <InlineError error={describeApiError(caught)} />
        <div className="mt-4">
          <RetryRefresh />
        </div>
      </div>
    );
  }

  const overview: MerchantOverview | null = await getOverview(
    cycle.opportunity.merchant_id,
  ).catch(() => null);

  return <CycleView initialCycle={cycle} initialOverview={overview} />;
}
