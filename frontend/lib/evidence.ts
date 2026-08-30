/**
 * Turns the detector's evidence dictionary into readable sections.
 *
 * Known sanitized keys become structured tables (cohorts, payment methods,
 * failure reasons); anything else lands in an explicit "Additional evidence"
 * key-value area. The raw dict is never dumped as JSON in the main UI.
 */

export interface EvidenceCohort {
  attempts: number | null;
  captured: number | null;
  conversionRate: number | null;
}

export interface EvidencePaymentMethod {
  method: string;
  attempts: number | null;
  captured: number | null;
  failed: number | null;
  abandoned: number | null;
  successRate: number | null;
}

export interface EvidenceFailureReason {
  reason: string;
  count: number;
}

export interface EvidenceKV {
  key: string;
  value: string;
}

export interface ParsedEvidence {
  segment: string | null;
  segmentCohort: EvidenceCohort;
  comparisonCohort: EvidenceCohort;
  paymentMethods: EvidencePaymentMethod[];
  failureReasons: EvidenceFailureReason[];
  additional: EvidenceKV[];
}

const KNOWN_KEYS = new Set([
  "segment",
  "segment_attempts",
  "segment_captured",
  "segment_conversion_rate",
  "comparison_attempts",
  "comparison_captured",
  "comparison_conversion_rate",
  "absolute_gap",
  "payment_method_metrics",
  "payment_method",
  "failure_reasons",
  "failure_reason",
]);

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function cohort(
  evidence: Record<string, unknown>,
  prefix: "segment" | "comparison",
): EvidenceCohort {
  const nested = evidence[prefix];
  if (nested && typeof nested === "object" && !Array.isArray(nested)) {
    const record = nested as Record<string, unknown>;
    return {
      attempts: num(record.attempts),
      captured: num(record.captured),
      conversionRate: num(record.conversion_rate),
    };
  }
  return {
    attempts: num(evidence[`${prefix}_attempts`]),
    captured: num(evidence[`${prefix}_captured`]),
    conversionRate: num(evidence[`${prefix}_conversion_rate`]),
  };
}

function parsePaymentMethods(
  evidence: Record<string, unknown>,
): EvidencePaymentMethod[] {
  const methods: EvidencePaymentMethod[] = [];
  const grouped = evidence.payment_method_metrics;
  if (grouped && typeof grouped === "object" && !Array.isArray(grouped)) {
    for (const [method, value] of Object.entries(
      grouped as Record<string, unknown>,
    )) {
      if (!value || typeof value !== "object" || Array.isArray(value)) continue;
      const record = value as Record<string, unknown>;
      methods.push({
        method,
        attempts: num(record.attempts),
        captured: num(record.captured),
        failed: num(record.failed),
        abandoned: num(record.abandoned),
        successRate: num(record.success_rate),
      });
    }
  }
  // Legacy flat shape: payment_method.<method>.<field>
  for (const [key, value] of Object.entries(evidence)) {
    if (!key.startsWith("payment_method.")) continue;
    if (!value || typeof value !== "object" || Array.isArray(value)) continue;
    const method = key.slice("payment_method.".length);
    const record = value as Record<string, unknown>;
    if (!methods.some((m) => m.method === method)) {
      methods.push({
        method,
        attempts: num(record.attempts),
        captured: num(record.captured),
        failed: num(record.failed),
        abandoned: num(record.abandoned),
        successRate: num(record.success_rate),
      });
    }
  }
  return methods;
}

function parseFailureReasons(
  evidence: Record<string, unknown>,
): EvidenceFailureReason[] {
  const reasons: EvidenceFailureReason[] = [];
  const push = (reason: string, count: unknown) => {
    if (typeof count === "number" && Number.isFinite(count)) {
      reasons.push({ reason, count });
    }
  };
  const grouped = evidence.failure_reasons;
  if (grouped && typeof grouped === "object" && !Array.isArray(grouped)) {
    for (const [reason, count] of Object.entries(
      grouped as Record<string, unknown>,
    )) {
      push(reason, count);
    }
  }
  for (const [key, value] of Object.entries(evidence)) {
    if (!key.startsWith("failure_reason.")) continue;
    push(key.slice("failure_reason.".length), value);
  }
  return reasons.sort((a, b) => b.count - a.count || a.reason.localeCompare(b.reason));
}

function formatScalar(value: unknown): string {
  if (value == null) return "-";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (typeof value === "string") return value;
  return "-";
}

export function parseEvidence(
  evidence: Record<string, unknown>,
): ParsedEvidence {
  const additional: EvidenceKV[] = [];
  for (const [key, value] of Object.entries(evidence)) {
    if (KNOWN_KEYS.has(key)) continue;
    if (
      key.startsWith("payment_method.") ||
      key.startsWith("failure_reason.")
    ) {
      continue; // handled by the structured parsers above
    }
    additional.push({ key, value: formatScalar(value) });
  }

  const segmentValue = evidence.segment;
  return {
    segment: typeof segmentValue === "string" ? segmentValue : null,
    segmentCohort: cohort(evidence, "segment"),
    comparisonCohort: cohort(evidence, "comparison"),
    paymentMethods: parsePaymentMethods(evidence),
    failureReasons: parseFailureReasons(evidence),
    additional,
  };
}
