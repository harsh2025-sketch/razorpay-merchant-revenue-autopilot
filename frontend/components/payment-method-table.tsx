import { formatInt, formatPercent, paymentMethodLabel } from "@/lib/format";
import type { PaymentMethodMetrics } from "@/lib/types";

/**
 * Compact observed payment-method table (deliberately NOT a chart).
 */
export function PaymentMethodTable({
  methods,
}: {
  methods: PaymentMethodMetrics[];
}) {
  return (
    <section className="rounded-lg border border-gray-200 bg-white p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-[16px] font-semibold text-gray-900">
            Payment Method Performance
          </h2>
          <p className="mt-0.5 text-[13px] text-gray-500">
            Historical attempts by payment method.
          </p>
        </div>
        <p className="shrink-0 text-[11px] uppercase tracking-wider text-gray-400">
          Observed
        </p>
      </div>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[380px] text-[13px]">
          <thead>
            <tr className="border-b border-gray-100 text-left text-[11px] uppercase tracking-wider text-gray-400">
              <th className="py-2 pr-3 font-medium">Method</th>
              <th className="px-2 py-2 text-right font-medium">Attempts</th>
              <th className="px-2 py-2 text-right font-medium">Captured</th>
              <th className="px-2 py-2 text-right font-medium">Failed</th>
              <th className="px-2 py-2 text-right font-medium">Aband.</th>
              <th className="py-2 pl-2 text-right font-medium">Success Rate</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {methods.map((method) => (
              <tr key={method.payment_method}>
                <td className="py-2 pr-3 font-medium text-gray-800">
                  {paymentMethodLabel(method.payment_method)}
                </td>
                <td className="px-2 py-2 text-right text-gray-600 tabular-nums">
                  {formatInt(method.attempts)}
                </td>
                <td className="px-2 py-2 text-right text-gray-600 tabular-nums">
                  {formatInt(method.captured)}
                </td>
                <td className="px-2 py-2 text-right text-gray-600 tabular-nums">
                  {formatInt(method.failed)}
                </td>
                <td className="px-2 py-2 text-right text-gray-600 tabular-nums">
                  {formatInt(method.abandoned)}
                </td>
                <td className="py-2 pl-2 text-right font-medium text-gray-900 tabular-nums">
                  {formatPercent(method.success_rate)}
                </td>
              </tr>
            ))}
            {methods.length === 0 && (
              <tr>
                <td colSpan={6} className="py-3 text-[13px] text-gray-400">
                  No payment method data available.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
