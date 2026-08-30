import { getCycle, getOverview } from "@/lib/api";
import { MERCHANT_ID } from "@/lib/constants";
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
  const [cycleResult, overviewResult] = await Promise.allSettled([
    getCycle(params.opportunityId),
    getOverview(MERCHANT_ID),
  ]);

  if (cycleResult.status === "rejected") {
    return (
      <div className="mx-auto max-w-2xl pt-10">
        <InlineError error={describeApiError(cycleResult.reason)} />
        <div className="mt-4">
          <RetryRefresh />
        </div>
      </div>
    );
  }

  const cycle: AutopilotCycle = cycleResult.value;
  const overview: MerchantOverview | null =
    overviewResult.status === "fulfilled" ? overviewResult.value : null;

  return <CycleView initialCycle={cycle} initialOverview={overview} />;
}
