interface PageHeaderProps {
  title: string;
  subtitle?: string;
  /** Small factual secondary line (e.g. attempt count). */
  meta?: string;
  right?: React.ReactNode;
}

export function PageHeader({ title, subtitle, meta, right }: PageHeaderProps) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        <h1 className="text-[28px] font-semibold leading-tight tracking-tight text-gray-900">
          {title}
        </h1>
        {subtitle && (
          <p className="mt-1 text-[14px] text-gray-500">{subtitle}</p>
        )}
        {meta && <p className="mt-0.5 text-[13px] text-gray-400">{meta}</p>}
      </div>
      {right && <div className="flex shrink-0 items-center gap-3">{right}</div>}
    </div>
  );
}
