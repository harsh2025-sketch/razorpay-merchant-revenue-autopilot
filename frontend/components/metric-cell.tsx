interface MetricCellProps {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  className?: string;
}

/** One metric in a strip: 12px label, ~24px value, optional 12.5px sub-line. */
export function MetricCell({ label, value, sub, className = "" }: MetricCellProps) {
  return (
    <div className={`min-w-0 px-5 py-4 ${className}`}>
      <p className="text-[12px] font-medium text-gray-500">{label}</p>
      <p className="mt-1 truncate text-[24px] font-semibold leading-tight tracking-tight text-gray-900 tabular-nums">
        {value}
      </p>
      {sub && <p className="mt-0.5 truncate text-[12.5px] text-gray-500">{sub}</p>}
    </div>
  );
}
