import React from "react";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";
import clsx from "clsx";

interface DataCardProps {
  label: string;
  value: React.ReactNode;
  unit?: string;
  delta?: number;                    // percent delta (optional)
  subValue?: React.ReactNode;
  tone?: "default" | "ok" | "warn" | "bad";
  loading?: boolean;
  className?: string;
}

/**
 * Path-B v2 W3 DataCard — compact KPI primitive.
 * label (small uppercase) + value (large mono) + optional delta/subValue.
 * DESIGN.md §4 Card pattern.
 */
export function DataCard({
  label,
  value,
  unit,
  delta,
  subValue,
  tone = "default",
  loading = false,
  className = "",
}: DataCardProps) {
  const isPositive = typeof delta === "number" && delta > 0;
  const isNegative = typeof delta === "number" && delta < 0;

  const toneCls = {
    default: "text-text-primary",
    ok:      "text-success",
    warn:    "text-warning",
    bad:     "text-danger",
  }[tone];

  return (
    <div
      className={clsx(
        "bg-bg-surface border border-border rounded-[12px] px-4 py-3",
        className,
      )}
    >
      <div className="text-small text-text-secondary uppercase tracking-widest font-medium">
        {label}
      </div>

      {loading ? (
        <div className="skeleton h-7 w-24 mt-1" aria-label="불러오는 중" />
      ) : (
        <div
          className={clsx(
            "mt-1 text-2xl font-mono font-semibold tabular-nums leading-tight",
            toneCls,
          )}
          style={{ fontVariantNumeric: "tabular-nums", fontFeatureSettings: '"tnum"' }}
        >
          {value}
          {unit && (
            <span className="ml-1 text-caption font-normal text-text-tertiary">
              {unit}
            </span>
          )}
        </div>
      )}

      {subValue && !loading && (
        <div className="text-small text-text-tertiary mt-1 tabular-nums">
          {subValue}
        </div>
      )}

      {typeof delta === "number" && !loading && (
        <div
          className={clsx(
            "flex items-center gap-1 mt-2 text-small font-medium",
            isPositive && "text-success",
            isNegative && "text-danger",
            !isPositive && !isNegative && "text-text-secondary",
          )}
        >
          {isPositive && <TrendingUp size={12} aria-hidden />}
          {isNegative && <TrendingDown size={12} aria-hidden />}
          {!isPositive && !isNegative && <Minus size={12} aria-hidden />}
          <span>
            {isPositive ? "+" : ""}
            {delta.toFixed(2)}%
          </span>
        </div>
      )}
    </div>
  );
}
