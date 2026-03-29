"use client";

import { useState, useEffect, useCallback } from "react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, Cell, ResponsiveContainer,
} from "recharts";
import {
  getStrategyMetrics, getShadowStats, getAttribution, getDailyReturns,
} from "@/lib/api";
import type { StrategyMetric } from "@/types";

// ─── Helpers ─────────────────────────────────────────────────────────────────

function heatColor(value: number, maxAbs: number): string {
  if (maxAbs === 0) return "rgba(80,80,80,0.15)";
  const ratio = Math.max(-1, Math.min(1, value / maxAbs));
  if (ratio > 0) return `rgba(0,255,136,${0.08 + ratio * 0.7})`;
  if (ratio < 0) return `rgba(255,77,77,${0.08 + (-ratio) * 0.7})`;
  return "rgba(80,80,80,0.15)";
}

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const HOURS = Array.from({ length: 24 }, (_, i) => i);

// ─── Sharpe Bar Chart ────────────────────────────────────────────────────────

interface StrategyBar { name: string; value: number; pnl: number }

function SharpeChart({ data }: { data: StrategyBar[] }) {
  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center h-32 text-xs font-mono text-terminal-subtle">
        Shadow 데이터 없음 — Shadow Mode 실행 후 표시됩니다
      </div>
    );
  }
  return (
    <ResponsiveContainer width="100%" height={data.length * 36 + 20}>
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 4, right: 16, left: 8, bottom: 4 }}
        barSize={16}
      >
        <XAxis
          type="number"
          tick={{ fontSize: 10, fontFamily: "JetBrains Mono, monospace", fill: "#666" }}
          axisLine={false}
          tickLine={false}
          tickFormatter={(v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}`}
        />
        <YAxis
          type="category"
          dataKey="name"
          tick={{ fontSize: 10, fontFamily: "JetBrains Mono, monospace", fill: "#a0a0a0" }}
          axisLine={false}
          tickLine={false}
          width={88}
        />
        <Tooltip
          contentStyle={{
            background: "#1a1a1a",
            border: "1px solid #333",
            borderRadius: 0,
            fontSize: 11,
            fontFamily: "JetBrains Mono, monospace",
          }}
          formatter={(v: number | undefined) => [v != null ? `${v >= 0 ? "+" : ""}${v.toFixed(4)}` : "—", "PnL"]}
          cursor={{ fill: "rgba(255,255,255,0.04)" }}
        />
        <Bar dataKey="value" radius={0}>
          {data.map((d, i) => (
            <Cell key={i} fill={d.value >= 0 ? "#00ff88" : "#ff4d4d"} fillOpacity={0.8} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

// ─── PnL Heatmap ─────────────────────────────────────────────────────────────

interface HeatCell { day: number; hour: number; value: number }

function PnLHeatmap({ cells }: { cells: HeatCell[] }) {
  const maxAbs = Math.max(...cells.map((c) => Math.abs(c.value)), 0.0001);
  if (cells.every((c) => c.value === 0)) {
    return (
      <div className="flex items-center justify-center h-32 text-xs font-mono text-terminal-subtle">
        귀속 데이터 없음 — Shadow 운영 후 표시됩니다
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <div className="min-w-[600px]">
        {/* Hour labels */}
        <div className="flex ml-10 mb-1">
          {HOURS.map((h) => (
            <div key={h} className="flex-1 text-center text-[9px] font-mono text-terminal-subtle tabular-nums">
              {h % 6 === 0 ? String(h).padStart(2, "0") : ""}
            </div>
          ))}
        </div>
        {/* Grid */}
        {DAYS.map((day, di) => (
          <div key={day} className="flex items-center mb-0.5">
            <div className="w-10 text-right pr-2 text-[9px] font-mono text-terminal-subtle shrink-0">
              {day}
            </div>
            {HOURS.map((h) => {
              const cell = cells.find((c) => c.day === di && c.hour === h);
              const v = cell?.value ?? 0;
              return (
                <div
                  key={h}
                  className="flex-1 h-5 mx-px"
                  style={{ backgroundColor: heatColor(v, maxAbs) }}
                  title={`${day} ${String(h).padStart(2, "0")}:00 — PnL: ${v >= 0 ? "+" : ""}$${v.toFixed(4)}`}
                />
              );
            })}
          </div>
        ))}
        {/* Legend */}
        <div className="flex items-center gap-3 mt-2 ml-10">
          <div className="flex items-center gap-1">
            <div className="w-10 h-2.5" style={{ background: "linear-gradient(to right, rgba(255,77,77,0.8), rgba(80,80,80,0.15))" }} />
            <span className="text-[9px] font-mono text-terminal-subtle">Loss</span>
          </div>
          <div className="flex items-center gap-1">
            <div className="w-10 h-2.5" style={{ background: "linear-gradient(to right, rgba(80,80,80,0.15), rgba(0,255,136,0.78))" }} />
            <span className="text-[9px] font-mono text-terminal-subtle">Profit</span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function AnalyticsPage() {
  const [metrics, setMetrics] = useState<Record<string, StrategyMetric>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sharpeData, setSharpeData] = useState<StrategyBar[]>([]);
  const [heatCells, setHeatCells] = useState<HeatCell[]>([]);
  const [shadowTotals, setShadowTotals] = useState<{pnl: number; trades: number; wr: number} | null>(null);

  const fetchMetrics = useCallback(async () => {
    try {
      const data = await getStrategyMetrics();
      setMetrics(data.strategies);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch strategy metrics");
    } finally {
      setLoading(false);
    }
  }, []);

  // Sharpe + heatmap data (less frequent)
  const fetchChartData = useCallback(async () => {
    try {
      const [shadow, attribution, dailyReturns] = await Promise.all([
        getShadowStats().catch(() => null),
        getAttribution().catch(() => null),
        getDailyReturns().catch(() => null),
      ]);

      // Shadow totals for summary cards (ground truth)
      if (shadow) {
        setShadowTotals({
          pnl: shadow.total_pnl ?? 0,
          trades: shadow.trades_executed ?? 0,
          wr: shadow.win_rate ?? 0,
        });
      }

      // Sharpe bar — use shadow by_strategy sorted by PnL
      if (shadow?.by_strategy && shadow.by_strategy.length > 0) {
        const bars: StrategyBar[] = shadow.by_strategy
          .map((s) => ({
            name: s.strategy_id.replace(/_/g, " ").substring(0, 14),
            value: s.pnl,
            pnl: s.pnl,
          }))
          .sort((a, b) => b.value - a.value);
        setSharpeData(bars);
      }

      // Heatmap — combine hourly attribution with daily returns
      if (attribution?.by_hour) {
        const hourlyPnl = Array.from({ length: 24 }, (_, h) => {
          const entry = attribution.by_hour.find((x) => x.key === String(h));
          return entry?.pnl ?? 0;
        });
        const totalAbsHourly = hourlyPnl.reduce((s, v) => s + Math.abs(v), 0);
        const hourWeights = hourlyPnl.map((v) =>
          totalAbsHourly > 0 ? v / totalAbsHourly : 1 / 24
        );

        // Last 7 daily returns (fill missing with 0)
        const rawReturns = Array.isArray(dailyReturns) ? dailyReturns : dailyReturns?.returns ?? [];
      const last7 = rawReturns.slice(-7);
        const dayPnls = Array.from({ length: 7 }, (_, i) => last7[i]?.pnl ?? 0);

        const cells: HeatCell[] = [];
        for (let di = 0; di < 7; di++) {
          for (let h = 0; h < 24; h++) {
            cells.push({ day: di, hour: h, value: dayPnls[di] * hourWeights[h] * 24 });
          }
        }
        setHeatCells(cells);
      }
    } catch {
      // Chart data is optional
    }
  }, []);

  useEffect(() => {
    fetchMetrics();
    fetchChartData();
    const interval = setInterval(fetchMetrics, 5000);
    const chartInterval = setInterval(fetchChartData, 30_000);
    return () => {
      clearInterval(interval);
      clearInterval(chartInterval);
    };
  }, [fetchMetrics, fetchChartData]);

  const strategies = Object.values(metrics);
  // Use shadow stats as ground truth for totals (strategy_manager metrics diverge)
  const totalPnl = shadowTotals?.pnl ?? strategies.reduce((s, m) => s + m.pnl, 0);
  const totalTrades = shadowTotals?.trades ?? strategies.reduce((s, m) => s + ((m as any).trades || m.fills || 0), 0);
  const totalSignals = strategies.reduce((s, m) => s + (m.signals_received || (m as any).trade_requests || 0), 0);

  return (
    <div className="space-y-4">
      {/* Header */}
      <div>
        <h2 className="text-lg font-mono font-semibold text-terminal-text">Analytics</h2>
        <p className="text-xs font-mono text-terminal-subtle mt-0.5">
          전략 성과 지표 · 5초마다 자동 새로고침
        </p>
      </div>

      {/* Summary row */}
      {strategies.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {[
            {
              label: "총 손익",
              value: `${totalPnl >= 0 ? "+" : ""}$${totalPnl.toFixed(4)}`,
              color: totalPnl >= 0 ? "#00ff88" : "#ff4d4d",
            },
            { label: "총 거래",  value: totalTrades.toLocaleString(),   color: undefined },
            { label: "총 시그널", value: totalSignals.toLocaleString(), color: undefined },
          ].map(({ label, value, color }) => (
            <div key={label} className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
              <p className="text-terminal-subtle text-xs font-mono">{label}</p>
              <p className="text-xl font-mono font-semibold tabular-nums mt-1" style={color ? { color } : undefined}>
                {value}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* Strategy Performance Ranking (Sharpe proxy) */}
      <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
        <div className="mb-3">
          <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">
            전략 성과 순위
          </span>
          <span className="ml-2 text-[10px] font-mono text-terminal-subtle">(PnL 기준, 높은 순)</span>
        </div>
        <SharpeChart data={sharpeData} />
      </div>

      {/* PnL Heatmap */}
      <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
        <div className="mb-3">
          <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">
            시간대별 PnL 히트맵
          </span>
          <span className="ml-2 text-[10px] font-mono text-terminal-subtle">(24시간 × 7일)</span>
        </div>
        <PnLHeatmap cells={heatCells} />
      </div>

      {/* Strategy cards */}
      {loading && strategies.length === 0 ? (
        <div className="bg-terminal-surface border border-terminal-border rounded-lg p-8 text-center text-terminal-subtle text-xs font-mono">
          Loading metrics...
        </div>
      ) : error ? (
        <div className="bg-terminal-surface border border-terminal-border rounded-lg p-8 text-center text-xs font-mono" style={{ color: "#ff4d4d" }}>
          {error}
        </div>
      ) : strategies.length === 0 ? (
        <div className="bg-terminal-surface border border-terminal-border rounded-lg p-8 text-center text-terminal-subtle text-xs font-mono">
          No strategy metrics available
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {strategies.map((m) => {
            const fillRatio = m.trade_requests > 0 ? (m.fills / m.trade_requests) * 100 : 0;
            const pnlColor = m.pnl >= 0 ? "#00ff88" : "#ff4d4d";
            const barWidth = Math.min(
              Math.abs(m.pnl) / Math.max(...strategies.map((s) => Math.abs(s.pnl)), 0.0001) * 100,
              100
            );

            return (
              <div key={m.id} className="bg-terminal-surface border border-terminal-border rounded-lg p-4 space-y-3">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="text-sm font-mono font-semibold text-terminal-text">{m.type}</p>
                    <p className="text-[10px] font-mono text-terminal-subtle mt-0.5">{m.id}</p>
                  </div>
                  <span
                    className="px-1.5 py-0.5 rounded text-[10px] font-mono shrink-0"
                    style={{
                      backgroundColor: m.enabled ? "rgba(0,255,136,0.1)" : "rgba(100,100,100,0.15)",
                      color: m.enabled ? "#00ff88" : "#666",
                      border: `1px solid ${m.enabled ? "rgba(0,255,136,0.2)" : "rgba(100,100,100,0.2)"}`,
                    }}
                  >
                    {m.enabled ? "ACTIVE" : "IDLE"}
                  </span>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[10px] font-mono text-terminal-subtle">PnL</span>
                    <span className="text-sm font-mono font-semibold tabular-nums" style={{ color: pnlColor }}>
                      {m.pnl >= 0 ? "+" : ""}${m.pnl.toFixed(4)}
                    </span>
                  </div>
                  <div className="h-1 bg-terminal-muted rounded-full overflow-hidden">
                    <div className="h-full rounded-full transition-all duration-500" style={{ width: `${barWidth}%`, backgroundColor: pnlColor }} />
                  </div>
                </div>

                <div className="grid grid-cols-3 gap-2 pt-1 border-t border-terminal-border/50">
                  {[
                    { label: "Trades",   value: ((m as any).trades || m.fills || 0).toString() },
                    { label: "Wins",     value: ((m as any).wins || 0).toString() },
                    { label: "WR",       value: ((m as any).win_rate ? `${((m as any).win_rate * 100).toFixed(0)}%` : `${m.trade_requests > 0 ? ((m.fills / m.trade_requests) * 100).toFixed(0) : 0}%`) },
                  ].map(({ label, value }) => (
                    <div key={label}>
                      <p className="text-[9px] font-mono text-terminal-subtle uppercase tracking-wider">{label}</p>
                      <p className="text-sm font-mono text-terminal-text tabular-nums mt-0.5">{value}</p>
                    </div>
                  ))}
                </div>

                <div className="flex items-center justify-between text-[10px] font-mono text-terminal-subtle">
                  <span>Fill ratio</span>
                  <span className="text-terminal-text">{fillRatio.toFixed(1)}%</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
