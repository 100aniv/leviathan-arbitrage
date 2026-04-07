'use client';

import { useEffect, useRef } from 'react';

// ─── Types ────────────────────────────────────────────────────────────────────

interface MetricCardProps {
  label: string;
  value: string;
  /** Optional secondary label below value (e.g. "누적 수익 (USDT)") */
  sublabel?: string;
  /** Numeric delta for showing ▲/▼ change indicator */
  delta?: number;
  valueColor?: string;
  /** Extra Tailwind classes for the root element */
  className?: string;
  /** Show skeleton shimmer instead of value */
  loading?: boolean;
}

// ─── Component ────────────────────────────────────────────────────────────────

export function MetricCard({
  label,
  value,
  sublabel,
  delta,
  valueColor,
  className = '',
  loading = false,
}: MetricCardProps) {
  const valueRef = useRef<HTMLDivElement>(null);
  const prevValue = useRef(value);

  // Trigger count-up animation when value changes
  useEffect(() => {
    if (value !== prevValue.current && valueRef.current) {
      valueRef.current.classList.remove('animate-count-up');
      void valueRef.current.offsetWidth; // reflow to restart animation
      valueRef.current.classList.add('animate-count-up');
      prevValue.current = value;
    }
  }, [value]);

  const deltaColor =
    delta === undefined ? undefined
    : delta > 0 ? '#00C896'
    : delta < 0 ? '#FF4757'
    : '#888888';

  const deltaArrow =
    delta === undefined ? null
    : delta > 0 ? '▲'
    : delta < 0 ? '▼'
    : '—';

  return (
    <div
      className={`card group cursor-default select-none ${className}`}
    >
      {/* Label */}
      <div className="card-header">{label}</div>

      {/* Value */}
      {loading ? (
        <div className="skeleton h-7 w-24 mb-1" />
      ) : (
        <div
          ref={valueRef}
          className="stat-value animate-count-up"
          style={{ color: valueColor }}
        >
          {value}
        </div>
      )}

      {/* Sublabel + delta row */}
      <div className="flex items-center gap-2 mt-1 min-h-[1rem]">
        {sublabel && (
          <span className="text-[10px] font-mono text-terminal-subtle truncate">
            {sublabel}
          </span>
        )}
        {delta !== undefined && !loading && (
          <span
            className="text-[10px] font-mono tabular-nums ml-auto"
            style={{ color: deltaColor }}
          >
            {deltaArrow} {Math.abs(delta).toFixed(2)}%
          </span>
        )}
      </div>
    </div>
  );
}
