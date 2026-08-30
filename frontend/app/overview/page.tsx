import { getMerchantAudit, getOverview } from "@/lib/api";
import {
  MERCHANT_ID,
  MERCHANT_NAME,
  RECENT_ACTIVITY_LIMIT,
} from "@/lib/constants";
import { describeApiError } from "@/lib/errors";
import { formatInt } from "@/lib/format";
import { OverviewView } from "@/components/overview-view";
import { PageHeader } from "@/components/page-header";
import { InlineError } from "@/components/inline-error";
import { RetryRefresh } from "@/components/retry-refresh";
import type { AuditEvent, MerchantOverview } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function OverviewPage() {
  const [overviewResult, auditResult] = await Promise.allSettled([
    getOverview(MERCHANT_ID),
    getMerchantAudit(MERCHANT_ID, RECENT_ACTIVITY_LIMIT),
  ]);

  if (overviewResult.status === "rejected") {
    return (
      <>
        <PageHeader title={MERCHANT_NAME} subtitle="Revenue optimization console" />
        <InlineError
          error={describeApiError(overviewResult.reason)}
          onRetry={undefined}
          className="max-w-2xl"
        />
        <div className="mt-4">
          <RetryRefresh />
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
        title={overview.merchant.name || MERCHANT_NAME}
        subtitle="Revenue optimization · Historical payment analysis"
        meta={`${formatInt(overview.metrics.attempts)} payment attempts`}
      />
      <OverviewView initialOverview={overview} initialAudit={audit} />
    </>
  );
}
