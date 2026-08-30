import { AlertTriangle, ExternalLink } from "lucide-react";
import { formatUtcDateTime, humanizeToken } from "@/lib/format";
import type { RazorpayResource } from "@/lib/types";
import { CopyButton } from "./copy-button";
import { StatusBadge } from "./badges";

const RESOURCE_LABELS: Record<string, string> = {
  payment_link: "Payment Link",
  offer: "Offer",
};

function resourceTypeLabel(type: string): string {
  return RESOURCE_LABELS[type] ?? humanizeToken(type);
}

/**
 * SECTION 5 - external execution proof point. Real Razorpay Test Mode resources
 * and explicit hosted-demo resources are rendered differently so the UI never
 * implies that a simulated object exists in the Razorpay dashboard.
 */
export function RazorpayResourcePanel({
  resource,
}: {
  resource: RazorpayResource;
}) {
  const active = resource.status === "active";
  // All hosted-demo external resources use the reserved demo_ namespace.
  // Keeping detection prefix-based also makes previously persisted demo rows
  // render truthfully after a production frontend redeploy.
  const simulated = resource.razorpay_id.startsWith("demo_");

  return (
    <section
      aria-label={simulated ? "Simulated payment resource" : "Razorpay test resource"}
      className="rounded-lg border border-indigo-200 bg-indigo-50/50 p-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-indigo-700">
          {simulated ? "Simulated Payment Resource" : "Razorpay Test Resource"}
        </p>
        <span className="text-[11px] font-medium uppercase tracking-wider text-indigo-500">
          {simulated ? "Hosted Demo · Simulated" : "Razorpay Test Mode"}
        </span>
      </div>

      <div className="mt-3.5 grid gap-x-8 gap-y-3 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <p className="text-[12px] font-medium text-gray-500">Resource</p>
          <p className="mt-0.5 inline-flex items-center gap-1.5 text-[14px] font-medium text-gray-900">
            {simulated ? "Simulated Payment Link" : resourceTypeLabel(resource.resource_type)}
            {!simulated ? (
              <ExternalLink size={12} aria-hidden className="text-indigo-400" />
            ) : null}
          </p>
        </div>
        <div className="min-w-0">
          <p className="text-[12px] font-medium text-gray-500">
            {simulated ? "Demo Resource ID" : "Razorpay ID"}
          </p>
          <div className="mt-0.5 flex items-center gap-2">
            <code className="truncate font-mono text-[13px] text-gray-800">
              {resource.razorpay_id}
            </code>
            <CopyButton
              value={resource.razorpay_id}
              label={simulated ? "Copy demo resource ID" : "Copy Razorpay ID"}
            />
          </div>
        </div>
        <div>
          <p className="text-[12px] font-medium text-gray-500">Status</p>
          <div className="mt-1">
            <StatusBadge tone={active ? "green" : "gray"}>{resource.status}</StatusBadge>
          </div>
        </div>
        <div>
          <p className="text-[12px] font-medium text-gray-500">Variant</p>
          <p className="mt-0.5 text-[13.5px] capitalize text-gray-800">
            {resource.variant ?? "-"}
          </p>
        </div>
      </div>

      <p className="mt-2.5 text-[12.5px] text-gray-400">
        Created {formatUtcDateTime(resource.created_at)} · through approved
        experiment execution
      </p>

      <p className="mt-4 border-t border-indigo-100 pt-3 text-[13px] leading-relaxed text-gray-700">
        {simulated ? (
          <>
            Hosted demo mode is active. No Razorpay API request was made and this
            demo resource does not exist in the Razorpay dashboard. Experimental
            customer traffic remains simulated separately.
          </>
        ) : (
          <>
            One real Razorpay Test Mode treatment resource is deployed. Experimental
            customer traffic is simulated separately.
          </>
        )}
      </p>
    </section>
  );
}

/**
 * Fail-closed deployment state (e.g. an unmapped semantic discount). No
 * workaround button exists - the system refuses to guess an Offer ID.
 */
export function DeploymentBlockedPanel({
  interventionLabel,
}: {
  interventionLabel: string;
}) {
  return (
    <section
      aria-label="Razorpay deployment blocked"
      className="rounded-lg border border-amber-200 bg-amber-50/60 p-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-amber-700">
          Razorpay Deployment
        </p>
        <span className="inline-flex items-center gap-1 rounded border border-amber-300 bg-amber-100 px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-wider text-amber-800">
          <AlertTriangle size={10} aria-hidden />
          Deployment Blocked
        </span>
      </div>
      <p className="mt-3 max-w-3xl text-[14px] font-medium leading-snug text-gray-900">
        Automated deployment is disabled until this semantic discount is mapped
        to a verified pre-created Razorpay Offer.
      </p>
      <p className="mt-1.5 text-[13px] text-gray-600">
        The system fails closed rather than guessing an Offer ID.
      </p>
      <p className="mt-2 text-[12px] text-gray-400">
        Blocked intervention: {interventionLabel}
      </p>
    </section>
  );
}
