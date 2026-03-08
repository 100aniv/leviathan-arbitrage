"use client";

import { useState, useEffect, useCallback } from "react";
import { getStrategyMetrics } from "@/lib/api";
import type { StrategyMetric } from "@/types";

export default function AnalyticsPage() {
  const [metrics, setMetrics] = useState<Record<string, StrategyMetric>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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

  useEffect(() => {
    fetchMetrics();
    const interval = setInterval(fetchMetrics, 5000);
    return () => clearInterval(interval);
  }, [fetchMetrics]);

  const strategies = Object.values(metrics);
  const totalPnl = strategies.reduce((s, m) => s + m.pnl, 0);
  const totalTrades = strategies.reduce((s, m) => s + m.fills, 0);
  const totalSignals = strategies.reduce((s, m) => s + m.signals_received, 0);

  return (
    <div className="space-y-4">
      {/* Header */}
      <div>
        <h2 className="text-lg font-mono font-semibold text-terminal-text">Analytics</h2>
        <p className="text-xs font-mono text-terminal-subtle mt-0.5">
          Strategy performance metrics · auto-refresh every 5s
        </p>
      </div>

      {/* Summary row */}
      {strategies.length > 0 && (
        <div className="grid grid-cols-3 gap-4">
          {[
            {
              label: "Total PnL",
              value: `${totalPnl >= 0 ? "+" : ""}$${totalPnl.toFixed(4)}`,
              color: totalPnl >= 0 ? "#00ff88" : "#ff4d4d",
            },
            {
              label: "Total Fills",
              value: totalTrades.toString(),
              color: undefined,
            },
            {
              label: "Total Signals",
              value: totalSignals.toString(),
              color: undefined,
            },
          ].map(({ label, value, color }) => (
            <div
              key={label}
              className="bg-terminal-surface border border-terminal-border rounded-lg p-4"
            >
              <p className="text-terminal-subtle text-xs font-mono">{label}</p>
              <p
                className="text-xl font-mono font-semibold tabular-nums mt-1"
                style={color ? { color } : undefined}
              >
                {value}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* Strategy cards */}
      {loading && strategies.length === 0 ? (
        <div className="bg-terminal-surface border border-terminal-border rounded-lg p-8 text-center text-terminal-subtle text-xs font-mono">
          Loading metrics...
        </div>
      ) : error ? (
        <div
          className="bg-terminal-surface border border-terminal-border rounded-lg p-8 text-center text-xs font-mono"
          style={{ color: "#ff4d4d" }}
        >
          {error}
        </div>
      ) : strategies.length === 0 ? (
        <div className="bg-terminal-surface border border-terminal-border rounded-lg p-8 text-center text-terminal-subtle text-xs font-mono">
          No strategy metrics available
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {strategies.map((m) => {
            const fillRatio =
              m.trade_requests > 0
                ? (m.fills / m.trade_requests) * 100
                : 0;
            const pnlColor = m.pnl >= 0 ? "#00ff88" : "#ff4d4d";
            const barWidth = Math.min(Math.abs(m.pnl) / Math.max(...strategies.map((s) => Math.abs(s.pnl)), 0.0001) * 100, 100);

            return (
              <div
                key={m.id}
                className="bg-terminal-surface border border-terminal-border rounded-lg p-4 space-y-3"
              >
                {/* Title row */}
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

                {/* PnL bar */}
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[10px] font-mono text-terminal-subtle">PnL</span>
                    <span
                      className="text-sm font-mono font-semibold tabular-nums"
                      style={{ color: pnlColor }}
                    >
                      {m.pnl >= 0 ? "+" : ""}${m.pnl.toFixed(4)}
                    </span>
                  </div>
                  <div className="h-1 bg-terminal-muted rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{ width: `${barWidth}%`, backgroundColor: pnlColor }}
                    />
                  </div>
                </div>

                {/* Stats grid */}
                <div className="grid grid-cols-3 gap-2 pt-1 border-t border-terminal-border/50">
                  {[
                    { label: "Signals", value: m.signals_received.toString() },
                    { label: "Requests", value: m.trade_requests.toString() },
                    { label: "Fills", value: m.fills.toString() },
                  ].map(({ label, value }) => (
                    <div key={label}>
                      <p className="text-[9px] font-mono text-terminal-subtle uppercase tracking-wider">{label}</p>
                      <p className="text-sm font-mono text-terminal-text tabular-nums mt-0.5">{value}</p>
                    </div>
                  ))}
                </div>

                {/* Fill ratio row */}
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
