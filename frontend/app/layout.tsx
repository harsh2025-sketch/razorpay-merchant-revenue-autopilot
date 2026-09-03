import type { Metadata } from "next";
import { Source_Sans_3 } from "next/font/google";
import { AppShell } from "@/components/app-shell";
import "./globals.css";

const sourceSans = Source_Sans_3({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-source-sans",
});

export const metadata: Metadata = {
  title: "Revenue Autopilot",
  description:
    "Merchant revenue optimization console: observed evidence, AI proposals, deterministic policy, an explicit Razorpay execution boundary, and fixed-horizon statistics.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body
        className={`${sourceSans.variable} bg-[#F9FAFB] font-sans text-[#111827] antialiased`}
      >
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
