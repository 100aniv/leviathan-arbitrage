'use client';

import { useEffect, useState } from 'react';
import {
  XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine,
  AreaChart, Area, PieChart, Pie, Cell,
} from 'recharts';
import { EquityCurve } from '@/components/EquityCurve';
import { getPortfolioMetrics, getPortfolioSummary, getEquityCurve, getPositions, getDailyReturns, getShadowStats } from '@/lib/api';
import type { Position } from '@/types';

interface PortfolioMetrics {
  sharpe_ratio:      number | null;
  max_drawdown_pct:  number;
  calmar_ratio:      number | null;
  win_rate:          number;
  total_trades:      number;
  total_pnl:         number;
}

interface ExchangeBalance {
  exchange_id:   string;
  balance_usdt:  number;
  pct_of_total:  number;
  connected:     boolean;
}

interface PortfolioSummary {
  total_balance_usdt: number;
  exchange_balances:  ExchangeBalance[];
}

interface CurvePoint {
  date: string;
  equity: number;
  btc_benchmark: number | null;
}

interface DrawdownPoint {
  date: string;
  drawdown: number;
}

const PIE_COLORS = [
  '#22c55e', '#3b82f6', '#eab308', '#ef4444',
  '#a855f7', '#06b6d4', '#f97316', '#ec4899',
];

// ─── 드로다운 Chart ───────────────────────────────────────────────────────────

