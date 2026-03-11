'use client';

import { useEffect, useState } from 'react';
import { getTCASummary } from '@/lib/api';
import type { TCASummary } from '@/types';

function MetricCard({ label, value, unit, color = '#00ff88' }: {
  label: string;
  value: string;
  unit: string;
  color?: string;
}) {
  return (
    <div className="space-y-1">
      <span className="text-[10px] font-mono uppercase tracking-wider text-terminal-subtle">
        {label}
      </span>
      <div className="flex items-baseline gap-1">
        <span className="text-lg font-mono tabular-nums" style={{ color }}>
          {value}
        </span>
        <span className="text-[10px] font-mono text-terminal-subtle">{unit}</span>
      </div>
    </div>
  );
}

export function TCAWidget() {
  const [data, setData] = useState<TCASummary | null>(null);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const result = await getTCASummary();
        if (active) setData(result);
      } catch {
        // API not available — show empty state
      }
    };
    load();
    const interval = setInterval(load, 5000);
    return () => { active = false; clearInterval(interval); };
  }, []);

  const empty = !data || data.sample_count === 0;

  return (
    <div className="bg-terminal-surface border border-terminal-border p-4">
      <div className="flex items-center justify-between mb-4">
        <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">
          실행 품질 (TCA)
        </span>
        <span className="text-[10px] font-mono text-terminal-subtle">
          {empty ? 'No data' : `${data!.sample_count} fills`}
        </span>
      </div>

      {empty ? (
        <p className="text-xs font-mono text-terminal-subtle text-center py-4">
          거래 실행 데이터가 아직 없습니다
        </p>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="space-y-3">
            <span className="text-[10px] font-mono uppercase tracking-wider text-terminal-subtle block border-b border-terminal-border pb-1">
              Implementation Shortfall
            </span>
            <MetricCard label="P50" value={data!.is_p50_bps.toFixed(1)} unit="bps" color="#00ff88" />
            <MetricCard label="P95" value={data!.is_p95_bps.toFixed(1)} unit="bps" color={data!.is_p95_bps > 10 ? '#f59e0b' : '#00ff88'} />
          </div>
          <div className="space-y-3">
            <span className="text-[10px] font-mono uppercase tracking-wider text-terminal-subtle block border-b border-terminal-border pb-1">
              실행 레이턴시
            </span>
            <MetricCard label="P50" value={data!.latency_p50_ms.toFixed(0)} unit="ms" color="#3b82f6" />
            <MetricCard label="P95" value={data!.latency_p95_ms.toFixed(0)} unit="ms" color={data!.latency_p95_ms > 500 ? '#f59e0b' : '#3b82f6'} />
            <MetricCard label="P99" value={data!.latency_p99_ms.toFixed(0)} unit="ms" color={data!.latency_p99_ms > 1000 ? '#ef4444' : '#3b82f6'} />
          </div>
          <div className="space-y-3">
            <span className="text-[10px] font-mono uppercase tracking-wider text-terminal-subtle block border-b border-terminal-border pb-1">
              체결률
            </span>
            <MetricCard
              label="Fill Rate"
              value={data!.fill_rate_pct.toFixed(1)}
              unit="%"
              color={data!.fill_rate_pct >= 90 ? '#00ff88' : data!.fill_rate_pct >= 70 ? '#f59e0b' : '#ef4444'}
            />
          </div>
        </div>
      )}
    </div>
  );
}
