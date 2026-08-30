"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { MERCHANT_NAME } from "@/lib/constants";

const NAV_LINKS = [
  { href: "/overview", label: "Overview" },
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
        <div className="ml-auto flex items-center gap-3">
          <span className="hidden text-[13px] font-medium text-gray-700 md:inline">
            {MERCHANT_NAME}
          </span>
          <span
            title="Razorpay Test Mode"
            className="inline-flex items-center rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-amber-700"
          >
            Test Mode
          </span>
        </div>
      </div>
      {/* Mobile fallback: simple inline menu, no collapse machinery. */}
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