function DrawdownChart({ data }: { data: DrawdownPoint[] }) {
  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center h-28 text-xs font-mono text-terminal-subtle">
        에쿼티 커브 데이터 없음 — Paper/Shadow/Live 운영 후 표시됩니다
      </div>
    );
  }

  const minDD = Math.min(...data.map((d) => d.drawdown));

  return (
    <ResponsiveContainer width="100%" height={120}>
      <AreaChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
        <defs>
          <linearGradient id="ddGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%"  stopColor="#ff4d4d" stopOpacity={0.4} />
            <stop offset="95%" stopColor="#ff4d4d" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <XAxis
          dataKey="date"
          tick={{ fontSize: 9, fontFamily: 'JetBrains Mono, monospace', fill: '#666' }}
          axisLine={false}
          tickLine={false}
          tickFormatter={(v: string) => v.slice(5)}
          interval="preserveStartEnd"
        />
        <YAxis
          tick={{ fontSize: 9, fontFamily: 'JetBrains Mono, monospace', fill: '#666' }}
          axisLine={false}
          tickLine={false}
          tickFormatter={(v: number) => `${v.toFixed(1)}%`}
          width={40}
          domain={[minDD * 1.1, 0]}
        />
        <Tooltip
          contentStyle={{
            background: '#1a1a1a',
            border: '1px solid #333',
            borderRadius: 0,
            fontSize: 11,
            fontFamily: 'JetBrains Mono, monospace',
          }}
          formatter={(v: number | undefined) => [v != null ? `${v.toFixed(3)}%` : '—', '드로다운']}
        />
        <ReferenceLine y={0} stroke="#333" strokeDasharray="3 3" />
        <Area
          type="monotone"
          dataKey="drawdown"
          stroke="#ff4d4d"
          strokeWidth={1.5}
          fill="url(#ddGradient)"
          dot={false}
          activeDot={{ r: 3, fill: '#ff4d4d' }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

// ─── Exposure Heatmap ─────────────────────────────────────────────────────────

function exposureColor(pct: number): string {
  if (pct <= 0) return 'rgba(80,80,80,0.15)';
  const alpha = Math.min(pct / 30, 1) * 0.75 + 0.08;
  return `rgba(59,130,246,${alpha})`;
}

interface ExposureHeatmapProps {
  positions: Position[];
}

function ExposureHeatmap({ positions }: ExposureHeatmapProps) {
  if (positions.length === 0) {
    return (
      <div className="flex items-center justify-center h-24 text-xs font-mono text-terminal-subtle">
        활성 포지션 없음
      </div>
    );
  }

  const strategies = Array.from(new Set(positions.map((p) => p.strategy_id)));
  const exchanges  = Array.from(new Set(positions.map((p) => p.exchange_id)));

  // Compute USD exposure per (strategy, exchange)
  const matrix: Record<string, Record<string, number>> = {};
  let totalExposure = 0;
  for (const pos of positions) {
    const usd = Math.abs((pos.quantity ?? 0) * (pos.mark_price ?? pos.entry_price ?? 1));
    if (!matrix[pos.strategy_id]) matrix[pos.strategy_id] = {};
    matrix[pos.strategy_id][pos.exchange_id] = (matrix[pos.strategy_id][pos.exchange_id] ?? 0) + usd;
    totalExposure += usd;
  }

  return (
    <div className="overflow-x-auto">
      <div className="min-w-[400px]">
        {/* Exchange header */}
        <div className="flex ml-28 mb-1">
          {exchanges.map((ex) => (
            <div key={ex} className="flex-1 text-center text-[9px] font-mono text-terminal-subtle truncate px-0.5">
              {ex.replace('_', ' ')}
            </div>
          ))}
        </div>
        {/* Rows */}
        {strategies.map((strat) => (
          <div key={strat} className="flex items-center mb-0.5">
            <div className="w-28 text-right pr-2 text-[9px] font-mono text-terminal-subtle truncate shrink-0">
              {strat.replace(/_/g, ' ')}
            </div>
            {exchanges.map((ex) => {
              const usd = matrix[strat]?.[ex] ?? 0;
              const pct = totalExposure > 0 ? (usd / totalExposure) * 100 : 0;
              return (
                <div
                  key={ex}
                  className="flex-1 h-6 mx-px flex items-center justify-center"
                  style={{ backgroundColor: exposureColor(pct) }}
                  title={`${strat} × ${ex}: $${usd.toFixed(0)} (${pct.toFixed(1)}%)`}
                >
                  {pct > 5 && (
                    <span className="text-[8px] font-mono text-terminal-text tabular-nums">
                      {pct.toFixed(0)}%
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        ))}
        {/* Legend */}
        <div className="flex items-center gap-2 mt-2 ml-28">
          <div className="w-16 h-2.5" style={{ background: 'linear-gradient(to right, rgba(80,80,80,0.15), rgba(59,130,246,0.83))' }} />
          <span className="text-[9px] font-mono text-terminal-subtle">0% → 30%+ exposure</span>
        </div>
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function PortfolioPage() {
  const [metrics,   setMetrics]   = useState<PortfolioMetrics | null>(null);
  const [summary,   setSummary]   = useState<PortfolioSummary | null>(null);
  const [curve,     setCurve]     = useState<CurvePoint[]>([]);
  const [drawdown,  setDrawdown]  = useState<DrawdownPoint[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [dailyReturns, setDailyReturns] = useState<{date: string; pnl: number}[]>([]);
  const [shadowByStrategy, setShadowByStrategy] = useState<{strategy_id: string; trades: number; pnl: number}[]>([]);

  useEffect(() => {
    async function load() {
      try {
        const [metricsData, summaryData, curveData, positionsData, dailyData, shadowData] = await Promise.all([
          getPortfolioMetrics().catch(() => null),
          getPortfolioSummary().catch(() => null),
          getEquityCurve().catch(() => null),
          getPositions().catch(() => null),
          getDailyReturns().catch(() => null),
          getShadowStats().catch(() => null),
        ]);
        if (metricsData) setMetrics(metricsData as PortfolioMetrics);
        if (summaryData) setSummary(summaryData);
        if (positionsData) setPositions(positionsData);
        if (dailyData) {
          const returns = Array.isArray(dailyData) ? dailyData : (dailyData as any)?.returns ?? [];
          setDailyReturns(returns);
        }
        if (shadowData?.by_strategy) {
          const bs = Array.isArray(shadowData.by_strategy) ? shadowData.by_strategy : [];
          setShadowByStrategy(bs);
        }

        if (curveData?.curve && curveData.curve.length > 0) {
          const pts = curveData.curve;
          setCurve(pts);

          // Compute drawdown series from equity curve
          let peak = pts[0].equity;
          const dd: DrawdownPoint[] = pts.map((p) => {
            if (p.equity > peak) peak = p.equity;
            const dd_pct = peak > 0 ? ((p.equity - peak) / peak) * 100 : 0;
            return { date: p.date, drawdown: dd_pct };
          });
          setDrawdown(dd);
        }
      } catch { /* ignore — engine may be offline */ }
    }
    load();
    const interval = setInterval(load, 10_000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-mono font-semibold text-terminal-text">Portfolio</h2>

      {/* 에쿼티 커브 */}
      <EquityCurve data={curve} metrics={metrics ?? undefined} />

      {/* Risk Metric Cards */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
        {[
          { label: '샤프 비율', value: metrics?.sharpe_ratio?.toFixed(2) ?? '—' },
          { label: '최대 낙폭', value: metrics ? `${metrics.max_drawdown_pct.toFixed(2)}%` : '—' },
          { label: '칼마 비율', value: metrics?.calmar_ratio?.toFixed(2) ?? '—' },
          { label: '승률',     value: metrics ? `${(metrics.win_rate * 100).toFixed(1)}%` : '—' },
        ].map(({ label, value }) => (
          <div key={label} className="bg-terminal-surface border border-terminal-border p-3">
            <div className="text-[10px] font-mono text-terminal-subtle uppercase tracking-wider">{label}</div>
            <div className="text-lg font-mono text-terminal-text mt-1 tabular-nums">{value}</div>
          </div>
        ))}
      </div>

      {/* 자산 배분 — Donut Chart */}
      <div className="bg-terminal-surface border border-terminal-border p-4">
        <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">자산 배분</span>
        {summary?.exchange_balances && summary.exchange_balances.length > 0 ? (
          <div className="flex items-center gap-6 mt-3">
            <div className="w-48 h-48 shrink-0">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={summary.exchange_balances.map(eb => ({
                      name: eb.exchange_id,
                      value: eb.balance_usdt,
                      pct: eb.pct_of_total,
                    }))}
                    cx="50%"
                    cy="50%"
                    innerRadius={45}
                    outerRadius={75}
                    paddingAngle={2}
                    dataKey="value"
                    stroke="none"
                  >
                    {summary.exchange_balances.map((_, i) => (
                      <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{ background: '#1a1a1a', border: '1px solid #333', borderRadius: 0, fontSize: 11, fontFamily: 'JetBrains Mono' }}
                    formatter={(value: number, name: string) => [`$${value.toLocaleString()}`, name]}
                  />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <div className="flex flex-col gap-1.5 flex-1">
              {summary.exchange_balances.map((eb, i) => (
                <div key={eb.exchange_id} className="flex items-center gap-2">
                  <div className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ backgroundColor: PIE_COLORS[i % PIE_COLORS.length] }} />
                  <span className="text-[10px] font-mono text-terminal-text flex-1 truncate">{eb.exchange_id}</span>
                  <span className="text-[10px] font-mono text-terminal-subtle tabular-nums">{(eb.pct_of_total * 100).toFixed(1)}%</span>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-center h-24 mt-2">
            <span className="text-xs font-mono text-terminal-subtle">거래소 연결 대기 중...</span>
          </div>
        )}
      </div>

      {/* 일별 수익 */}
      <div className="bg-terminal-surface border border-terminal-border p-4">
        <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">일별 수익</span>
        {dailyReturns.length > 0 ? (
          <div className="mt-3">
            <ResponsiveContainer width="100%" height={100}>
              <AreaChart data={dailyReturns} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
                <XAxis dataKey="date" tick={{ fontSize: 9, fontFamily: 'JetBrains Mono', fill: '#666' }} axisLine={false} tickLine={false} tickFormatter={(v: string) => v.slice(5)} />
                <YAxis tick={{ fontSize: 9, fontFamily: 'JetBrains Mono', fill: '#666' }} axisLine={false} tickLine={false} tickFormatter={(v: number) => `$${v.toFixed(0)}`} width={45} />
                <Tooltip contentStyle={{ background: '#1a1a1a', border: '1px solid #333', borderRadius: 0, fontSize: 11, fontFamily: 'JetBrains Mono' }} formatter={(v: number) => [`$${v.toFixed(2)}`, 'PnL']} />
                <ReferenceLine y={0} stroke="#333" strokeDasharray="3 3" />
                <Area type="monotone" dataKey="pnl" stroke="#00ff88" strokeWidth={1.5} fill="rgba(0,255,136,0.1)" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <div className="flex items-center justify-center h-24 mt-2">
            <span className="text-xs font-mono text-terminal-subtle">Paper/Shadow/Live 운영 이후 누적 데이터가 표시됩니다</span>
          </div>
        )}
      </div>

      {/* Strategy PnL Breakdown */}
      {shadowByStrategy.length > 0 && (
        <div className="bg-terminal-surface border border-terminal-border p-4">
          <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">전략별 수익 기여</span>
          <div className="mt-3 space-y-2">
            {shadowByStrategy.sort((a, b) => b.pnl - a.pnl).map((s) => {
              const maxPnl = Math.max(...shadowByStrategy.map(x => Math.abs(x.pnl)), 0.01);
              const w = Math.min(Math.abs(s.pnl) / maxPnl * 100, 100);
              return (
                <div key={s.strategy_id} className="flex items-center gap-2">
                  <span className="text-[10px] font-mono text-terminal-subtle w-28 shrink-0 truncate">{s.strategy_id.replace(/_v\d+$/, '')}</span>
                  <div className="flex-1 h-4 bg-terminal-muted/20 overflow-hidden">
                    <div className="h-full transition-all" style={{ width: `${w}%`, backgroundColor: s.pnl >= 0 ? '#00ff88' : '#ff4d4d', opacity: 0.7 }} />
                  </div>
                  <span className="text-[10px] font-mono tabular-nums w-20 text-right" style={{ color: s.pnl >= 0 ? '#00ff88' : '#ff4d4d' }}>
                    {s.pnl >= 0 ? '+' : ''}${s.pnl.toFixed(2)}
                  </span>
                  <span className="text-[9px] font-mono text-terminal-subtle w-12 text-right">{s.trades}t</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 드로다운 Chart */}
      <div className="bg-terminal-surface border border-terminal-border p-4">
        <div className="mb-3">
          <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">드로다운</span>
          {metrics && (
            <span className="ml-2 text-[10px] font-mono text-terminal-subtle">
              최대 <span style={{ color: '#ff4d4d' }}>{metrics.max_drawdown_pct.toFixed(2)}%</span>
            </span>
          )}
        </div>
        <DrawdownChart data={drawdown} />
      </div>

      {/* Strategy × Exchange Exposure Heatmap */}
      <div className="bg-terminal-surface border border-terminal-border p-4">
        <div className="mb-3">
          <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">
            Exposure 히트맵
          </span>
          <span className="ml-2 text-[10px] font-mono text-terminal-subtle">전략 × 거래소</span>
        </div>
        <ExposureHeatmap positions={positions} />
      </div>
    </div>
  );
}
