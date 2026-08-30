import type { Metadata } from "next";
import { AppShell } from "@/components/app-shell";
import "./globals.css";

export const metadata: Metadata = {
  title: "Revenue Autopilot · TechBazaar Electronics",
  description:
    "Merchant revenue optimization console: observed evidence, AI proposals, deterministic policy, Razorpay Test Mode execution and fixed-horizon statistics.",
};

/**
 * Typography: one sans-serif family. Inter is used when installed locally;
 * the tailwind stack falls back to clean system sans-serifs so the build
 * never depends on a font CDN.
 */
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="bg-slate-50 font-sans text-gray-900 antialiased">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
