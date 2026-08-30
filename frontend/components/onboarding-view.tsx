"use client";

import { FormEvent, useState } from "react";
import { Database, FileSpreadsheet, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { getDemoMerchantSource, onboardMerchantWithCsv } from "@/lib/api";
import {
  ACTIVE_MERCHANT_ID_COOKIE,
  ACTIVE_MERCHANT_ID_STORAGE,
  ACTIVE_MERCHANT_NAME_STORAGE,
} from "@/lib/constants";
import { describeApiError, type DescribedError } from "@/lib/errors";
import { InlineError } from "./inline-error";
import { LoadingButton } from "./loading-button";

const REQUIRED_COLUMNS = [
  "external_id",
  "amount_paise",
  "status",
  "created_at",
  "segment",
  "payment_method",
];

const OPTIONAL_COLUMNS = [
  "currency",
  "failure_reason",
  "device_type",
  "customer_ref",
  "internal_order_ref",
  "razorpay_order_id",
  "razorpay_payment_id",
  "razorpay_payment_link_id",
  "completed_at",
];

function selectMerchant(merchantId: string, merchantName: string): void {
  document.cookie = `${ACTIVE_MERCHANT_ID_COOKIE}=${encodeURIComponent(merchantId)}; Path=/; Max-Age=31536000; SameSite=Lax`;
  window.localStorage.setItem(ACTIVE_MERCHANT_ID_STORAGE, merchantId);
  window.localStorage.setItem(ACTIVE_MERCHANT_NAME_STORAGE, merchantName);
  window.dispatchEvent(new Event("mra:merchant-changed"));
}

function monthlyGmvToPaise(raw: string): number | undefined {
  const trimmed = raw.trim();
  if (!trimmed) return undefined;
  const rupees = Number(trimmed);
  if (!Number.isFinite(rupees) || rupees < 0) {
    throw new Error("Monthly GMV must be a non-negative amount in rupees.");
  }
  const paise = Math.round(rupees * 100);
  if (!Number.isSafeInteger(paise)) {
    throw new Error("Monthly GMV is too large.");
  }
  return paise;
}

export function OnboardingView() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [monthlyGmv, setMonthlyGmv] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [demoLoading, setDemoLoading] = useState(false);
  const [uploadLoading, setUploadLoading] = useState(false);
  const [error, setError] = useState<DescribedError | null>(null);

  const finishSelection = (merchantId: string, merchantName: string) => {
    selectMerchant(merchantId, merchantName);
    router.push("/overview");
    router.refresh();
  };

  const useDemo = async () => {
    setError(null);
    setDemoLoading(true);
    try {
      const demo = await getDemoMerchantSource();
      finishSelection(demo.merchant_id, demo.name);
    } catch (caught) {
      setError(describeApiError(caught));
      setDemoLoading(false);
    }
  };

  const submitMerchant = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);

    if (!file) {
      setError({
        title: "Payment CSV required",
        detail: "Choose the merchant payment-history CSV before continuing.",
        code: "CSV_REQUIRED",
        tone: "red",
      });
      return;
    }
    if (!file.name.toLowerCase().endsWith(".csv")) {
      setError({
        title: "CSV file required",
        detail: "Revenue Autopilot accepts a UTF-8 .csv file for initial onboarding.",
        code: "CSV_REQUIRED",
        tone: "red",
      });
      return;
    }

    let monthlyGmvPaise: number | undefined;
    try {
      monthlyGmvPaise = monthlyGmvToPaise(monthlyGmv);
    } catch (caught) {
      setError({
        title: "Check monthly GMV",
        detail: caught instanceof Error ? caught.message : "Monthly GMV is invalid.",
        code: "INVALID_MONTHLY_GMV",
        tone: "red",
      });
      return;
    }

    setUploadLoading(true);
    try {
      const merchant = await onboardMerchantWithCsv({
        name,
        category,
        monthlyGmvPaise,
        file,
      });
      finishSelection(merchant.merchant_id, merchant.name);
    } catch (caught) {
      setError(describeApiError(caught));
      setUploadLoading(false);
    }
  };

  return (
    <div className="space-y-5">
      {error && <InlineError error={error} className="max-w-3xl" />}

      <div className="grid gap-5 lg:grid-cols-2">
        <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-indigo-50 text-indigo-700">
            <Database size={19} aria-hidden />
          </div>
          <h2 className="mt-4 text-[19px] font-semibold tracking-tight text-gray-900">
            Explore with TechBazaar
          </h2>
          <p className="mt-2 text-[13.5px] leading-6 text-gray-600">
            Use the preloaded demonstration merchant. Its synthetic historical
            payment population stays explicitly separated from real merchant data.
          </p>
          <div className="mt-5 rounded-lg border border-gray-100 bg-slate-50 px-4 py-3 text-[12.5px] text-gray-600">
            No upload required. Existing TechBazaar experiment history and Razorpay
            Test Mode evidence remain available.
          </div>
          <div className="mt-5">
            <LoadingButton
              loading={demoLoading}
              loadingLabel="Opening demo…"
              onClick={useDemo}
            >
              Use TechBazaar demo
            </LoadingButton>
          </div>
        </section>

        <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-50 text-emerald-700">
            <FileSpreadsheet size={19} aria-hidden />
          </div>
          <h2 className="mt-4 text-[19px] font-semibold tracking-tight text-gray-900">
            Use my merchant data
          </h2>
          <p className="mt-2 text-[13.5px] leading-6 text-gray-600">
            Register a company and ingest its initial payment history. Uploaded rows
            are stored as observed merchant data, never as simulator output.
          </p>

          <form className="mt-5 space-y-4" onSubmit={submitMerchant}>
            <label className="block">
              <span className="text-[12.5px] font-medium text-gray-700">Company name</span>
              <input
                required
                minLength={2}
                maxLength={120}
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Acme Commerce"
                className="mt-1.5 w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-[13.5px] outline-none ring-indigo-500 focus:ring-2"
              />
            </label>

            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block">
                <span className="text-[12.5px] font-medium text-gray-700">Category</span>
                <input
                  maxLength={80}
                  value={category}
                  onChange={(event) => setCategory(event.target.value)}
                  placeholder="Electronics"
                  className="mt-1.5 w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-[13.5px] outline-none ring-indigo-500 focus:ring-2"
                />
              </label>
              <label className="block">
                <span className="text-[12.5px] font-medium text-gray-700">
                  Monthly GMV (₹)
                </span>
                <input
                  inputMode="decimal"
                  value={monthlyGmv}
                  onChange={(event) => setMonthlyGmv(event.target.value)}
                  placeholder="2500000"
                  className="mt-1.5 w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-[13.5px] outline-none ring-indigo-500 focus:ring-2"
                />
              </label>
            </div>

            <label className="block rounded-lg border border-dashed border-gray-300 bg-slate-50 p-4">
              <span className="text-[12.5px] font-medium text-gray-700">
                Initial payment-history CSV
              </span>
              <input
                required
                type="file"
                accept=".csv,text/csv"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                className="mt-2 block w-full text-[12.5px] text-gray-600 file:mr-3 file:rounded-md file:border-0 file:bg-white file:px-3 file:py-1.5 file:text-[12.5px] file:font-medium file:text-gray-700"
              />
              <span className="mt-2 block text-[11.5px] text-gray-500">
                UTF-8 CSV · up to 10 MB · up to 100,000 rows
              </span>
            </label>

            <LoadingButton
              type="submit"
              loading={uploadLoading}
              loadingLabel="Validating and importing…"
            >
              Register and import data
            </LoadingButton>
          </form>
        </section>
      </div>

      <section className="rounded-xl border border-gray-200 bg-white p-5">
        <div className="flex items-start gap-3">
          <ShieldCheck size={17} className="mt-0.5 shrink-0 text-gray-500" aria-hidden />
          <div>
            <h2 className="text-[14px] font-semibold text-gray-900">Canonical CSV contract</h2>
            <p className="mt-1 text-[12.5px] leading-5 text-gray-600">
              Required columns are validated before anything is committed. Status must
              be <span className="font-mono">captured</span>,{" "}
              <span className="font-mono">failed</span>, or{" "}
              <span className="font-mono">abandoned</span>; timestamps are ISO-8601;
              amounts are integer paise.
            </p>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {REQUIRED_COLUMNS.map((column) => (
                <code key={column} className="rounded bg-indigo-50 px-1.5 py-0.5 text-[11px] text-indigo-700">
                  {column}
                </code>
              ))}
            </div>
            <p className="mt-3 text-[11.5px] text-gray-500">
              Optional: {OPTIONAL_COLUMNS.join(", ")}.
            </p>
            <p className="mt-2 text-[11.5px] font-medium text-gray-600">
              Task 21A accepts the initial history once. New files are appended and
              deduplicated by the incremental-ingestion path in Task 21B rather than
              replaying this baseline.
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}
