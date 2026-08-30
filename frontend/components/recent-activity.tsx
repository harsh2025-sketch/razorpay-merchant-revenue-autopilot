import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { formatUtcShort } from "@/lib/format";
import { auditEventSummary, eventLabel } from "@/lib/labels";
import type { AuditEvent } from "@/lib/types";
import { ActorBadge, IntegrityBadge } from "./badges";

function activitySummary(event: AuditEvent): string {
  if (event.event_type === "RAZORPAY_RESOURCE_CREATED") {
    const id = typeof event.data.razorpay_id === "string" ? event.data.razorpay_id : "";
    const type = typeof event.data.resource_type === "string" ? event.data.resource_type : "resource";
    if (id.startsWith("demo_")) {
      return `${type} ${id} created in hosted demo mode (simulated)`;
    }
  }
  if (event.event_type === "RAZORPAY_RESOURCE_CANCELLED") {
    const id = typeof event.data.razorpay_id === "string" ? event.data.razorpay_id : "";
    if (id.startsWith("demo_")) {
      return `Simulated resource ${id} cancelled in hosted demo mode`;
    }
  }
  return auditEventSummary(event.event_type, event.data);
}

/**
 * Compact lifecycle activity list - used for Overview recent activity and the
 * per-cycle activity section. No hashes here; the Audit Log owns those.
 */
export function RecentActivity({
  events,
  chainValid,
  limit = 5,
  title = "Recent Activity",
  href = "/audit",
  linkLabel = "View Audit Log",
}: {
  events: AuditEvent[];
  chainValid: boolean | null;
  limit?: number;
  title?: string;
  href?: string;
  linkLabel?: string;
}) {
  const shown = events.slice(0, limit);
  return (
    <section className="rounded-lg border border-gray-200 bg-white">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 px-5 py-3">
        <h2 className="text-[16px] font-semibold text-gray-900">{title}</h2>
        <div className="flex items-center gap-3">
          {chainValid != null && (
            <div className="flex items-center gap-1.5">
              <span className="text-[11px] uppercase tracking-wider text-gray-400">
                Audit integrity
              </span>
              <IntegrityBadge valid={chainValid} />
            </div>
          )}
          <Link
            href={href}
            className="inline-flex items-center gap-1 text-[12.5px] font-medium text-indigo-600 hover:text-indigo-700"
          >
            {linkLabel}
            <ArrowRight size={12} aria-hidden />
          </Link>
        </div>
      </div>
      <ul className="divide-y divide-gray-50">
        {shown.map((event) => (
          <li
            key={event.id}
            className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-5 py-2 text-[13px]"
          >
            <span className="w-[104px] shrink-0 text-[12px] text-gray-400 tabular-nums">
              {formatUtcShort(event.created_at)}
            </span>
            <span className="font-medium text-gray-800">
              {eventLabel(event.event_type)}
            </span>
            <ActorBadge actor={event.actor} />
            <span className="min-w-0 flex-1 basis-48 truncate text-gray-500">
              {activitySummary(event)}
            </span>
          </li>
        ))}
        {shown.length === 0 && (
          <li className="px-5 py-4 text-[13px] text-gray-400">
            No lifecycle events recorded yet.
          </li>
        )}
      </ul>
    </section>
  );
}
