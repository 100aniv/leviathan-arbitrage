"use client";

import { useState, useEffect, useCallback } from "react";
import { getFundingRates } from "@/lib/api";
import type { FundingRate } from "@/types";

export default function FundingPage() {
  const [data, setData] = useState<Record<string, Record<string, FundingRate>>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchRates = useCallback(async () => {
    try {
      const result = await getFundingRates();
      setData(result);
      setError(null);
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

  // Build symbol × exchange matrix
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

  function rateColor(rate: number) {
    if (rate > 0.0001) return "#00ff88";
    if (rate < -0.0001) return "#ff4d4d";
    return "#8b9cb3";
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
            <span className="w-2 h-2 rounded-full inline-block" style={{ backgroundColor: "#00ff88" }} />
            profit (long pays short)
          </span>
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full inline-block" style={{ backgroundColor: "#ff4d4d" }} />
            loss (short pays long)
          </span>
        </div>
      </div>

      {/* Table */}
      <div className="bg-terminal-surface border border-terminal-border rounded-lg overflow-hidden">
        {loading && symbols.length === 0 ? (
          <div className="p-8 text-center text-terminal-subtle text-xs font-mono">
            Loading funding rates...
          </div>
        ) : error ? (
          <div className="p-8 text-center text-xs font-mono" style={{ color: "#ff4d4d" }}>
            {error}
          </div>
        ) : symbols.length === 0 ? (
          <div className="p-8 text-center text-terminal-subtle text-xs font-mono">
            No funding rate data available
          </div>
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
                          <td key={ex} className="px-4 py-2 text-right text-terminal-border tabular-nums">
                            —
                          </td>
                        );
                      }
                      return (
                        <td
                          key={ex}
                          className="px-4 py-2 text-right tabular-nums font-semibold"
                          style={{ color: rateColor(entry.rate) }}
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
                <p
                  className="text-base font-mono font-semibold tabular-nums mt-1"
                  style={{ color: rateColor(avgRate) }}
                >
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
    </div>
  );
}
