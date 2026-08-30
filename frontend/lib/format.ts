/**
 * Deterministic display formatting. All money arrives in paise and is
 * converted to INR here for display only - backend values are never mutated.
 * Timestamps are rendered in a hand-rolled UTC form so server and client
 * markup are identical (no hydration mismatches, no locale drift).
 */

const inrFormatter = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

const intFormatter = new Intl.NumberFormat("en-IN");

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

/** Paise → "₹12,34,567" (Indian digit grouping). */
export function formatInrPaise(paise: number | null | undefined): string {
  if (paise == null || !Number.isFinite(paise)) return "-";
  return inrFormatter.format(paise / 100);
}

/** 12345 → "12,345" (en-IN grouping). */
export function formatInt(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "-";
  return intFormatter.format(value);
}

/** Rate fraction (0..1) → "47.2%". */
export function formatPercent(
  rate: number | null | undefined,
  digits = 1,
): string {
  if (rate == null || !Number.isFinite(rate)) return "-";
  return `${(rate * 100).toFixed(digits)}%`;
}

/** Signed rate-fraction difference → "+4.3pp" / "−11.4pp" (percentage points). */
export function formatPp(
  diff: number | null | undefined,
  digits = 1,
): string {
  if (diff == null || !Number.isFinite(diff)) return "-";
  const value = diff * 100;
  if (Math.abs(value) < 0.5 / 10 ** digits) return `0.0pp`;
  const sign = value > 0 ? "+" : "−";
  return `${sign}${Math.abs(value).toFixed(digits)}pp`;
}

/** Signed fraction → "+9.1%" (used for relative lift). */
export function formatSignedPercent(
  value: number | null | undefined,
  digits = 1,
): string {
  if (value == null || !Number.isFinite(value)) return "-";
  const v = value * 100;
  const sign = v > 0 ? "+" : v < 0 ? "−" : "";
  return `${sign}${Math.abs(v).toFixed(digits)}%`;
}

/** P-value → "0.0321" / "< 0.001" (never invented). */
export function formatPValue(p: number | null | undefined): string {
  if (p == null || !Number.isFinite(p)) return "-";
  if (p < 0.001) return "< 0.001";
  return p.toFixed(4);
}

/** Hours → "72h". */
export function formatDurationHours(hours: number | null | undefined): string {
  if (hours == null || !Number.isFinite(hours)) return "-";
  return `${Math.round(hours)}h`;
}

function pad(value: number): string {
  return value.toString().padStart(2, "0");
}

function utcParts(iso: string): {
  day: string;
  month: string;
  year: number;
  time: string;
} | null {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return {
    day: pad(date.getUTCDate()),
    month: MONTHS[date.getUTCMonth()],
    year: date.getUTCFullYear(),
    time: `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`,
  };
}

/** ISO string → "26 Aug 2026, 14:03 UTC" (deterministic in every runtime). */
export function formatUtcDateTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const parts = utcParts(iso);
  if (!parts) return "-";
  return `${parts.day} ${parts.month} ${parts.year}, ${parts.time} UTC`;
}

/** ISO string → "26 Aug, 14:03" (compact list rows). */
export function formatUtcShort(iso: string | null | undefined): string {
  if (!iso) return "-";
  const parts = utcParts(iso);
  if (!parts) return "-";
  return `${parts.day} ${parts.month}, ${parts.time}`;
}

/** "opp_1f0cad54-…" → "1f0cad54" (display-only short id). */
export function shortId(id: string | null | undefined, length = 8): string {
  if (!id) return "-";
  const tail = id.includes("_") ? id.slice(id.indexOf("_") + 1) : id;
  return tail.slice(0, length);
}

const PAYMENT_METHOD_LABELS: Record<string, string> = {
  upi: "UPI",
  card: "Card",
  netbanking: "Netbanking",
  wallet: "Wallet",
};

/** "upi" → "UPI"; unknown methods fall back to capitalization. */
export function paymentMethodLabel(method: string): string {
  const known = PAYMENT_METHOD_LABELS[method.toLowerCase()];
  if (known) return known;
  return method.charAt(0).toUpperCase() + method.slice(1);
}

/** "bank_declined" → "Bank declined". */
export function humanizeToken(token: string): string {
  const text = token.replace(/_/g, " ").trim();
  if (!text) return token;
  return text.charAt(0).toUpperCase() + text.slice(1);
}
