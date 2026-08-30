import { DataUpdateView } from "@/components/data-update-view";
import { InlineError } from "@/components/inline-error";
import { PageHeader } from "@/components/page-header";
import { RetryRefresh } from "@/components/retry-refresh";
import { getActiveMerchantId } from "@/lib/active-merchant";
import { getMerchant, getOnboardingDataStatus } from "@/lib/api";
import { describeApiError } from "@/lib/errors";

export const dynamic = "force-dynamic";

export default async function DataPage() {
  const merchantId = getActiveMerchantId();
  const [merchantResult, statusResult] = await Promise.allSettled([
    getMerchant(merchantId),
    getOnboardingDataStatus(merchantId),
  ]);

  if (merchantResult.status === "rejected" || statusResult.status === "rejected") {
    const reason =
      merchantResult.status === "rejected"
        ? merchantResult.reason
        : statusResult.status === "rejected"
          ? statusResult.reason
          : null;
    return (
      <>
        <PageHeader
          title="Data"
          subtitle="Append new merchant evidence without replaying historical transactions."
        />
        <InlineError error={describeApiError(reason)} className="max-w-2xl" />
        <div className="mt-4">
          <RetryRefresh />
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Data"
        subtitle={`${merchantResult.value.name} · Incremental evidence`}
        meta={statusResult.value.data_source === "demo" ? "Demo data" : "Merchant data"}
      />
      <DataUpdateView
        merchant={merchantResult.value}
        initialStatus={statusResult.value}
      />
    </>
  );
}
