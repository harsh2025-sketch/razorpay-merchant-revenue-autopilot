"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";

/**
 * Copy affordance for monospace identifiers and hashes. The only confirmed
 * feedback in the product is the inline "Copied" swap - no toasts needed.
 */
export function CopyButton({ value, label }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard unavailable (permissions/insecure context): stay silent,
      // the value remains selectable text.
    }
  };

  return (
    <button
      type="button"
      onClick={onCopy}
      title={label ?? "Copy"}
      aria-label={copied ? "Copied" : (label ?? "Copy")}
      className="inline-flex items-center gap-1 rounded border border-gray-200 bg-white px-1.5 py-0.5 text-[11px] font-medium text-gray-500 transition-colors hover:bg-gray-50 hover:text-gray-700"
    >
      {copied ? (
        <>
          <Check size={11} aria-hidden className="text-emerald-600" />
          Copied
        </>
      ) : (
        <>
          <Copy size={11} aria-hidden />
          Copy
        </>
      )}
    </button>
  );
}
