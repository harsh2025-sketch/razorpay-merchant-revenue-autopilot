/** Default demo identity and API location. */

export const DEFAULT_MERCHANT_ID = "merchant_techbazaar";
export const DEFAULT_MERCHANT_NAME = "TechBazaar Electronics";

/** Browser-persisted active merchant selection. */
export const ACTIVE_MERCHANT_ID_COOKIE = "mra_merchant_id";
export const ACTIVE_MERCHANT_ID_STORAGE = "mra_merchant_id";
export const ACTIVE_MERCHANT_NAME_STORAGE = "mra_merchant_name";

/**
 * Root of the Merchant Revenue Autopilot API as seen by browser-side fetches.
 * Local development defaults to the FastAPI server on port 8000; production
 * must set NEXT_PUBLIC_API_BASE_URL to the deployed Render HTTPS origin.
 */
const PUBLIC_API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/**
 * Absolute API URL for server-side rendering. In production this should point
 * at the same deployed Render HTTPS origin as NEXT_PUBLIC_API_BASE_URL. The
 * value is not a secret and is never exposed as a backend credential.
 */
const INTERNAL_API_BASE =
  process.env.API_INTERNAL_BASE_URL ?? "http://localhost:8000";

const runningOnServer = typeof window === "undefined";

export const API_BASE_URL =
  runningOnServer && PUBLIC_API_BASE.startsWith("/")
    ? INTERNAL_API_BASE
    : PUBLIC_API_BASE;

/** Events shown in the Overview / cycle "recent activity" lists. */
export const RECENT_ACTIVITY_LIMIT = 5;

/** Events fetched for the Audit Log page. */
export const AUDIT_PAGE_LIMIT = 100;

export const API_PATHS = {
  merchant: (merchantId: string) => `/api/v1/merchants/${merchantId}`,
  overview: (merchantId: string) => `/api/v1/merchants/${merchantId}/overview`,
  intelligence: (merchantId: string) =>
    `/api/v1/merchants/${merchantId}/intelligence`,
  opportunities: (merchantId: string) =>
    `/api/v1/merchants/${merchantId}/opportunities`,
  cycle: (opportunityId: string) =>
    `/api/v1/opportunities/${opportunityId}/cycle`,
  merchantAudit: (merchantId: string, limit: number) =>
    `/api/v1/merchants/${merchantId}/audit?limit=${limit}`,
  autopilotStep: (merchantId: string) =>
    `/api/v1/merchants/${merchantId}/autopilot/step`,
  newAutopilotCycle: (merchantId: string) =>
    `/api/v1/merchants/${merchantId}/autopilot/new-cycle`,
  rollbackExperiment: (experimentId: string) =>
    `/api/v1/experiments/${experimentId}/rollback`,
  onboardingDemo: "/api/v1/onboarding/demo",
  onboardMerchantWithCsv: "/api/v1/onboarding/merchants/with-csv",
  onboardingDataStatus: (merchantId: string) =>
    `/api/v1/onboarding/merchants/${merchantId}/data-status`,
} as const;
