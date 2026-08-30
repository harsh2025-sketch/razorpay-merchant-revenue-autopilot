"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatPercent } from "@/lib/format";
import type { SegmentMetrics } from "@/lib/types";

/**
 * CHART 1 of 2 - observed conversion per customer segment.
 * Horizontal bars, sorted descending; the weakest segment uses a stronger
 * (but still restrained) indigo. No trend lines, no animations.
 */
export function SegmentConversionChart({
  segments,
}: {
  segments: SegmentMetrics[];
}) {
  const data = [...segments]
    .filter((s) => typeof s.conversion_rate === "number")
    .sort((a, b) => (b.conversion_rate ?? 0) - (a.conversion_rate ?? 0));

  const weakest =
    data.length > 0 ? data.reduce((a, b) => (a.conversion_rate ?? 1) <= (b.conversion_rate ?? 1) ? a : b) : null;

  if (data.length === 0) {
    return (
      <p className="text-[13px] text-gray-400">
        No segment conversion data available.
      </p>
    );
  }

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-[16px] font-semibold text-gray-900">
            Segment Conversion
          </h2>
          <p className="mt-0.5 text-[13px] text-gray-500">
            Observed payment conversion by customer segment.
          </p>
        </div>
        <p className="shrink-0 text-[11px] uppercase tracking-wider text-gray-400">
          Observed
        </p>
      </div>
      <div className="mt-4" data-testid="segment-chart">
        <ResponsiveContainer width="100%" height={data.length * 40 + 30}>
          <BarChart
            data={data}
            layout="vertical"
            margin={{ top: 0, right: 52, left: 8, bottom: 0 }}
          >
            <CartesianGrid horizontal={false} stroke="#F1F5F9" />
            <XAxis
              type="number"
              domain={[0, 1]}
              ticks={[0, 0.25, 0.5, 0.75, 1]}
              tickFormatter={(value: number) => `${Math.round(value * 100)}%`}
              axisLine={{ stroke: "#E5E7EB" }}
              tickLine={false}
              tick={{ fill: "#94A3B8", fontSize: 11 }}
            />
            <YAxis
              type="category"
              dataKey="segment"
              width={112}
              axisLine={false}
              tickLine={false}
              tick={{ fill: "#475569", fontSize: 12 }}
            />
            <Tooltip
              cursor={{ fill: "rgba(99,102,241,0.05)" }}
              formatter={(value: number) => [formatPercent(value), "Conversion"]}
              contentStyle={{
                borderRadius: 8,
                border: "1px solid #E5E7EB",
                fontSize: 12,
                padding: "6px 10px",
              }}
            />
            <Bar dataKey="conversion_rate" barSize={16} radius={[0, 3, 3, 0]} isAnimationActive={false}>
              {data.map((entry) => (
                <Cell
                  key={entry.segment}
                  fill={weakest != null && entry.segment === weakest.segment ? "#3730A3" : "#6366F1"}
                />
              ))}
              <LabelList
                dataKey="conversion_rate"
                position="right"
                formatter={(value: number) => formatPercent(value)}
                style={{ fill: "#334155", fontSize: 12, fontWeight: 500 }}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      {weakest && (
        <p className="mt-2 text-[12px] text-gray-400">
          Weakest segment shown darker:{" "}
          <span className="font-medium text-gray-600">{weakest.segment}</span> at{" "}
          {formatPercent(weakest.conversion_rate)} conversion.
        </p>
      )}
    </section>
  );
}
