import { cookies } from "next/headers";
import {
  ACTIVE_MERCHANT_ID_COOKIE,
  DEFAULT_MERCHANT_ID,
} from "./constants";

const SAFE_MERCHANT_ID = /^[A-Za-z0-9_.:-]{1,200}$/;

/**
 * Resolve the merchant selected by the browser onboarding flow.
 *
 * Existing demo users keep working because absence (or corruption) of the
 * cookie falls back to the canonical TechBazaar merchant.
 */
export function getActiveMerchantId(): string {
  const raw = cookies().get(ACTIVE_MERCHANT_ID_COOKIE)?.value?.trim();
  if (!raw || !SAFE_MERCHANT_ID.test(raw)) return DEFAULT_MERCHANT_ID;
  return raw;
}
