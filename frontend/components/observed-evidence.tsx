import {
  formatInt,
  formatPercent,
  humanizeToken,
  paymentMethodLabel,
} from "@/lib/format";
import { parseEvidence } from "@/lib/evidence";
import type { Opportunity } from "@/lib/types";
import { MetricCell } from "./metric-cell";

function KVRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <span className="text-[12.5px] text-gray-500">{label}</span>
      <span className="text-[13px] font-medium text-gray-800 tabular-nums">
        {value}
      </span>
    </div>
  );
}

/**
 * SECTION 1 - deterministic observable data above the AI trust boundary.
 * Everything in this card comes from the detector's persisted evidence.
 */
export function ObservedEvidence({ opportunity }: { opportunity: Opportunity }) {
  const parsed = parseEvidence(opportunity.evidence);
  const detected = opportunity.detected_value;
  const baseline = opportunity.baseline_value;
  const gap =
    detected != null && baseline != null ? detected - baseline : null;
  const hasCohorts =
    parsed.segmentCohort.attempts != null ||
    parsed.comparisonCohort.attempts != null;

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-gray-500">
          Observed Evidence
        </p>
        <p className="text-[12px] text-gray-500">
          Detector · Deterministic analysis
        </p>
      </div>

      {/* Primary finding */}
      <div className="mt-4 grid grid-cols-1 gap-px overflow-hidden rounded-md border border-gray-100 bg-gray-100 sm:grid-cols-3">
        <div className="bg-slate-50/70">
          <MetricCell
            label="Segment Conversion"
            value={formatPercent(detected)}
            sub={parsed.segment ?? opportunity.segment ?? undefined}
          />
        </div>
        <div className="bg-slate-50/70">
          <MetricCell
            label="Comparison Conversion"
            value={formatPercent(baseline)}
            sub="All other segments"
          />
        </div>
        <div className="bg-slate-50/70">
          <MetricCell
            label="Absolute Gap"
            value={
              <span className={gap != null && gap < 0 ? "text-gray-900" : ""}>
                {gap != null
                  ? `${gap > 0 ? "+" : gap < 0 ? "−" : ""}${Math.abs(gap * 100).toFixed(1)}pp`
                  : "-"}
              </span>
            }
            sub="Segment against comparison cohort"
          />
        </div>
      </div>

      {/* Readable evidence details */}
      <div className="mt-5 grid gap-5 lg:grid-cols-2">
        {hasCohorts && (
          <div>
            <h3 className="text-[12px] font-semibold uppercase tracking-wider text-gray-400">
              Cohorts
            </h3>
            <div className="mt-2 rounded-md border border-gray-100 px-3.5 py-1.5">
              <KVRow
                label={`${parsed.segment ?? opportunity.segment ?? "Segment"} attempts`}
                value={formatInt(parsed.segmentCohort.attempts)}
              />
              <KVRow
                label={`${parsed.segment ?? opportunity.segment ?? "Segment"} captured`}
                value={formatInt(parsed.segmentCohort.captured)}
              />
              <KVRow
                label="Segment conversion rate"
                value={formatPercent(parsed.segmentCohort.conversionRate)}
              />
              <div className="my-1 border-t border-gray-100" />
              <KVRow
                label="Comparison cohort attempts"
                value={formatInt(parsed.comparisonCohort.attempts)}
              />
              <KVRow
                label="Comparison cohort captured"
                value={formatInt(parsed.comparisonCohort.captured)}
              />
              <KVRow
                label="Comparison conversion rate"
                value={formatPercent(parsed.comparisonCohort.conversionRate)}
              />
            </div>
          </div>
        )}

        {parsed.failureReasons.length > 0 && (
          <div>
            <h3 className="text-[12px] font-semibold uppercase tracking-wider text-gray-400">
              Failure Reasons
            </h3>
            <div className="mt-2 rounded-md border border-gray-100 px-3.5 py-1.5">
              {parsed.failureReasons.map((reason) => (
                <KVRow
                  key={reason.reason}
                  label={humanizeToken(reason.reason)}
                  value={formatInt(reason.count)}
                />
              ))}
            </div>
          </div>
        )}
      </div>

      {parsed.paymentMethods.length > 0 && (
        <div className="mt-5">
          <h3 className="text-[12px] font-semibold uppercase tracking-wider text-gray-400">
            Payment Methods · Segment
          </h3>
          <div className="mt-2 overflow-x-auto rounded-md border border-gray-100">
            <table className="w-full min-w-[420px] text-[12.5px]">
              <thead>
                <tr className="border-b border-gray-100 text-left text-[11px] uppercase tracking-wider text-gray-400">
                  <th className="px-3.5 py-2 font-medium">Method</th>
                  <th className="px-2 py-2 text-right font-medium">Attempts</th>
                  <th className="px-2 py-2 text-right font-medium">Captured</th>
                  <th className="px-2 py-2 text-right font-medium">Failed</th>
                  <th className="px-2 py-2 text-right font-medium">Abandoned</th>
                  <th className="px-3.5 py-2 text-right font-medium">Success</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {parsed.paymentMethods.map((method) => (
                  <tr key={method.method}>
                    <td className="px-3.5 py-1.5 font-medium text-gray-800">
                      {paymentMethodLabel(method.method)}
                    </td>
                    <td className="px-2 py-1.5 text-right text-gray-600 tabular-nums">
                      {formatInt(method.attempts)}
                    </td>
                    <td className="px-2 py-1.5 text-right text-gray-600 tabular-nums">
                      {formatInt(method.captured)}
                    </td>
                    <td className="px-2 py-1.5 text-right text-gray-600 tabular-nums">
                      {formatInt(method.failed)}
                    </td>
                    <td className="px-2 py-1.5 text-right text-gray-600 tabular-nums">
                      {formatInt(method.abandoned)}
                    </td>
                    <td className="px-3.5 py-1.5 text-right font-medium text-gray-900 tabular-nums">
                      {formatPercent(method.successRate)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {parsed.additional.length > 0 && (
        <details className="mt-4">
          <summary className="cursor-pointer text-[12.5px] font-medium text-gray-500 hover:text-gray-700">
            Additional evidence
          </summary>
          <div className="mt-2 rounded-md border border-gray-100 px-3.5 py-1.5">
            {parsed.additional.map((entry) => (
              <KVRow key={entry.key} label={entry.key} value={entry.value} />
            ))}
          </div>
        </details>
      )}
    </section>
  );
}
