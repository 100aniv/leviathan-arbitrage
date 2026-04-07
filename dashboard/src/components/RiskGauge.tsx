'use client';

import Link from 'next/link';
import { useApi } from '@/hooks/useApi';
import { getRiskMetrics } from '@/lib/api';
import type { RiskMetrics } from '@/types';

const RADIUS    = 90;
const CX        = 100;
const CY        = 100;
const TOTAL_ARC = Math.PI * RADIUS; // ≈ 282.74

function arcColor(pct: number): string {
  if (pct > 15) return '#DC2626';
  if (pct > 5)  return '#f59e0b';
  return '#059669';
}

function GaugeArc({ pct }: { pct: number }) {
  const clamped = Math.min(Math.max(pct, 0), 100);
  const offset  = TOTAL_ARC * (1 - clamped / 100);
  const color   = arcColor(clamped);
  const d       = `M ${CX - RADIUS},${CY} A ${RADIUS},${RADIUS} 0 0,1 ${CX + RADIUS},${CY}`;

  return (
    <svg viewBox="0 0 200 110" className="w-full max-w-[200px]" aria-label={`Drawdown gauge: ${clamped.toFixed(1)}%`}>
      {/* Track */}
      <path d={d} fill="none" stroke="#E5E7EB" strokeWidth={14} strokeLinecap="round" />
      {/* Progress arc */}
      <path
        d={d}
        fill="none"
        stroke={color}
        strokeWidth={14}
        strokeLinecap="round"
        strokeDasharray={TOTAL_ARC}
        strokeDashoffset={offset}
        style={{ transition: 'stroke-dashoffset 0.5s ease, stroke 0.3s ease' }}
      />
      {/* Percentage */}
      <text
        x={CX}
        y={82}
        textAnchor="middle"
        fill={color}
        fontSize={26}
        fontFamily="'JetBrains Mono', monospace"
        fontWeight={600}
        style={{ transition: 'fill 0.3s ease' }}
      >
        {clamped.toFixed(1)}%
      </text>
      {/* Label */}
      <text x={CX} y={98} textAnchor="middle" fill="#6B7280" fontSize={9} fontFamily="monospace" letterSpacing="1">
        MAX DRAWDOWN
      </text>
    </svg>
  );
}

export function RiskGauge() {
  const { data, error, mutate } = useApi<RiskMetrics>(
    '/risk/metrics',
    getRiskMetrics,
    { refreshInterval: 5000 },
  );

  return (
    <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">Risk Gauge</span>
        <Link href="/risk" className="text-[10px] font-mono text-terminal-subtle hover:text-accent transition-colors">
          View all →
        </Link>
      </div>

      {error ? (
        <div className="flex flex-col items-center gap-2 py-6">
          <p className="text-xs font-mono text-loss">Failed to load risk metrics</p>
          <button
            onClick={() => mutate()}
            aria-label="리스크 지표 다시 불러오기"
            className="text-[10px] font-mono border border-terminal-border px-3 py-1 text-terminal-subtle hover:text-terminal-text transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            Retry
          </button>
        </div>
      ) : (
        <div className="flex flex-col items-center gap-4">
          <GaugeArc pct={data?.max_drawdown_pct ?? 0} />

          {/* Badges */}
          <div className="flex gap-4 flex-wrap justify-center">
            <div className="flex flex-col items-center gap-1">
              <span className="text-[10px] font-mono text-terminal-subtle">Kill Switch</span>
              {data?.kill_switch_active
                ? <span className="badge-loss">ACTIVE</span>
                : <span className="badge-profit">STANDBY</span>
              }
            </div>

            <div className="flex flex-col items-center gap-1">
              <span className="text-[10px] font-mono text-terminal-subtle">Circuit Breaker</span>
              {data?.circuit_breaker_state === 'OPEN'
                ? <span className="badge-loss">OPEN</span>
                : data?.circuit_breaker_state === 'HALF_OPEN'
                ? <span className="badge-warn">HALF_OPEN</span>
                : <span className="badge-accent">{data?.circuit_breaker_state ?? 'CLOSED'}</span>
              }
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
