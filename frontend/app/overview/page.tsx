import Link from "next/link";
import { getOverview } from "@/lib/api";
import { getActiveMerchantId } from "@/lib/active-merchant";
import { DEFAULT_MERCHANT_NAME } from "@/lib/constants";
import { describeApiError } from "@/lib/errors";
import { formatInt } from "@/lib/format";
import { OverviewView } from "@/components/overview-view";
import { PageHeader } from "@/components/page-header";
import { InlineError } from "@/components/inline-error";
import { RetryRefresh } from "@/components/retry-refresh";
import type { MerchantOverview } from "@/lib/types";

/**
 * Render the critical Overview read first. Merchant selection keeps this route
 * request-aware through cookies, while the read itself may use the short
 * server cache configured in lib/api.ts. Client-side mutations always refresh
 * with uncached browser requests.
 */
export default async function OverviewPage() {
  const merchantId = getActiveMerchantId();
  let overview: MerchantOverview;

  try {
    overview = await getOverview(merchantId);
  } catch (reason) {
    return (
      <>
        <PageHeader title="Merchant Overview" subtitle="Revenue optimization console" />
        <InlineError
          error={describeApiError(reason)}
          onRetry={undefined}
          className="max-w-2xl"
        />
        <div className="mt-4 flex items-center gap-3">
          <RetryRefresh />
          <Link
            href="/onboarding"
            className="rounded-md border border-gray-300 bg-white px-3 py-2 text-[13px] font-medium text-gray-700 hover:bg-gray-50"
          >
            Choose merchant data
          </Link>
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={overview.merchant.name || DEFAULT_MERCHANT_NAME}
        subtitle="Revenue optimization · Historical payment analysis"
        meta={`${formatInt(overview.metrics.attempts)} payment attempts`}
      />
      <OverviewView initialOverview={overview} />
    </>
  );
}
