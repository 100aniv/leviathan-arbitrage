import React from "react";
import { CheckCircle, AlertTriangle, XCircle, Circle, Pause, StopCircle } from "lucide-react";
import clsx from "clsx";
import ko from "@/i18n/ko.json";

// 안전 상태 (탭4 전용)
export type SafetyLevel = "normal" | "caution" | "danger";

// 전략/거래소 상태 (운용 탭 전용)
export type OperationalStatus = "active" | "paused" | "stopped" | "error";

export type StatusVariant = SafetyLevel | OperationalStatus | "loading" | "inactive";

interface StatusBadgeProps {
  status: StatusVariant;
  label?: string;
  showIcon?: boolean;
  showLabel?: boolean; // backward-compat alias for showIcon
  size?: "sm" | "md" | "lg";
  className?: string;
}

const VARIANT_MAP: Record<StatusVariant, {
  defaultLabel: string;
  Icon: React.ElementType;
  cls: string;
}> = {
  normal:   { defaultLabel: ko.safety.normal,  Icon: CheckCircle,  cls: "bg-success-bg text-[#026B3F]" },
  caution:  { defaultLabel: ko.safety.caution, Icon: AlertTriangle, cls: "bg-warning-bg text-[#92400E]" },
  danger:   { defaultLabel: ko.safety.danger,  Icon: XCircle,      cls: "bg-danger-bg text-danger" },
  active:   { defaultLabel: "활성",            Icon: CheckCircle,  cls: "bg-success-bg text-[#026B3F]" },
  paused:   { defaultLabel: "일시정지",         Icon: Pause,        cls: "bg-warning-bg text-[#92400E]" },
  stopped:  { defaultLabel: "중지됨",          Icon: StopCircle,   cls: "bg-danger-bg text-danger" },
  error:    { defaultLabel: "오류",            Icon: XCircle,      cls: "bg-danger-bg text-danger" },
  inactive: { defaultLabel: "비활성",          Icon: Circle,       cls: "bg-bg-surface text-text-secondary border border-border" },
  loading:  { defaultLabel: "확인 중",         Icon: Circle,       cls: "bg-bg-surface text-text-secondary" },
};

const SIZE_MAP = {
  sm: "px-1.5 py-0.5 text-small gap-1",
  md: "px-2 py-1 text-caption gap-1.5",
  lg: "px-3 py-1.5 text-body gap-2",
};

export function StatusBadge({
  status,
  label,
  showIcon = true,
  showLabel,        // backward-compat: showLabel={false} → showIcon=false
  size = "md",
  className = "",
}: StatusBadgeProps) {
  const resolvedShowIcon = showLabel !== undefined ? showLabel : showIcon;
  const { defaultLabel, Icon, cls } = VARIANT_MAP[status] ?? VARIANT_MAP.inactive;
  const displayLabel = label ?? defaultLabel;
  const iconSize = size === "sm" ? 11 : size === "lg" ? 15 : 13;

  return (
    <span
      className={clsx(
        "inline-flex items-center rounded-[6px] font-medium",
        SIZE_MAP[size],
        cls,
        className,
      )}
      aria-label={`상태: ${displayLabel}`}
    >
      {resolvedShowIcon && status !== "loading" && (
        <Icon size={iconSize} aria-hidden />
      )}
      {status === "loading" && (
        <span className="w-2 h-2 rounded-full bg-current animate-pulse" aria-hidden />
      )}
      {displayLabel}
    </span>
  );
}
