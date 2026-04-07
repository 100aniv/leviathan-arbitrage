import React from "react";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";
import clsx from "clsx";

type Currency = "KRW" | "USD" | "pct" | "plain";

interface NumberDisplayProps {
  value: number | null | undefined;
  currency?: Currency;
  decimals?: number;
  showSign?: boolean;    // +/- 부호 표시
  showArrow?: boolean;   // ▲▼ 아이콘 표시 (색맹 접근성)
  colorize?: boolean;    // 양수=초록, 음수=빨강
  loading?: boolean;
  className?: string;
  "aria-label"?: string;
}

const KRW_FORMATTER = new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 });
const USD_FORMATTER = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function NumberDisplay({
  value,
  currency = "plain",
  decimals = 2,
  showSign = false,
  showArrow = false,
  colorize = false,
  loading = false,
  className = "",
  "aria-label": ariaLabel,
}: NumberDisplayProps) {
  if (loading) {
    return <span className="skeleton inline-block h-4 w-16" aria-label="불러오는 중" />;
  }

  if (value === null || value === undefined || isNaN(value)) {
    return <span className="text-text-tertiary">—</span>;
  }

  const isPositive = value > 0;
  const isNegative = value < 0;

  let formatted: string;
  if (currency === "KRW")  formatted = KRW_FORMATTER.format(value);
  else if (currency === "USD") formatted = USD_FORMATTER.format(value);
  else if (currency === "pct") formatted = `${value.toFixed(decimals)}%`;
  else formatted = value.toLocaleString("ko-KR", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });

  if (showSign && isPositive && currency !== "KRW" && currency !== "USD") {
    formatted = `+${formatted}`;
  }

  const colorClass = colorize
    ? isPositive ? "text-success" : isNegative ? "text-danger" : "text-text-primary"
    : "";

  return (
    <span
      className={clsx(
        "tabular-nums inline-flex items-center gap-1",
        colorClass,
        className,
      )}
      style={{ fontVariantNumeric: "tabular-nums", fontFeatureSettings: '"tnum"' }}
      aria-label={ariaLabel ?? `${formatted}${isPositive ? " 수익" : isNegative ? " 손실" : ""}`}
    >
      {showArrow && isPositive && <TrendingUp size={12} aria-label="상승" />}
      {showArrow && isNegative && <TrendingDown size={12} aria-label="하락" />}
      {showArrow && !isPositive && !isNegative && <Minus size={12} aria-label="변화없음" />}
      {formatted}
    </span>
  );
}

/** 단축 헬퍼 */
export function formatKRW(v: number): string { return KRW_FORMATTER.format(v); }
export function formatUSD(v: number): string { return USD_FORMATTER.format(v); }
export function formatPct(v: number, decimals = 2): string { return `${v >= 0 ? "+" : ""}${v.toFixed(decimals)}%`; }
export function formatNum(v: number, decimals = 2): string {
  return v.toLocaleString("ko-KR", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}
