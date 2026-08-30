"use client";

import { AlertTriangle } from "lucide-react";
import type { DescribedError } from "@/lib/errors";

/**
 * The one reusable inline error surface. Important errors are always inline
 * (never toasts); `tone="amber"` is used for blocked / ambiguous execution
 * states where the system intentionally refuses to act.
 */
export function InlineError({
  error,
  onRetry,
  retrying = false,
  className = "",
}: {
  error: DescribedError;
  onRetry?: () => void;
  retrying?: boolean;
  className?: string;
}) {
  const amber = error.tone === "amber";
  return (
    <div
      role="alert"
      className={`rounded-lg border px-4 py-3 ${
        amber
          ? "border-amber-200 bg-amber-50 text-amber-800"
          : "border-red-200 bg-red-50 text-red-800"
      } ${className}`}
    >
      <div className="flex items-start gap-2.5">
        <AlertTriangle
          size={15}
          aria-hidden
          className={`mt-0.5 shrink-0 ${amber ? "text-amber-500" : "text-red-500"}`}
        />
        <div className="min-w-0 flex-1">
          <p className="text-[13.5px] font-medium leading-snug">{error.title}</p>
          {error.detail && (
            <p className="mt-0.5 text-[12.5px] leading-snug opacity-80">
              {error.detail}
            </p>
          )}
          {error.code && (
            <p className="mt-1 font-mono text-[11px] opacity-60">{error.code}</p>
          )}
        </div>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            disabled={retrying}
            className={`shrink-0 rounded-md border px-2.5 py-1 text-[12.5px] font-medium transition-colors disabled:opacity-60 ${
              amber
                ? "border-amber-300 bg-white text-amber-800 hover:bg-amber-100"
                : "border-red-300 bg-white text-red-800 hover:bg-red-100"
            }`}
          >
            {retrying ? "Retrying…" : "Retry"}
          </button>
        )}
      </div>
    </div>
  );
}
