'use client';

import { useEffect, useRef, useState } from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { useEngineWs } from '@/hooks/useEngineWs';
import type { SessionPnlPoint } from '@/types';

const MAX_POINTS        = 500;
const SAMPLE_INTERVAL_MS = 5_000;

interface ChartPoint {
  time: string;
  pnl: number;
  wr: number;
}

export function PerformanceTrend() {
  const { data } = useEngineWs();
  const [points, setPoints] = useState<SessionPnlPoint[]>([]);
  const lastSampleRef = useRef<number>(0);

  // Accumulate one point every 5 seconds from WS state_update
  useEffect(() => {
    if (!data) return;
    const now = Date.now();
    if (now - lastSampleRef.current < SAMPLE_INTERVAL_MS) return;
    lastSampleRef.current = now;

    const winRate = data.shadow_stats?.win_rate ?? 0;
    setPoints(prev => [
      ...prev.slice(-(MAX_POINTS - 1)),
      { timestamp: now, pnl: data.pnl?.total ?? 0, win_rate: winRate },
    ]);
  }, [data]);

  const chartData: ChartPoint[] = points.map(p => ({
    time: new Date(p.timestamp).toLocaleTimeString('en-GB', {
      hour: '2-digit',
      minute: '2-digit',
    }),
    pnl: p.pnl,
    wr:  parseFloat((p.win_rate * 100).toFixed(1)),
  }));

  if (points.length < 2) {
    return (
      <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
        <div className="flex items-center justify-between mb-3">
          <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">
            Session Trend
          </span>
          <span className="text-[9px] font-mono text-terminal-subtle animate-pulse">● COLLECTING</span>
        </div>
        <div className="flex items-center justify-center h-[120px]">
          <p className="text-xs font-mono text-terminal-subtle">Accumulating data...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">
          Session Trend
        </span>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1">
            <div className="w-4 h-0.5" style={{ background: '#059669' }} />
            <span className="text-[10px] font-mono text-terminal-subtle">PnL</span>
          </div>
          <div className="flex items-center gap-1">
            <div className="w-4 h-0.5" style={{ background: '#3b82f6' }} />
            <span className="text-[10px] font-mono text-terminal-subtle">WR%</span>
          </div>
        </div>
      </div>

      <ResponsiveContainer width="100%" height={120}>
        <AreaChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <XAxis
            dataKey="time"
            tick={{ fill: '#6B7280', fontSize: 9, fontFamily: 'monospace' }}
            tickLine={false}
            axisLine={{ stroke: '#E5E7EB' }}
            interval="preserveStartEnd"
          />
          <YAxis hide />
          <Tooltip
            contentStyle={{
              background: '#FFFFFF',
              border: '1px solid #E5E7EB',
              borderRadius: 0,
              fontFamily: 'monospace',
              fontSize: 11,
            }}
            labelStyle={{ color: '#6B7280', fontSize: 10 }}
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            formatter={(value: any, name: any) => {
              const v = +(value ?? 0);
              return [name === 'pnl' ? `$${v.toFixed(2)}` : `${v}%`, name === 'pnl' ? 'PnL' : 'Win Rate'];
            }}
          />
          <Area
            type="monotone"
            dataKey="pnl"
            stroke="#059669"
            fill="rgba(5,150,105,0.1)"
            dot={false}
            strokeWidth={1.5}
            isAnimationActive={false}
          />
          <Area
            type="monotone"
            dataKey="wr"
            stroke="#3b82f6"
            fill="rgba(59,130,246,0.05)"
            dot={false}
            strokeWidth={1}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
