import React from "react";
import clsx from "clsx";

interface PageHeaderProps {
  title: string;
  subtitle?: string;
  lastUpdated?: string | Date | null;
  right?: React.ReactNode;
  className?: string;
}

/**
 * Path-B v2 W3 page header — title + subtitle + last_updated timestamp.
 * Used across 8 operator dashboard pages (DESIGN.md §5).
 */
export function PageHeader({
  title,
  subtitle,
  lastUpdated,
  right,
  className = "",
}: PageHeaderProps) {
  const updatedLabel = formatUpdatedLabel(lastUpdated);

  return (
    <div
      className={clsx(
        "flex flex-wrap items-start justify-between gap-3 pb-4 mb-4 border-b border-border",
        className,
      )}
    >
      <div className="min-w-0">
        <h1 className="text-title font-semibold text-text-primary tracking-tight">
          {title}
        </h1>
        {subtitle && (
          <p className="text-caption text-text-secondary mt-1">{subtitle}</p>
        )}
      </div>

      <div className="flex items-center gap-3">
        {updatedLabel && (
          <span
            className="text-small font-mono text-text-tertiary tabular-nums"
            aria-label={`마지막 갱신: ${updatedLabel}`}
          >
            updated · {updatedLabel}
          </span>
        )}
        {right}
      </div>
    </div>
  );
}

function formatUpdatedLabel(value: string | Date | null | undefined): string | null {
  if (!value) return null;
  try {
    const d = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(d.getTime())) return null;
    return d.toLocaleTimeString("ko-KR", { hour12: false });
  } catch {
    return null;
  }
}
