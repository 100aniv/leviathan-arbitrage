import React from "react";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";
import { InfoTooltip } from "./InfoTooltip";
import clsx from "clsx";

interface KPICardProps {
  label: string;
  value: React.ReactNode;
  subValue?: React.ReactNode;
  delta?: number;          // 변화율 (퍼센트, +/- 부호 포함)
  deltaLabel?: string;
  tooltip?: string;
  size?: "sm" | "md" | "lg";
  loading?: boolean;
  className?: string;
}

export function KPICard({
  label,
  value,
  subValue,
  delta,
  deltaLabel,
  tooltip,
  size = "md",
  loading = false,
  className = "",
}: KPICardProps) {
  const isPositive = delta !== undefined && delta > 0;
  const isNegative = delta !== undefined && delta < 0;

  const valueSize = {
    sm: "text-title",
    md: "text-2xl",
    lg: "text-display",
  }[size];

  return (
    <div className={clsx("card", className)}>
      {/* 레이블 */}
      <div className="flex items-center gap-1.5 mb-2">
        <span className="text-caption text-text-secondary font-medium">{label}</span>
        {tooltip && <InfoTooltip content={tooltip} />}
      </div>

      {/* 주요 수치 */}
      {loading ? (
        <div className="skeleton h-8 w-32 mb-1" aria-label="불러오는 중" />
      ) : (
        <div
          className={clsx(valueSize, "font-bold text-text-primary tabular-nums leading-tight")}
          style={{ fontVariantNumeric: "tabular-nums", fontFeatureSettings: '"tnum"' }}
        >
          {value}
        </div>
      )}

      {/* 보조 수치 */}
      {subValue && !loading && (
        <div className="text-caption text-text-secondary mt-0.5 tabular-nums">
          {subValue}
        </div>
      )}

      {/* 변화율 */}
      {delta !== undefined && !loading && (
        <div className={clsx(
          "flex items-center gap-1 mt-2 text-small font-medium",
          isPositive && "text-success",
          isNegative && "text-danger",
          !isPositive && !isNegative && "text-text-secondary",
        )}>
          {isPositive && <TrendingUp size={13} aria-label="상승" />}
          {isNegative && <TrendingDown size={13} aria-label="하락" />}
          {!isPositive && !isNegative && <Minus size={13} aria-label="변화없음" />}
          <span>
            {isPositive ? "+" : ""}{delta.toFixed(2)}%
            {deltaLabel && <span className="text-text-tertiary ml-1">{deltaLabel}</span>}
          </span>
        </div>
      )}
    </div>
  );
}
