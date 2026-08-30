import Link from "next/link";
import {
  formatInrPaise,
  formatInt,
  formatPp,
  formatPValue,
  formatPercent,
  formatUtcShort,
  humanizeToken,
  shortId,
} from "@/lib/format";
import type { MerchantIntelligence } from "@/lib/types";

export function MerchantIntelligenceView({
  intelligence,
}: {
  intelligence: MerchantIntelligence;
}) {
  const { portfolio, champion, memory } = intelligence;

  return (
    <div className="space-y-5">
      <section
        aria-label="Merchant intelligence summary"
        className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-gray-200 bg-gray-100 lg:grid-cols-4"
      >
        <SummaryCell label="Current Champion" value={`v${champion.version}`} sub={`${formatInt(champion.promotion_count)} promoted treatment${champion.promotion_count === 1 ? "" : "s"}`} />
        <SummaryCell label="Terminal Trials" value={formatInt(memory.trial_count)} sub={`${formatInt(memory.completed_result_count)} statistical result${memory.completed_result_count === 1 ? "" : "s"}`} />
        <SummaryCell label="Kept Treatments" value={formatInt(memory.keep_count)} sub={`${formatInt(memory.rollback_count)} rollback · ${formatInt(memory.inconclusive_count)} inconclusive`} />
        <SummaryCell label="Policy Rejections" value={formatInt(memory.policy_rejection_count)} sub="Deterministic merchant guardrails" />
      </section>

      <section className="rounded-lg border border-gray-200 bg-white">
        <SectionHeader
          eyebrow="Next best action"
          title="Opportunity Portfolio"
          description="Task 19B ranks untouched detected opportunities using observed conversion gaps, captured order value, prior terminal trials, and merchant policy feasibility."
        />
        {portfolio.opportunities.length === 0 ? (
          <EmptyState text="No untouched active opportunity is currently available for portfolio ranking." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[920px] text-left text-[13px]">
              <thead className="border-y border-gray-100 bg-gray-50 text-[11px] uppercase tracking-wide text-gray-500">
                <tr>
                  <th className="px-5 py-2.5 font-semibold">Rank</th>
                  <th className="px-3 py-2.5 font-semibold">Segment</th>
                  <th className="px-3 py-2.5 font-semibold">Observed gap</th>
                  <th className="px-3 py-2.5 font-semibold">GMV sizing proxy</th>
                  <th className="px-3 py-2.5 font-semibold">Prior trials</th>
                  <th className="px-3 py-2.5 font-semibold">Untried allowed</th>
                  <th className="px-5 py-2.5 font-semibold">Priority</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {portfolio.opportunities.map((row) => {
                  const next = row.opportunity_id === portfolio.next_best_opportunity_id;
                  return (
                    <tr key={row.opportunity_id} className={next ? "bg-indigo-50/40" : ""}>
                      <td className="px-5 py-3 font-semibold text-gray-900">
                        #{row.rank}
                        {next && <span className="ml-2 rounded bg-indigo-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-indigo-700">Next</span>}
                      </td>
                      <td className="px-3 py-3">
                        <Link href={`/autopilot/${row.opportunity_id}`} className="font-medium text-gray-900 hover:text-indigo-700">
                          {row.segment}
                        </Link>
                        <div className="mt-0.5 text-[11px] text-gray-400">{shortId(row.opportunity_id)}</div>
                      </td>
                      <td className="px-3 py-3 font-medium text-gray-800">{formatPp(row.conversion_gap)}</td>
                      <td className="px-3 py-3 text-gray-700">{formatInrPaise(row.history_adjusted_gmv_proxy_paise)}</td>
                      <td className="px-3 py-3 text-gray-700">{formatInt(row.prior_terminal_trials)}</td>
                      <td className="px-3 py-3 text-gray-600">{row.untried_allowed_interventions.length ? row.untried_allowed_interventions.map(humanizeToken).join(", ") : "None"}</td>
                      <td className="px-5 py-3">
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 w-20 overflow-hidden rounded-full bg-gray-100">
                            <div className="h-full rounded-full bg-indigo-600" style={{ width: `${Math.max(0, Math.min(100, row.priority_index * 100))}%` }} />
                          </div>
                          <span className="text-[12px] font-medium text-gray-700">{formatPercent(row.priority_index, 0)}</span>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <div className="border-t border-gray-100 px-5 py-3 text-[11.5px] leading-5 text-gray-500">
          GMV sizing proxy is an observed opportunity-sizing heuristic from Task 19B, not a forecast, booked revenue, or causal claim.
        </div>
      </section>

      <section className="rounded-lg border border-gray-200 bg-white">
        <SectionHeader
          eyebrow="Champion–challenger"
          title={`Champion v${champion.version}`}
          description="Only statistically significant KEEP decisions promote a treatment. Future challengers use the promoted configuration as their control for the same intervention type."
        />
        {champion.configs.length === 0 ? (
          <EmptyState text="Baseline configuration remains the champion. No treatment has earned a KEEP promotion yet." />
        ) : (
          <div className="grid gap-3 p-5 md:grid-cols-2">
            {champion.configs.map((config) => (
              <article key={config.intervention_type} className="rounded-lg border border-gray-200 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">Promoted control</div>
                    <h3 className="mt-1 text-[14px] font-semibold text-gray-900">{humanizeToken(config.intervention_type)}</h3>
                  </div>
                  <span className="rounded bg-emerald-50 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-emerald-700">KEEP</span>
                </div>
                <div className="mt-3 flex flex-wrap gap-1.5">{configChips(config.config)}</div>
                <dl className="mt-4 grid grid-cols-3 gap-3 border-t border-gray-100 pt-3">
                  <Stat label="Lift" value={formatPp(config.absolute_lift)} />
                  <Stat label="p-value" value={formatPValue(config.p_value)} />
                  <Stat label="Promoted" value={formatUtcShort(config.promoted_at)} />
                </dl>
                <Link href={`/autopilot/${config.source_experiment_id}`} className="mt-3 block text-[11.5px] text-indigo-600 hover:text-indigo-800">
                  Source experiment {shortId(config.source_experiment_id)}
                </Link>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="rounded-lg border border-gray-200 bg-white">
        <SectionHeader
          eyebrow="Experiment memory"
          title="Learned Intervention History"
          description="Terminal experiments are grouped by segment and intervention. Active work is excluded until it reaches a safe terminal boundary."
        />
        {memory.knowledge.length === 0 ? (
          <EmptyState text="No terminal experiment history has been learned yet." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] text-left text-[13px]">
              <thead className="border-y border-gray-100 bg-gray-50 text-[11px] uppercase tracking-wide text-gray-500">
                <tr>
                  <th className="px-5 py-2.5 font-semibold">Segment</th>
                  <th className="px-3 py-2.5 font-semibold">Intervention</th>
                  <th className="px-3 py-2.5 font-semibold">Trials</th>
                  <th className="px-3 py-2.5 font-semibold">KEEP</th>
                  <th className="px-3 py-2.5 font-semibold">Rollback</th>
                  <th className="px-3 py-2.5 font-semibold">Inconclusive</th>
                  <th className="px-3 py-2.5 font-semibold">Rejected</th>
                  <th className="px-5 py-2.5 font-semibold">Latest observed lift</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {memory.knowledge.map((row) => (
                  <tr key={`${row.segment}:${row.intervention_type}`}>
                    <td className="px-5 py-3 font-medium text-gray-900">{row.segment}</td>
                    <td className="px-3 py-3 text-gray-700">{humanizeToken(row.intervention_type)}</td>
                    <td className="px-3 py-3 text-gray-700">{formatInt(row.trial_count)}</td>
                    <td className="px-3 py-3 text-emerald-700">{formatInt(row.keep_count)}</td>
                    <td className="px-3 py-3 text-rose-700">{formatInt(row.rollback_count)}</td>
                    <td className="px-3 py-3 text-amber-700">{formatInt(row.inconclusive_count)}</td>
                    <td className="px-3 py-3 text-gray-700">{formatInt(row.rejected_count)}</td>
                    <td className="px-5 py-3 font-medium text-gray-800">{formatPp(row.latest_absolute_lift)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="rounded-lg border border-gray-200 bg-white">
        <SectionHeader
          eyebrow="Persisted outcomes"
          title="Recent Terminal Trials"
          description="Newest first. These rows are read from persisted policy and statistical decisions; the LLM does not label experiment outcomes."
        />
        {memory.records.length === 0 ? (
          <EmptyState text="No terminal trial records are available." />
        ) : (
          <div className="divide-y divide-gray-100">
            {memory.records.slice(0, 8).map((record) => (
              <div key={record.experiment_id} className="grid gap-2 px-5 py-3.5 md:grid-cols-[1.4fr_1.3fr_1fr_1fr_auto] md:items-center">
                <div>
                  <div className="text-[13px] font-medium text-gray-900">{record.segment}</div>
                  <div className="text-[11px] text-gray-400">{shortId(record.experiment_id)} · {formatUtcShort(record.ended_at ?? record.created_at)}</div>
                </div>
                <div className="text-[12.5px] text-gray-700">{humanizeToken(record.intervention_type)}</div>
                <div className="text-[12px] text-gray-600">Policy {record.policy_decision ?? "-"}</div>
                <div className="text-[12px] font-medium text-gray-800">{record.statistical_decision ?? humanizeToken(record.terminal_reason)}</div>
                <div className="text-right text-[12px] font-semibold text-gray-800">{formatPp(record.absolute_lift)}</div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function SummaryCell({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="bg-white px-4 py-4">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">{label}</div>
      <div className="mt-1 text-[22px] font-semibold tracking-tight text-gray-900">{value}</div>
      <div className="mt-1 text-[11.5px] text-gray-500">{sub}</div>
    </div>
  );
}

function SectionHeader({ eyebrow, title, description }: { eyebrow: string; title: string; description: string }) {
  return (
    <div className="px-5 py-4">
      <div className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-indigo-600">{eyebrow}</div>
      <h2 className="mt-1 text-[16px] font-semibold tracking-tight text-gray-900">{title}</h2>
      <p className="mt-1 max-w-4xl text-[12.5px] leading-5 text-gray-500">{description}</p>
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return <div className="border-t border-gray-100 px-5 py-8 text-[13px] text-gray-500">{text}</div>;
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-wide text-gray-400">{label}</dt>
      <dd className="mt-1 text-[12px] font-medium text-gray-800">{value}</dd>
    </div>
  );
}

function configChips(config: Record<string, unknown>) {
  const entries = Object.entries(config);
  if (entries.length === 0) return <span className="text-[12px] text-gray-400">Baseline config</span>;
  return entries.map(([key, value]) => (
    <span key={key} className="rounded border border-gray-200 bg-gray-50 px-2 py-1 text-[11px] text-gray-700">
      {humanizeToken(key)}: {configValue(key, value)}
    </span>
  ));
}

function configValue(key: string, value: unknown): string {
  if (typeof value === "boolean") return value ? "Enabled" : "Disabled";
  if (typeof value === "number") {
    if (key.endsWith("_pct")) return formatPercent(value, 0);
    if (key.endsWith("_hours")) return `${value}h`;
    return String(value);
  }
  if (value == null) return "None";
  return String(value);
}
