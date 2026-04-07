'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { useApi } from '@/hooks/useApi';
import { getPnl } from '@/lib/api';
import type { PnlResponse } from '@/types';

interface PnLPoint {
  time: string;
  total: number;
  realized: number;
  unrealized: number;
}

interface PnLChartProps {
  /** Optional real-time PnL from WebSocket — takes priority over REST poll */
  wsPnl?: { realized: number; unrealized: number; total: number } | null;
}

const COLORS = {
  total:      '#059669',
  realized:   '#3b82f6',
  unrealized: '#f59e0b',
};

const MAX_POINTS = 120;

// No seed data — show only real data from API/WS
function genSeedData(): PnLPoint[] {
  return [];
}

function fmt(v: number) {
  return `${v >= 0 ? '+' : ''}$${v.toFixed(2)}`;
}

export function PnLChart({ wsPnl }: PnLChartProps = {}) {
  const { data, error, isLoading, mutate } = useApi<PnlResponse>(
    '/api/v1/pnl',
    getPnl,
    { refreshInterval: 2000 },
  );

  const [history, setHistory] = useState<PnLPoint[]>(genSeedData);

  // Normalise REST response (realized_pnl / unrealized_pnl / total_pnl) to
  // the same shape as the WS pnl object (realized / unrealized / total).
  const restPoint: PnLPoint | null = data
    ? {
        time:       '',
        total:      data.total_pnl,
        realized:   data.realized_pnl,
        unrealized: data.unrealized_pnl,
      }
    : null;

  // Prefer WS data when it has real values; fall back to REST when WS is all zeros
  const wsHasData = wsPnl && (wsPnl.total !== 0 || wsPnl.realized !== 0 || wsPnl.unrealized !== 0);
  const livePoint = wsHasData ? wsPnl : (restPoint ?? wsPnl);

  useEffect(() => {
    if (!livePoint) return;
    setHistory(prev => [
      ...prev.slice(-(MAX_POINTS - 1)),
      {
        time: new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
        total:      livePoint.total,
        realized:   livePoint.realized,
        unrealized: livePoint.unrealized,
      },
    ]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [livePoint?.total, livePoint?.realized, livePoint?.unrealized]);

  const latest = useMemo(() => {
    if (livePoint) return livePoint;
    if (history.length === 0) return null;
    const last = history[history.length - 1];
    return { total: last.total, realized: last.realized, unrealized: last.unrealized };
  }, [livePoint, history]);

  if (error) {
    return (
      <div className="bg-terminal-surface border border-terminal-border p-4">
        <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle block mb-4">
          PnL Curve
        </span>
        <div className="flex flex-col items-center justify-center py-12 gap-3">
          <p className="text-xs font-mono text-loss">연결에 실패했어요</p>
          <button
            onClick={() => mutate()}
            aria-label="PnL 차트 다시 불러오기"
            className="text-[10px] font-mono border border-terminal-border px-3 py-1 text-terminal-subtle hover:text-terminal-text transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            다시 시도
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-terminal-surface border border-terminal-border p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">
          PnL Curve
        </span>
        {!isLoading && (
          <span className="text-[9px] font-mono text-profit animate-pulse">● LIVE</span>
        )}
      </div>

      {/* Summary cards */}
      {latest && (
        <div className="grid grid-cols-3 gap-2 mb-4">
          {(
            [
              { label: 'Total',      value: latest.total,      color: COLORS.total },
              { label: 'Realized',   value: latest.realized,   color: COLORS.realized },
              { label: 'Unrealized', value: latest.unrealized, color: COLORS.unrealized },
            ] as const
          ).map(({ label, value, color }) => (
            <div key={label} className="bg-terminal-bg border border-terminal-border p-2">
              <div className="text-[10px] font-mono text-terminal-subtle uppercase tracking-wider mb-1" style={{ color }}>
                {label}
              </div>
              <div
                className="text-sm font-mono tabular-nums font-semibold"
                style={{ color: value >= 0 ? '#059669' : '#DC2626' }}
              >
                {fmt(value)}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Chart */}
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={history} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" vertical={false} />
          <XAxis
            dataKey="time"
            tick={{ fill: '#6B7280', fontSize: 9, fontFamily: 'monospace' }}
            tickLine={false}
            axisLine={{ stroke: '#E5E7EB' }}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fill: '#6B7280', fontSize: 9, fontFamily: 'monospace' }}
            tickLine={false}
            axisLine={false}
            tickFormatter={v => `$${v}`}
            width={52}
          />
          <Tooltip
            contentStyle={{
              background: '#FFFFFF',
              border: '1px solid #E5E7EB',
              borderRadius: 0,
              fontFamily: 'monospace',
              fontSize: 11,
            }}
            labelStyle={{ color: '#6B7280', fontSize: 10 }}
            formatter={(value: number | undefined, name: string | undefined) => [value != null ? fmt(value) : '—', name ?? '']}
          />
          <Line type="monotone" dataKey="total"      stroke={COLORS.total}      dot={false} strokeWidth={1.5} isAnimationActive={false} />
          <Line type="monotone" dataKey="realized"   stroke={COLORS.realized}   dot={false} strokeWidth={1}   isAnimationActive={false} />
          <Line type="monotone" dataKey="unrealized" stroke={COLORS.unrealized} dot={false} strokeWidth={1}   isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>

      {/* Legend */}
      <div className="flex items-center gap-4 mt-2">
        {(Object.entries(COLORS) as [string, string][]).map(([key, color]) => (
          <div key={key} className="flex items-center gap-1">
            <div className="w-4 h-0.5" style={{ background: color }} />
            <span className="text-[10px] font-mono text-terminal-subtle capitalize">{key}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
