import { MerchantIntelligenceView } from "@/components/merchant-intelligence-view";
import { InlineError } from "@/components/inline-error";
import { PageHeader } from "@/components/page-header";
import { RetryRefresh } from "@/components/retry-refresh";
import { getActiveMerchantId } from "@/lib/active-merchant";
import { getMerchantIntelligence } from "@/lib/api";
import { describeApiError } from "@/lib/errors";

export const dynamic = "force-dynamic";

export default async function IntelligencePage() {
  const merchantId = getActiveMerchantId();
  try {
    const intelligence = await getMerchantIntelligence(merchantId);
    return (
      <>
        <PageHeader
          title="Merchant Intelligence"
          subtitle={`${intelligence.merchant.name} · Persisted optimization learning`}
          meta={`Champion v${intelligence.champion.version}`}
        />
        <MerchantIntelligenceView intelligence={intelligence} />
      </>
    );
  } catch (caught) {
    return (
      <>
        <PageHeader
          title="Merchant Intelligence"
          subtitle="Persisted optimization learning"
        />
        <InlineError error={describeApiError(caught)} className="max-w-2xl" />
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
}
