"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { Database, FileSpreadsheet, RefreshCw } from "lucide-react";
import { appendDemoPeriod, appendMerchantCsv } from "@/lib/api";
import { DEFAULT_MERCHANT_ID } from "@/lib/constants";
import { describeApiError, type DescribedError } from "@/lib/errors";
import type { MerchantDataStatus } from "@/lib/onboarding-types";
import type { MerchantSummary } from "@/lib/types";
import { InlineError } from "./inline-error";
import { LoadingButton } from "./loading-button";

export function DataUpdateView({
  merchant,
  initialStatus,
}: {
  merchant: MerchantSummary;
  initialStatus: MerchantDataStatus;
}) {
  const isDemo = merchant.merchant_id === DEFAULT_MERCHANT_ID;
  const [status, setStatus] = useState(initialStatus);
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<DescribedError | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const appendCsv = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setMessage(null);
    if (!file) {
      setError({
        title: "Choose a CSV with new transactions.",
        detail: null,
        code: "CSV_REQUIRED",
        tone: "red",
      });
      return;
    }
    if (!file.name.toLowerCase().endsWith(".csv")) {
      setError({
        title: "Upload must be a .csv file.",
        detail: null,
        code: "CSV_REQUIRED",
        tone: "red",
      });
      return;
    }

    setLoading(true);
    try {
      const result = await appendMerchantCsv(merchant.merchant_id, file);
      setStatus({
        merchant_id: result.merchant_id,
        data_source: result.data_source,
        historical_observations: result.historical_observations,
        real_observations: result.real_observations,
        simulated_observations: result.simulated_observations,
        segment_count: result.segment_count,
        has_data: result.historical_observations > 0,
      });
      setFile(null);
      setMessage(
        result.rows_appended === 0
          ? `No new evidence was added. All ${result.rows_deduplicated} transactions were already present.`
          : `Added ${result.rows_appended} new transaction${result.rows_appended === 1 ? "" : "s"}; ${result.rows_deduplicated} duplicate${result.rows_deduplicated === 1 ? " was" : "s were"} ignored.`,
      );
    } catch (caught) {
      setError(describeApiError(caught));
    } finally {
      setLoading(false);
    }
  };

  const advanceDemo = async () => {
    setError(null);
    setMessage(null);
    setLoading(true);
    try {
      const result = await appendDemoPeriod();
      setStatus({
        merchant_id: result.merchant_id,
        data_source: result.data_source,
        historical_observations: result.historical_observations,
        real_observations: result.real_observations,
        simulated_observations: result.simulated_observations,
        segment_count: result.segment_count,
        has_data: result.historical_observations > 0,
      });
      const start = new Date(result.period_start).toISOString().slice(0, 10);
      const end = new Date(result.period_end).toISOString().slice(0, 10);
      setMessage(
        `Demo period ${result.period_index} appended ${result.rows_appended} new transactions (${start} → ${end}).`,
      );
    } catch (caught) {
      setError(describeApiError(caught));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-5">
      {error && <InlineError error={error} className="max-w-3xl" />}
      {message && (
        <div className="max-w-3xl rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-[13px] text-emerald-800">
          {message}
        </div>
      )}

      <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <Database size={17} className="text-gray-500" aria-hidden />
              <h2 className="text-[16px] font-semibold text-gray-900">Current evidence</h2>
            </div>
            <p className="mt-1 text-[12.5px] text-gray-500">
              {isDemo ? "Deterministic TechBazaar demo history" : "Observed merchant payment history"}
            </p>
          </div>
          <Link
            href="/overview"
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-[13px] font-medium text-gray-700 hover:bg-gray-50"
          >
            Return to Overview
          </Link>
        </div>

        <div className="mt-5 grid gap-px overflow-hidden rounded-lg border border-gray-200 bg-gray-100 sm:grid-cols-3">
          <Metric label="Historical observations" value={status.historical_observations} />
          <Metric label="Segments" value={status.segment_count} />
          <Metric
            label={isDemo ? "Simulated observations" : "Real observations"}
            value={isDemo ? status.simulated_observations : status.real_observations}
          />
        </div>
      </section>

      {isDemo ? (
        <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-indigo-50 text-indigo-700">
            <RefreshCw size={18} aria-hidden />
          </div>
          <h2 className="mt-4 text-[18px] font-semibold text-gray-900">Append next demo period</h2>
          <p className="mt-2 max-w-2xl text-[13.5px] leading-6 text-gray-600">
            Generate the next seven-day TechBazaar period. Its timestamps start after the
            current historical window and its transaction ids are distinct, so no baseline
            row is replayed as new evidence.
          </p>
          <div className="mt-5">
            <LoadingButton
              loading={loading}
              loadingLabel="Appending new period…"
              onClick={advanceDemo}
            >
              Append next period
            </LoadingButton>
          </div>
        </section>
      ) : (
        <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-50 text-emerald-700">
            <FileSpreadsheet size={18} aria-hidden />
          </div>
          <h2 className="mt-4 text-[18px] font-semibold text-gray-900">Upload new transactions</h2>
          <p className="mt-2 max-w-2xl text-[13.5px] leading-6 text-gray-600">
            Upload another file using the same canonical columns. Existing external ids are
            deduplicated; a duplicate id with different transaction data is rejected rather
            than rewriting history.
          </p>
          <form className="mt-5 space-y-4" onSubmit={appendCsv}>
            <label className="block max-w-2xl rounded-lg border border-dashed border-gray-300 bg-slate-50 p-4">
              <span className="text-[12.5px] font-medium text-gray-700">Incremental payment CSV</span>
              <input
                key={file?.name ?? "empty"}
                type="file"
                accept=".csv,text/csv"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                className="mt-2 block w-full text-[12.5px] text-gray-600 file:mr-3 file:rounded-md file:border-0 file:bg-white file:px-3 file:py-1.5 file:text-[12.5px] file:font-medium file:text-gray-700"
              />
              <span className="mt-2 block text-[11.5px] text-gray-500">
                UTF-8 CSV · same schema as onboarding · up to 10 MB
              </span>
            </label>
            <LoadingButton type="submit" loading={loading} loadingLabel="Deduplicating and appending…">
              Append new transactions
            </LoadingButton>
          </form>
        </section>
      )}

      <p className="max-w-3xl text-[12px] leading-5 text-gray-500">
        Adding data does not automatically authorize an experiment. It only advances the
        evidence revision; the normal detector → AI → policy → experiment boundaries remain
        unchanged.
      </p>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-white px-4 py-3">
      <p className="text-[11px] uppercase tracking-wide text-gray-400">{label}</p>
      <p className="mt-1 text-[20px] font-semibold tabular-nums text-gray-900">
        {value.toLocaleString("en-IN")}
      </p>
    </div>
  );
}
