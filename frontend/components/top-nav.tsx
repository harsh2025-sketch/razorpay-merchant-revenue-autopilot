"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import {
  ACTIVE_MERCHANT_ID_STORAGE,
  ACTIVE_MERCHANT_NAME_STORAGE,
  DEFAULT_MERCHANT_ID,
  DEFAULT_MERCHANT_NAME,
} from "@/lib/constants";

const NAV_LINKS = [
  { href: "/overview", label: "Overview" },
  { href: "/intelligence", label: "Intelligence" },
  { href: "/autopilot", label: "Autopilot" },
  { href: "/audit", label: "Audit Log" },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/overview") {
    return pathname === "/" || pathname.startsWith("/overview");
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function TopNav() {
  const pathname = usePathname() ?? "";
  const [merchantId, setMerchantId] = useState(DEFAULT_MERCHANT_ID);
  const [merchantName, setMerchantName] = useState(DEFAULT_MERCHANT_NAME);

  useEffect(() => {
    const refreshMerchant = () => {
      setMerchantId(
        window.localStorage.getItem(ACTIVE_MERCHANT_ID_STORAGE) ?? DEFAULT_MERCHANT_ID,
      );
      setMerchantName(
        window.localStorage.getItem(ACTIVE_MERCHANT_NAME_STORAGE) ?? DEFAULT_MERCHANT_NAME,
      );
    };
    refreshMerchant();
    window.addEventListener("mra:merchant-changed", refreshMerchant);
    return () => window.removeEventListener("mra:merchant-changed", refreshMerchant);
  }, []);

  const demoMode = merchantId === DEFAULT_MERCHANT_ID;

  return (
    <header className="sticky top-0 z-30 border-b border-gray-200 bg-white">
      <div className="mx-auto flex h-[60px] w-full max-w-[1320px] items-center gap-6 px-4 sm:px-8">
        <Link
          href="/overview"
          className="shrink-0 text-[15px] font-semibold tracking-tight text-gray-900"
        >
          Revenue Autopilot
        </Link>
        <nav aria-label="Primary" className="hidden items-stretch self-stretch sm:flex">
          {NAV_LINKS.map((link) => {
            const active = isActive(pathname, link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={active ? "page" : undefined}
                className={`relative flex items-center px-3 text-[13.5px] transition-colors ${
                  active
                    ? "font-medium text-gray-900"
                    : "text-gray-500 hover:text-gray-900"
                }`}
              >
                {link.label}
                {active && (
                  <span
                    aria-hidden
                    className="absolute inset-x-2.5 bottom-[-1px] h-0.5 rounded-full bg-indigo-600"
                  />
                )}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-2.5">
          <span className="hidden max-w-[220px] truncate text-[13px] font-medium text-gray-700 md:inline">
            {merchantName}
          </span>
          <span
            className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${
              demoMode
                ? "border-amber-200 bg-amber-50 text-amber-700"
                : "border-emerald-200 bg-emerald-50 text-emerald-700"
            }`}
          >
            {demoMode ? "Demo Data" : "Merchant Data"}
          </span>
          <Link
            href="/onboarding"
            className="rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-[12px] font-medium text-gray-700 hover:bg-gray-50"
          >
            Switch
          </Link>
        </div>
      </div>
      <nav
        aria-label="Primary mobile"
        className="flex items-center gap-1 border-t border-gray-100 px-4 pb-2 pt-1 sm:hidden"
      >
        {NAV_LINKS.map((link) => {
          const active = isActive(pathname, link.href);
          return (
            <Link
              key={link.href}
              href={link.href}
              aria-current={active ? "page" : undefined}
              className={`rounded px-2 py-1 text-[13px] ${
                active
                  ? "bg-indigo-50 font-medium text-indigo-700"
                  : "text-gray-500 hover:text-gray-900"
              }`}
            >
              {link.label}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}
