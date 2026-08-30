"use client";

import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { formatUtcDateTime } from "@/lib/format";
import {
  AUDIT_FILTERS,
  auditEventSummary,
  eventLabel,
} from "@/lib/labels";
import type { AuditEvent } from "@/lib/types";
import { ActorBadge, IntegrityBadge } from "./badges";
import { CopyButton } from "./copy-button";

function dataEntries(data: Record<string, unknown>): [string, unknown][] {
  return Object.entries(data).filter(([, value]) => value != null);
}

function auditSummary(event: AuditEvent): string {
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

function DataValue({ value }: { value: unknown }) {
  if (typeof value === "object") {
    return (
      <code className="break-all font-mono text-[11.5px] text-gray-600">
        {JSON.stringify(value)}
      </code>
    );
  }
  if (typeof value === "boolean") {
    return (
      <code className="font-mono text-[11.5px] text-gray-600">
        {String(value)}
      </code>
    );
  }
  return (
    <code className="break-all font-mono text-[11.5px] text-gray-600">
      {String(value)}
    </code>
  );
}

/**
 * One tamper-evident event: collapsed one-liner, expanded structured data,
 * entity identity and the hash-chain links (monospace, copyable).
 */
export function AuditEventRow({ event }: { event: AuditEvent }) {
  const [open, setOpen] = useState(false);
  const entries = dataEntries(event.data);

  return (
    <li className="relative pl-6">
      <span
        aria-hidden
        className={`absolute left-[5px] top-4 h-2 w-2 rounded-full border ${
          open ? "border-indigo-500 bg-indigo-500" : "border-gray-300 bg-white"
        }`}
      />
      <span
        aria-hidden
        className="absolute left-[8.5px] top-7 bottom-0 w-px bg-gray-100"
      />
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-start gap-3 py-3 text-left"
      >
        <span className="w-[148px] shrink-0 pt-0.5 text-[12px] leading-snug text-gray-400 tabular-nums">
          {formatUtcDateTime(event.created_at)}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="text-[13.5px] font-medium text-gray-900">
              {eventLabel(event.event_type)}
            </span>
            <ActorBadge actor={event.actor} />
          </span>
          <span className="mt-0.5 block truncate text-[12.5px] text-gray-500">
            {auditSummary(event)}
          </span>
        </span>
        <ChevronDown
          size={15}
          aria-hidden
          className={`mt-1 shrink-0 text-gray-400 transition-transform ${
            open ? "rotate-180" : ""
          }`}
        />
      </button>

      {open && (
        <div className="mb-4 ml-[160px] rounded-md border border-gray-100 bg-slate-50/70 p-3.5">
          {entries.length > 0 && (
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-wider text-gray-400">
                Event data
              </p>
              <dl className="mt-1.5 space-y-1">
                {entries.map(([key, value]) => (
                  <div key={key} className="flex gap-3 text-[12.5px]">
                    <dt className="w-52 shrink-0 truncate text-gray-500">{key}</dt>
                    <dd className="min-w-0 flex-1">
                      <DataValue value={value} />
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          )}
          <div className="mt-3 space-y-1.5 border-t border-gray-200/70 pt-2.5 text-[12px]">
            <div className="flex items-center gap-2">
              <span className="w-24 shrink-0 text-gray-400">Entity</span>
              <span className="font-mono text-gray-600">
                {event.entity_type ?? "-"} · {event.entity_id ?? "-"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span className="w-24 shrink-0 text-gray-400">Event hash</span>
              <span className="min-w-0 truncate font-mono text-gray-600">
                {event.event_hash ?? "-"}
              </span>
              {event.event_hash && (
                <CopyButton value={event.event_hash} label="Copy event hash" />
              )}
            </div>
            <div className="flex items-center gap-2">
              <span className="w-24 shrink-0 text-gray-400">Prev hash</span>
              <span className="min-w-0 truncate font-mono text-gray-600">
                {event.prev_hash ?? "-"}
              </span>
              {event.prev_hash && (
                <CopyButton value={event.prev_hash} label="Copy previous hash" />
              )}
            </div>
          </div>
        </div>
      )}
    </li>
  );
}

/**
 * Hybrid timeline/list of lifecycle events with simple client-side actor
 * filters. The failure banner renders above the events - records are never
 * hidden when the chain does not verify.
 */
export function AuditTimeline({
  events,
  chainValid,
}: {
  events: AuditEvent[];
  chainValid: boolean | null;
}) {
  const [filterId, setFilterId] = useState("all");
  const filter = AUDIT_FILTERS.find((f) => f.id === filterId) ?? AUDIT_FILTERS[0];
  const filtered = filter.actors.length
    ? events.filter((event) => filter.actors.includes(event.actor))
    : events;

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        {AUDIT_FILTERS.map((option) => (
          <button
            key={option.id}
            type="button"
            onClick={() => setFilterId(option.id)}
            aria-pressed={filterId === option.id}
            className={`rounded-full border px-2.5 py-1 text-[12px] font-medium transition-colors ${
              filterId === option.id
                ? "border-indigo-200 bg-indigo-50 text-indigo-700"
                : "border-gray-200 bg-white text-gray-500 hover:bg-gray-50"
            }`}
          >
            {option.label}
          </button>
        ))}
        {chainValid != null && (
          <span className="ml-auto flex items-center gap-2">
            <span className="text-[11px] uppercase tracking-wider text-gray-400">
              Integrity
            </span>
            <IntegrityBadge valid={chainValid} />
          </span>
        )}
      </div>

      {chainValid === false && (
        <div
          role="alert"
          className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-[13.5px] font-medium text-red-800"
        >
          Audit verification failed. Event records may have been modified.
        </div>
      )}

      <ol className="mt-4 rounded-lg border border-gray-200 bg-white px-4 py-1">
        {filtered.map((event) => (
          <AuditEventRow key={event.id} event={event} />
        ))}
        {filtered.length === 0 && (
          <li className="px-2 py-6 text-center text-[13px] text-gray-400">
            No events for this filter.
          </li>
        )}
      </ol>
    </div>
  );
}
