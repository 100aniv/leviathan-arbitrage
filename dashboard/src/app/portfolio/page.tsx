'use client';

import { useEffect, useState, type ReactNode } from 'react';
import {
  XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine,
  AreaChart, Area, PieChart, Pie, Cell,
} from 'recharts';
import { EquityCurve } from '@/components/EquityCurve';
import { getPortfolioMetrics, getPortfolioSummary, getEquityCurve, getPositions, getDailyReturns, getShadowStats, getLivePositions } from '@/lib/api';
import type { LivePositionsResponse } from '@/lib/api';
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
  '#149E61', '#3b82f6', '#F59E0B', '#E5484D',
  '#a855f7', '#06b6d4', '#f97316', '#ec4899',
];

// ─── Drawdown Chart ───────────────────────────────────────────────────────────

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
            <stop offset="5%"  stopColor="#E5484D" stopOpacity={0.4} />
            <stop offset="95%" stopColor="#E5484D" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <XAxis
          dataKey="date"
          tick={{ fontSize: 9, fontFamily: 'IBM Plex Mono, monospace', fill: '#686B82' }}
          axisLine={false}
          tickLine={false}
          tickFormatter={(v: string) => v.slice(5)}
          interval="preserveStartEnd"
        />
        <YAxis
          tick={{ fontSize: 9, fontFamily: 'IBM Plex Mono, monospace', fill: '#686B82' }}
          axisLine={false}
          tickLine={false}
          tickFormatter={(v: number) => `${v.toFixed(1)}%`}
          width={40}
          domain={[minDD * 1.1, 0]}
        />
        <Tooltip
          contentStyle={{
            background: '#FFFFFF',
            border: '1px solid #DEDEE5',
            borderRadius: 0,
            fontSize: 11,
            fontFamily: 'IBM Plex Mono, monospace',
          }}
          formatter={(v: number | undefined) => [v != null ? `${v.toFixed(3)}%` : '—', '드로다운']}
        />
        <ReferenceLine y={0} stroke="#DEDEE5" strokeDasharray="3 3" />
        <Area
          type="monotone"
          dataKey="drawdown"
          stroke="#E5484D"
          strokeWidth={1.5}
          fill="url(#ddGradient)"
          dot={false}
          activeDot={{ r: 3, fill: '#E5484D' }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

// ─── Exposure Heatmap ─────────────────────────────────────────────────────────

function exposureColor(pct: number): string {
  if (pct <= 0) return 'rgba(104,107,130,0.08)';
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
        <div className="flex ml-28 mb-1">
          {exchanges.map((ex) => (
            <div key={ex} className="flex-1 text-center text-[9px] font-mono text-terminal-subtle truncate px-0.5">
              {ex.replace('_', ' ')}
            </div>
          ))}
        </div>
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
        <div className="flex items-center gap-2 mt-2 ml-28">
          <div className="w-16 h-2.5" style={{ background: 'linear-gradient(to right, rgba(104,107,130,0.08), rgba(59,130,246,0.83))' }} />
          <span className="text-[9px] font-mono text-terminal-subtle">0% → 30%+ exposure</span>
        </div>
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

// ─── Cross-Exchange Live Positions Panel ──────────────────────────────────────

function LivePositionsPanel({ data }: { data: LivePositionsResponse | null }) {
  if (!data) {
    return (
      <div className="flex items-center justify-center h-20 text-xs font-mono text-terminal-subtle">
        거래소 연결 중... (엔진 실행 필요)
      </div>
    );
  }

  const pnlColor = (v: number) => v > 0 ? 'text-profit' : v < 0 ? 'text-loss' : 'text-terminal-subtle';
  const fmt = (v: number) => `${v >= 0 ? '+' : ''}$${v.toFixed(4)}`;

  return (
    <div className="space-y-3">
      {/* 요약 */}
      <div className="grid grid-cols-2 gap-3">
        {data.exchanges.map((ex) => (
          <div key={ex.exchange_id} className="bg-terminal-muted/10 border border-terminal-border/40 p-2.5">
            <div className="text-[9px] font-mono text-terminal-subtle uppercase tracking-wider">{ex.exchange_id.replace('_', ' ')}</div>
            <div className="text-base font-mono text-terminal-text tabular-nums mt-0.5">
              ${ex.balance_usdt.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </div>
            {ex.error && <div className="text-[9px] text-loss font-mono mt-1 truncate">{ex.error}</div>}
          </div>
        ))}
      </div>

      {/* 합산 미실현 손익 */}
      <div className="flex items-center justify-between px-1">
        <span className="text-[10px] font-mono text-terminal-subtle">합산 미실현 손익</span>
        <span className={`text-sm font-mono tabular-nums ${pnlColor(data.total_unrealized_pnl)}`}>
          {fmt(data.total_unrealized_pnl)}
        </span>
      </div>

      {/* 헤지 페어 테이블 */}
      {data.hedge_pairs.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-[10px] font-mono">
            <thead>
              <tr className="border-b border-terminal-border">
                <th className="text-left py-1.5 text-terminal-subtle font-normal">심볼</th>
                <th className="text-center py-1.5 text-terminal-subtle font-normal">Binance</th>
                <th className="text-center py-1.5 text-terminal-subtle font-normal">Bitget</th>
                <th className="text-right py-1.5 text-terminal-subtle font-normal">합산 PnL</th>
                <th className="text-right py-1.5 text-terminal-subtle font-normal">헤지</th>
              </tr>
            </thead>
            <tbody>
              {data.hedge_pairs.map((pair) => (
                <tr key={pair.symbol} className="border-b border-terminal-border/30 hover:bg-terminal-muted/10">
                  <td className="py-1.5 text-terminal-text font-semibold">{pair.symbol}</td>
                  <td className="py-1.5 text-center">
                    {pair.binance_futures ? (
                      <span className={pair.binance_futures.side === 'long' ? 'text-profit' : 'text-loss'}>
                        {pair.binance_futures.side.toUpperCase()} {Math.abs(pair.binance_futures.size).toFixed(4)}
                      </span>
                    ) : <span className="text-terminal-subtle">—</span>}
                  </td>
                  <td className="py-1.5 text-center">
                    {pair.bitget_futures ? (
                      <span className={pair.bitget_futures.side === 'long' ? 'text-profit' : 'text-loss'}>
                        {pair.bitget_futures.side.toUpperCase()} {Math.abs(pair.bitget_futures.size).toFixed(4)}
                      </span>
                    ) : <span className="text-terminal-subtle">—</span>}
                  </td>
                  <td className={`py-1.5 text-right tabular-nums ${pnlColor(pair.net_pnl)}`}>
                    {fmt(pair.net_pnl)}
                  </td>
                  <td className="py-1.5 text-right">
                    <span className={`px-1 py-0.5 text-[8px] ${pair.is_hedged ? 'bg-profit/20 text-profit' : 'bg-loss/10 text-loss'}`}>
                      {pair.is_hedged ? '헤지' : '단방향'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="flex items-center justify-center h-12 text-xs font-mono text-terminal-subtle">
          활성 포지션 없음
        </div>
      )}
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
  const [liveData, setLiveData] = useState<LivePositionsResponse | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const [metricsData, summaryData, curveData, positionsData, dailyData, shadowData, livePositions] = await Promise.all([
          getPortfolioMetrics().catch(() => null),
          getPortfolioSummary().catch(() => null),
          getEquityCurve().catch(() => null),
          getPositions().catch(() => null),
          getDailyReturns().catch(() => null),
          getShadowStats().catch(() => null),
          getLivePositions().catch(() => null),
        ]);
        if (metricsData) setMetrics(metricsData as PortfolioMetrics);
        if (summaryData) setSummary(summaryData);
        if (positionsData) setPositions(positionsData);
        if (livePositions) setLiveData(livePositions);
        if (dailyData) {
          const returns = Array.isArray(dailyData) ? dailyData : ((dailyData as Record<string, unknown>)?.returns as { date: string; pnl: number }[]) ?? [];
          setDailyReturns(returns);
        }
        if (shadowData?.by_strategy) {
          const bs = Array.isArray(shadowData.by_strategy) ? shadowData.by_strategy : [];
          setShadowByStrategy(bs);
        }

        if (curveData?.curve && curveData.curve.length > 0) {
          const pts = curveData.curve;
          setCurve(pts);

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

      {/* 거래소 간 실시간 포지션 (Binance Futures + Bitget Futures) */}
      <div className="bg-terminal-surface border border-terminal-border p-4">
        <div className="flex items-center justify-between mb-3">
          <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">실시간 포지션</span>
          {liveData && (
            <span className="text-[10px] font-mono text-terminal-subtle tabular-nums">
              총 잔고 <span className="text-terminal-text">${liveData.total_balance_usdt.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
            </span>
          )}
        </div>
        <LivePositionsPanel data={liveData} />
      </div>

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
                    contentStyle={{ background: '#FFFFFF', border: '1px solid #DEDEE5', borderRadius: 0, fontSize: 11, fontFamily: 'IBM Plex Mono' }}
                    formatter={(value: number | undefined, name: string | undefined): [ReactNode, string] => [`$${(value ?? 0).toLocaleString()}`, name ?? '']}
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
                <XAxis dataKey="date" tick={{ fontSize: 9, fontFamily: 'IBM Plex Mono', fill: '#686B82' }} axisLine={false} tickLine={false} tickFormatter={(v: string) => v.slice(5)} />
                <YAxis tick={{ fontSize: 9, fontFamily: 'IBM Plex Mono', fill: '#686B82' }} axisLine={false} tickLine={false} tickFormatter={(v: number) => `$${v.toFixed(0)}`} width={45} />
                <Tooltip contentStyle={{ background: '#FFFFFF', border: '1px solid #DEDEE5', borderRadius: 0, fontSize: 11, fontFamily: 'IBM Plex Mono' }} formatter={(v: number | undefined): [ReactNode, string] => [`$${(v ?? 0).toFixed(2)}`, 'PnL']} />
                <ReferenceLine y={0} stroke="#DEDEE5" strokeDasharray="3 3" />
                <Area type="monotone" dataKey="pnl" stroke="#149E61" strokeWidth={1.5} fill="rgba(20,158,97,0.1)" dot={false} />
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
                    <div
                      className={`h-full transition-all ${s.pnl >= 0 ? 'bg-profit' : 'bg-loss'}`}
                      style={{ width: `${w}%`, opacity: 0.7 }}
                    />
                  </div>
                  <span className={`text-[10px] font-mono tabular-nums w-20 text-right ${s.pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
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
              최대 <span className="text-loss">{metrics.max_drawdown_pct.toFixed(2)}%</span>
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
