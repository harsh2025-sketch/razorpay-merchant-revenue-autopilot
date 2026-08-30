import { getActiveMerchantId } from "@/lib/active-merchant";
import { getMerchantAudit, getOverview } from "@/lib/api";
import { AUDIT_PAGE_LIMIT } from "@/lib/constants";
import { describeApiError } from "@/lib/errors";
import { AuditTimeline } from "@/components/audit-timeline";
import { IntegrityBadge } from "@/components/badges";
import { PageHeader } from "@/components/page-header";
import { InlineError } from "@/components/inline-error";
import { RetryRefresh } from "@/components/retry-refresh";
import type { AuditEvent } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function AuditPage() {
  const merchantId = getActiveMerchantId();
  const [auditResult, overviewResult] = await Promise.allSettled([
    getMerchantAudit(merchantId, AUDIT_PAGE_LIMIT),
    getOverview(merchantId),
  ]);

  if (auditResult.status === "rejected") {
    return (
      <>
        <PageHeader
          title="Audit Log"
          subtitle="Tamper-evident lifecycle history for Revenue Autopilot."
        />
        <InlineError error={describeApiError(auditResult.reason)} />
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

  const events: AuditEvent[] = auditResult.value;
  const chainValid =
    overviewResult.status === "fulfilled"
      ? overviewResult.value.audit_chain_valid
      : null;

  return (
    <>
      <PageHeader
        title="Audit Log"
        subtitle="Tamper-evident lifecycle history for Revenue Autopilot."
        right={
          chainValid != null ? (
            <div className="flex items-center gap-2">
              <span className="text-[11px] uppercase tracking-wider text-gray-400">
                Integrity
              </span>
              <IntegrityBadge valid={chainValid} />
            </div>
          ) : null
        }
      />
      <AuditTimeline events={events} chainValid={chainValid} />
    </>
  );
}
