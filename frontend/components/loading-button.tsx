"use client";

import { Loader2 } from "lucide-react";

type Variant = "primary" | "outline" | "danger";
type ButtonType = "button" | "submit" | "reset";

const VARIANT_CLASSES: Record<Variant, string> = {
  primary:
    "border border-indigo-600 bg-indigo-600 text-white hover:bg-indigo-700 disabled:hover:bg-indigo-600",
  outline:
    "border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:hover:bg-white",
  danger:
    "border border-red-600 bg-red-600 text-white hover:bg-red-700 disabled:hover:bg-red-600",
};

/**
 * Single button-level loading surface for mutations: spinner + label only.
 * No full-page spinners, no blocking overlays - the button disables while
 * its request is active and the page keeps its content.
 */
export function LoadingButton({
  loading = false,
  loadingLabel,
  disabled = false,
  variant = "primary",
  type = "button",
  onClick,
  children,
  className = "",
  title,
}: {
  loading?: boolean;
  loadingLabel?: string;
  disabled?: boolean;
  variant?: Variant;
  type?: ButtonType;
  onClick?: () => void;
  children: React.ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled || loading}
      title={title}
      className={`inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md px-3.5 py-2 text-[13px] font-medium shadow-none transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${VARIANT_CLASSES[variant]} ${className}`}
    >
      {loading && <Loader2 size={14} aria-hidden className="animate-spin" />}
      <span>{loading && loadingLabel ? loadingLabel : children}</span>
    </button>
  );
}
