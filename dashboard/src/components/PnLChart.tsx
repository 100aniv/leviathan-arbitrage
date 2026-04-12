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
      <div className="card">
        <span className="card-header block">PNL 추이</span>
        <div className="flex flex-col items-center justify-center py-12 gap-3">
          <p className="text-small text-loss">연결에 실패했어요</p>
          <button
            onClick={() => mutate()}
            aria-label="PnL 차트 다시 불러오기"
            className="text-small border border-border px-3 py-1 text-text-tertiary hover:text-text-primary transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
          >
            다시 시도
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <span className="card-header mb-0">
          PNL 추이
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
              { label: '합산',      value: latest.total,      color: COLORS.total },
              { label: '실현',   value: latest.realized,   color: COLORS.realized },
              { label: '미실현', value: latest.unrealized, color: COLORS.unrealized },
            ] as const
          ).map(({ label, value, color }) => (
            <div key={label} className="bg-bg-base border border-border rounded-[10px] p-2.5">
              <div className="text-small font-medium uppercase tracking-wider mb-1" style={{ color }}>
                {label}
              </div>
              <div
                className="text-body font-semibold tabular-nums"
                style={{ color: value >= 0 ? '#149E61' : '#E5484D' }}
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
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border, #DEDEE5)" vertical={false} />
          <XAxis
            dataKey="time"
            tick={{ fill: 'var(--color-text-tertiary, #9497A9)', fontSize: 10 }}
            tickLine={false}
            axisLine={{ stroke: 'var(--color-border, #DEDEE5)' }}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fill: 'var(--color-text-tertiary, #9497A9)', fontSize: 10 }}
            tickLine={false}
            axisLine={false}
            tickFormatter={v => `$${v}`}
            width={52}
          />
          <Tooltip
            contentStyle={{
              background: 'var(--color-bg-elevated, #FFFFFF)',
              border: '1px solid var(--color-border, #DEDEE5)',
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: 'var(--color-text-secondary, #686B82)', fontSize: 11 }}
            formatter={(value: number | undefined, name: string | undefined) => [value != null ? fmt(value) : '—', name ?? '']}
          />
          <Line type="monotone" dataKey="total"      name="합산"   stroke={COLORS.total}      dot={false} strokeWidth={1.5} isAnimationActive={false} />
          <Line type="monotone" dataKey="realized"   name="실현"   stroke={COLORS.realized}   dot={false} strokeWidth={1}   isAnimationActive={false} />
          <Line type="monotone" dataKey="unrealized" name="미실현" stroke={COLORS.unrealized} dot={false} strokeWidth={1}   isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>

      {/* Legend */}
      <div className="flex items-center gap-4 mt-2">
        {([['total', '합산', COLORS.total], ['realized', '실현', COLORS.realized], ['unrealized', '미실현', COLORS.unrealized]] as const).map(([, label, color]) => (
          <div key={label} className="flex items-center gap-1">
            <div className="w-4 h-0.5" style={{ background: color }} />
            <span className="text-small text-text-tertiary">{label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
