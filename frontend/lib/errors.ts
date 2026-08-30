import { ApiError } from "./api";

/**
 * Safe, merchant-facing error copy. Known API codes map to fixed sentences;
 * unknown codes fall back to the backend's already-sanitized single-line
 * message. Raw error objects, exception class names and stack traces are
 * never rendered.
 */

export interface DescribedError {
  title: string;
  detail: string | null;
  code: string | null;
  tone: "red" | "amber";
}

const ERROR_TITLES: Record<string, string> = {
  OPENAI_NOT_CONFIGURED:
    "AI diagnosis is unavailable because OpenAI is not configured.",
  RAZORPAY_NOT_CONFIGURED:
    "Razorpay Test Mode credentials are not configured.",
  DEPLOYMENT_CONFIG_UNSUPPORTED:
    "This treatment cannot currently be deployed automatically.",
  EXECUTION_STATE_CONFLICT:
    "The previous external operation is still unresolved. Automatic retry is disabled.",
  EXPERIMENT_NOT_READY:
    "The experiment has not reached its fixed sample target.",
  NETWORK_ERROR: "Unable to connect to Revenue Autopilot.",
};

/** Codes rendered as an amber caution instead of a hard failure. */
const AMBER_CODES = new Set([
  "EXECUTION_STATE_CONFLICT",
  "DEPLOYMENT_CONFIG_UNSUPPORTED",
  "EXPERIMENT_NOT_READY",
]);

export function describeApiError(error: unknown): DescribedError {
  if (error instanceof ApiError) {
    const known = ERROR_TITLES[error.code];
    const tone: "red" | "amber" = AMBER_CODES.has(error.code) ? "amber" : "red";
    if (known) {
      return { title: known, detail: null, code: null, tone };
    }
    const message = error.message?.trim();
    return {
      title:
        message && message.length > 0 && message.length <= 300
          ? message
          : "The request could not be completed.",
      detail: null,
      code: error.code !== "NETWORK_ERROR" ? error.code : null,
      tone,
    };
  }
  return {
    title: "The request could not be completed.",
    detail: null,
    code: null,
    tone: "red",
  };
}
