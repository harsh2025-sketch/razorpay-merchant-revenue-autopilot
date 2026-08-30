import { getMerchantAudit, getOverview } from "@/lib/api";
import { getActiveMerchantId } from "@/lib/active-merchant";
import { DEFAULT_MERCHANT_NAME, RECENT_ACTIVITY_LIMIT } from "@/lib/constants";
import { describeApiError } from "@/lib/errors";
import { formatInt } from "@/lib/format";
import { OverviewView } from "@/components/overview-view";
import { PageHeader } from "@/components/page-header";
import { InlineError } from "@/components/inline-error";
import { RetryRefresh } from "@/components/retry-refresh";
import type { AuditEvent, MerchantOverview } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function OverviewPage() {
  const merchantId = getActiveMerchantId();
  const [overviewResult, auditResult] = await Promise.allSettled([
    getOverview(merchantId),
    getMerchantAudit(merchantId, RECENT_ACTIVITY_LIMIT),
  ]);

  if (overviewResult.status === "rejected") {
    return (
      <>
        <PageHeader title="Merchant Overview" subtitle="Revenue optimization console" />
        <InlineError
          error={describeApiError(overviewResult.reason)}
          onRetry={undefined}
          className="max-w-2xl"
        />
        <div className="mt-4 flex items-center gap-3">
          <RetryRefresh />
          <a
            href="/onboarding"
            className="rounded-md border border-gray-300 bg-white px-3 py-2 text-[13px] font-medium text-gray-700 hover:bg-gray-50"
          >
            Choose merchant data
          </a>
        </div>
      </>
    );
  }

  const overview: MerchantOverview = overviewResult.value;
  const audit: AuditEvent[] =
    auditResult.status === "fulfilled" ? auditResult.value : [];

  return (
    <>
      <PageHeader
        title={overview.merchant.name || DEFAULT_MERCHANT_NAME}
        subtitle="Revenue optimization · Historical payment analysis"
        meta={`${formatInt(overview.metrics.attempts)} payment attempts`}
      />
      <OverviewView initialOverview={overview} initialAudit={audit} />
    </>
  );
}
