import { getMerchantAudit, getOverview } from "@/lib/api";
import { AUDIT_PAGE_LIMIT, MERCHANT_ID } from "@/lib/constants";
import { describeApiError } from "@/lib/errors";
import { AuditTimeline } from "@/components/audit-timeline";
import { IntegrityBadge } from "@/components/badges";
import { PageHeader } from "@/components/page-header";
import { InlineError } from "@/components/inline-error";
import { RetryRefresh } from "@/components/retry-refresh";
import type { AuditEvent } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function AuditPage() {
  const [auditResult, overviewResult] = await Promise.allSettled([
    getMerchantAudit(MERCHANT_ID, AUDIT_PAGE_LIMIT),
    getOverview(MERCHANT_ID),
  ]);

  if (auditResult.status === "rejected") {
    return (
      <>
        <PageHeader
          title="Audit Log"
          subtitle="Tamper-evident lifecycle history for Revenue Autopilot."
        />
        <InlineError error={describeApiError(auditResult.reason)} />
        <div className="mt-4">
          <RetryRefresh />
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
