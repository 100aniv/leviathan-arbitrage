"use client";

import { useState, useEffect, useCallback } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";
import { getFundingRates } from "@/lib/api";
import { SkeletonCard, FriendlyError, EmptyState } from "@/components/ui";
import { TrendingUp } from "lucide-react";
import type { FundingRate } from "@/types";

interface HistoryEntry {
  ts: string;
  exchange: string;
  symbol: string;
  rate: number;
  cumulative: number;
}

export default function FundingPage() {
  const [data, setData] = useState<Record<string, Record<string, FundingRate>>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);

  const fetchRates = useCallback(async () => {
    try {
      const result = await getFundingRates();
      setData(result);
      setError(null);

      const ts = new Date().toISOString();
      const newEntries: Omit<HistoryEntry, 'cumulative'>[] = [];
      for (const [ex, exData] of Object.entries(result)) {
        for (const [, rate] of Object.entries(exData)) {
          newEntries.push({ ts, exchange: ex, symbol: rate.symbol, rate: rate.rate });
        }
      }
      setHistory(prev => {
        const combined = [...prev, ...newEntries].slice(-50);
        const cumMap: Record<string, number> = {};
        return combined.map(e => {
          cumMap[e.symbol] = (cumMap[e.symbol] ?? 0) + e.rate;
          return { ...e, cumulative: cumMap[e.symbol] };
        });
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch funding rates");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchRates();
    const interval = setInterval(fetchRates, 10000);
    return () => clearInterval(interval);
  }, [fetchRates]);

  const exchanges = Object.keys(data).sort();
  const symbolSet = new Set<string>();
  for (const exData of Object.values(data)) {
    for (const sym of Object.keys(exData)) symbolSet.add(sym);
  }
  const symbols = Array.from(symbolSet).sort();

  function fmtRate(rate: number) {
    const pct = (rate * 100).toFixed(4);
    return rate >= 0 ? `+${pct}%` : `${pct}%`;
  }

  function rateClassName(rate: number): string {
    if (rate > 0.0001) return "text-profit";
    if (rate < -0.0001) return "text-loss";
    return "text-terminal-subtle";
  }

  function rateColor(rate: number): string {
    if (rate > 0.0001) return "#149E61";
    if (rate < -0.0001) return "#E5484D";
    return "#9497A9";
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-mono font-semibold text-terminal-text">Funding Rates</h2>
          <p className="text-xs font-mono text-terminal-subtle mt-0.5">
            Exchange × symbol matrix · auto-refresh every 10s
          </p>
        </div>
        <div className="flex items-center gap-4 text-[10px] font-mono text-terminal-subtle">
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full inline-block bg-profit" />
            profit (long pays short)
          </span>
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full inline-block bg-loss" />
            loss (short pays long)
          </span>
        </div>
      </div>

      {/* Table */}
      <div className="bg-terminal-surface border border-terminal-border rounded-lg overflow-hidden">
        {loading && symbols.length === 0 ? (
          <div className="p-6 space-y-3">
            <SkeletonCard /><SkeletonCard /><SkeletonCard />
          </div>
        ) : error ? (
          <FriendlyError error={error} onRetry={fetchRates} />
        ) : symbols.length === 0 ? (
          <EmptyState
            icon={TrendingUp}
            title="펀딩비 데이터 없음"
            description="No funding rate data available"
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="border-b border-terminal-border">
                  <th className="text-left px-4 py-2 text-terminal-subtle font-normal sticky left-0 bg-terminal-surface z-10">
                    Symbol
                  </th>
                  {exchanges.map((ex) => (
                    <th key={ex} className="text-right px-4 py-2 text-terminal-subtle font-normal whitespace-nowrap">
                      {ex}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {symbols.map((sym, i) => (
                  <tr
                    key={sym}
                    className={`border-b border-terminal-border/50 hover:bg-terminal-muted/30 transition-colors ${
                      i % 2 === 0 ? "" : "bg-terminal-bg/30"
                    }`}
                  >
                    <td className="px-4 py-2 font-semibold text-terminal-text sticky left-0 bg-inherit z-10 whitespace-nowrap">
                      {sym}
                    </td>
                    {exchanges.map((ex) => {
                      const entry = data[ex]?.[sym];
                      if (!entry) {
                        return (
                          <td key={ex} className="px-4 py-2 text-right text-terminal-subtle tabular-nums">
                            —
                          </td>
                        );
                      }
                      return (
                        <td
                          key={ex}
                          className={`px-4 py-2 text-right tabular-nums font-semibold ${rateClassName(entry.rate)}`}
                          title={
                            entry.next_funding_time
                              ? `Next: ${new Date(entry.next_funding_time).toLocaleString()}`
                              : undefined
                          }
                        >
                          {fmtRate(entry.rate)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Exchange summary */}
      {exchanges.length > 0 && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {exchanges.map((ex) => {
            const exData = Object.values(data[ex] ?? {});
            const avgRate = exData.length > 0
              ? exData.reduce((s, r) => s + r.rate, 0) / exData.length
              : 0;
            return (
              <div key={ex} className="bg-terminal-surface border border-terminal-border rounded-lg p-3">
                <p className="text-[10px] font-mono text-terminal-subtle uppercase tracking-wider">{ex}</p>
                <p className={`text-base font-mono font-semibold tabular-nums mt-1 ${rateClassName(avgRate)}`}>
                  {fmtRate(avgRate)}
                </p>
                <p className="text-[10px] font-mono text-terminal-subtle mt-0.5">
                  avg · {exData.length} symbols
                </p>
              </div>
            );
          })}
        </div>
      )}

      {/* History section */}
      {history.length > 0 && (
        <>
          {/* BarChart */}
          <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4 space-y-3">
            <p className="text-xs font-mono text-terminal-subtle uppercase tracking-wider">
              Funding Rate History — by Symbol
            </p>
            <ResponsiveContainer width="100%" height={160}>
              <BarChart
                data={symbols.slice(0, 12).map(sym => {
                  const rates = exchanges.map(ex => data[ex]?.[sym]?.rate ?? 0);
                  const avg = rates.reduce((s, r) => s + r, 0) / Math.max(rates.length, 1);
                  return { symbol: sym, rate: parseFloat((avg * 100).toFixed(4)) };
                })}
                margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
              >
                <XAxis
                  dataKey="symbol"
                  tick={{ fill: '#686B82', fontSize: 9, fontFamily: 'monospace' }}
                  tickLine={false}
                  axisLine={{ stroke: '#DEDEE5' }}
                />
                <YAxis
                  tick={{ fill: '#686B82', fontSize: 9, fontFamily: 'monospace' }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={v => `${v}%`}
                  width={44}
                />
                <Tooltip
                  contentStyle={{ background: '#FFFFFF', border: '1px solid #DEDEE5', borderRadius: 0, fontFamily: 'monospace', fontSize: 11 }}
                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                  formatter={(v: any) => [`${(v ?? 0) > 0 ? '+' : ''}${(+(v ?? 0)).toFixed(4)}%`, 'Avg Rate']}
                />
                <Bar dataKey="rate" isAnimationActive={false} radius={[2, 2, 0, 0]}>
                  {symbols.slice(0, 12).map((sym, idx) => {
                    const rates = exchanges.map(ex => data[ex]?.[sym]?.rate ?? 0);
                    const avg = rates.reduce((s, r) => s + r, 0) / Math.max(rates.length, 1);
                    return <Cell key={idx} fill={rateColor(avg)} />;
                  })}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* History table */}
          <div className="bg-terminal-surface border border-terminal-border rounded-lg overflow-hidden">
            <div className="px-4 py-2 border-b border-terminal-border">
              <p className="text-xs font-mono text-terminal-subtle uppercase tracking-wider">
                Recent Snapshots ({history.slice(-20).length})
              </p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs font-mono">
                <thead>
                  <tr className="border-b border-terminal-border text-terminal-subtle">
                    <th className="text-left px-4 py-2 font-normal">Timestamp</th>
                    <th className="text-left px-4 py-2 font-normal">Exchange</th>
                    <th className="text-left px-4 py-2 font-normal">Symbol</th>
                    <th className="text-right px-4 py-2 font-normal">Rate</th>
                    <th className="text-right px-4 py-2 font-normal">Cumulative</th>
                  </tr>
                </thead>
                <tbody>
                  {history.slice(-20).reverse().map((e, i) => (
                    <tr
                      key={i}
                      className={`border-b border-terminal-border/50 hover:bg-terminal-muted/30 transition-colors ${i % 2 === 0 ? '' : 'bg-terminal-bg/30'}`}
                    >
                      <td className="px-4 py-1.5 text-terminal-subtle tabular-nums whitespace-nowrap">
                        {new Date(e.ts).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                      </td>
                      <td className="px-4 py-1.5 text-terminal-text uppercase">{e.exchange}</td>
                      <td className="px-4 py-1.5 text-terminal-text font-semibold">{e.symbol}</td>
                      <td className={`px-4 py-1.5 text-right tabular-nums font-semibold ${rateClassName(e.rate)}`}>
                        {fmtRate(e.rate)}
                      </td>
                      <td className={`px-4 py-1.5 text-right tabular-nums ${rateClassName(e.cumulative)}`}>
                        {fmtRate(e.cumulative)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
