export default function OverviewLoading() {
  return (
    <div className="space-y-5" aria-label="Loading merchant overview" aria-busy="true">
      <div className="space-y-2">
        <div className="h-7 w-56 animate-pulse rounded bg-slate-200" />
        <div className="h-4 w-80 max-w-full animate-pulse rounded bg-slate-200" />
        <div className="h-3 w-36 animate-pulse rounded bg-slate-100" />
      </div>

      <div className="h-36 animate-pulse rounded-lg border border-slate-200 bg-white" />

      <div className="grid overflow-hidden rounded-xl border border-slate-200 bg-slate-200 sm:grid-cols-3 sm:gap-px">
        {[0, 1, 2].map((item) => (
          <div key={item} className="bg-white p-5">
            <div className="h-3 w-24 animate-pulse rounded bg-slate-200" />
            <div className="mt-3 h-7 w-28 animate-pulse rounded bg-slate-200" />
            <div className="mt-2 h-3 w-36 animate-pulse rounded bg-slate-100" />
          </div>
        ))}
      </div>

      <div className="h-80 animate-pulse rounded-xl border border-slate-200 bg-white" />
    </div>
  );
}
