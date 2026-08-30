import { MerchantIntelligenceView } from "@/components/merchant-intelligence-view";
import { InlineError } from "@/components/inline-error";
import { PageHeader } from "@/components/page-header";
import { RetryRefresh } from "@/components/retry-refresh";
import { getMerchantIntelligence } from "@/lib/api";
import { MERCHANT_ID, MERCHANT_NAME } from "@/lib/constants";
import { describeApiError } from "@/lib/errors";

export const dynamic = "force-dynamic";

export default async function IntelligencePage() {
  try {
    const intelligence = await getMerchantIntelligence(MERCHANT_ID);
    return (
      <>
        <PageHeader
          title="Merchant Intelligence"
          subtitle={`${intelligence.merchant.name || MERCHANT_NAME} · Persisted optimization learning`}
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
          subtitle={`${MERCHANT_NAME} · Persisted optimization learning`}
        />
        <InlineError error={describeApiError(caught)} className="max-w-2xl" />
        <div className="mt-4">
          <RetryRefresh />
        </div>
      </>
    );
  }
}
