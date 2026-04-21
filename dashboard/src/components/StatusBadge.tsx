import React from "react";
import clsx from "clsx";

/**
 * Path-B v2 W3 status badge — verified | pending | diverged | halted.
 * DESIGN.md §4 Badge pattern (re-export namespace, distinct from ui/StatusBadge
 * which carries the operational/safety variants used by the rest of the app).
 */
export type PathBStatus =
  | "verified"
  | "pending"
  | "diverged"
  | "halted"
  | "ok"
  | "warn"
  | "bad";

interface StatusBadgeProps {
  status: PathBStatus;
  label?: string;
  pulse?: boolean;
  className?: string;
}

const VARIANT_CLS: Record<PathBStatus, string> = {
  verified: "bg-success-bg text-[#026B3F]",
  ok:       "bg-success-bg text-[#026B3F]",
  pending:  "bg-warning-bg text-[#92400E]",
  warn:     "bg-warning-bg text-[#92400E]",
  diverged: "bg-danger-bg text-danger",
  halted:   "bg-danger-bg text-danger",
  bad:      "bg-danger-bg text-danger",
};

const DEFAULT_LABEL: Record<PathBStatus, string> = {
  verified: "VERIFIED",
  ok:       "OK",
  pending:  "PENDING",
  warn:     "WARN",
  diverged: "DIVERGED",
  halted:   "HALTED",
  bad:      "BAD",
};

export function StatusBadge({
  status,
  label,
  pulse = false,
  className = "",
}: StatusBadgeProps) {
  const cls = VARIANT_CLS[status];
  const display = label ?? DEFAULT_LABEL[status];
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 px-2 py-0.5 rounded-[4px] text-[10px] font-mono font-medium uppercase tracking-wider",
        cls,
        pulse && "pulse-green",
        className,
      )}
      aria-label={`상태: ${display}`}
    >
      {display}
    </span>
  );
}
