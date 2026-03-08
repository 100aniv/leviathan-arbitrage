"use client";

import { useState, useEffect, useCallback } from "react";
import { getTrades, getStrategies } from "@/lib/api";
import type { Trade, Strategy } from "@/types";

export default function TradesPage() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [selectedStrategy, setSelectedStrategy] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchTrades = useCallback(async () => {
    try {
      const data = await getTrades(selectedStrategy || undefined, 100);
      setTrades(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch trades");
    } finally {
      setLoading(false);
    }
  }, [selectedStrategy]);

  useEffect(() => {
    getStrategies()
      .then(setStrategies)
      .catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    fetchTrades();
    const interval = setInterval(fetchTrades, 5000);
    return () => clearInterval(interval);
  }, [fetchTrades]);

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-mono font-semibold text-terminal-text">Trade History</h2>
          <p className="text-xs font-mono text-terminal-subtle mt-0.5">
            Executed arbitrage trades · auto-refresh every 5s
          </p>
        </div>
        <select
          value={selectedStrategy}
          onChange={(e) => setSelectedStrategy(e.target.value)}
          className="bg-terminal-surface border border-terminal-border rounded-md px-3 py-1.5 text-xs font-mono text-terminal-text focus:outline-none focus:border-accent"
        >
          <option value="">All Strategies</option>
          {strategies.map((s) => (
            <option key={s.id} value={s.id}>
              {s.id}
            </option>
          ))}
        </select>
      </div>

      {/* Table card */}
      <div className="bg-terminal-surface border border-terminal-border rounded-lg overflow-hidden">
        {loading && trades.length === 0 ? (
          <div className="p-8 text-center text-terminal-subtle text-xs font-mono">
            Loading trades...
          </div>
        ) : error ? (
          <div className="p-8 text-center font-mono text-xs" style={{ color: "#ff4d4d" }}>
            {error}
          </div>
        ) : trades.length === 0 ? (
          <div className="p-8 text-center text-terminal-subtle text-xs font-mono">
            No trades found
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="border-b border-terminal-border text-terminal-subtle">
                  <th className="text-left px-4 py-2">Timestamp</th>
                  <th className="text-left px-4 py-2">Strategy</th>
                  <th className="text-left px-4 py-2">Symbol</th>
                  <th className="text-left px-4 py-2">Buy → Sell</th>
                  <th className="text-right px-4 py-2">Size</th>
                  <th className="text-right px-4 py-2">Entry</th>
                  <th className="text-right px-4 py-2">Exit</th>
                  <th className="text-right px-4 py-2">PnL</th>
                  <th className="text-left px-4 py-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((trade, i) => (
                  <tr
                    key={trade.id}
                    className={`border-b border-terminal-border/50 hover:bg-terminal-muted/30 transition-colors ${
                      i % 2 === 0 ? "" : "bg-terminal-bg/30"
                    }`}
                  >
                    <td className="px-4 py-2 text-terminal-subtle tabular-nums whitespace-nowrap">
                      {new Date(trade.timestamp).toLocaleString()}
                    </td>
                    <td className="px-4 py-2 text-terminal-text">{trade.strategy_id}</td>
                    <td className="px-4 py-2 text-terminal-text font-semibold">{trade.symbol}</td>
                    <td className="px-4 py-2 text-terminal-subtle">
                      {trade.buy_exchange} → {trade.sell_exchange}
                    </td>
                    <td className="px-4 py-2 text-right text-terminal-text tabular-nums">
                      {trade.size.toFixed(4)}
                    </td>
                    <td className="px-4 py-2 text-right text-terminal-text tabular-nums">
                      ${trade.entry_price.toFixed(2)}
                    </td>
                    <td className="px-4 py-2 text-right text-terminal-text tabular-nums">
                      ${trade.exit_price.toFixed(2)}
                    </td>
                    <td
                      className="px-4 py-2 text-right tabular-nums font-semibold"
                      style={{ color: trade.pnl >= 0 ? "#00ff88" : "#ff4d4d" }}
                    >
                      {trade.pnl >= 0 ? "+" : ""}${trade.pnl.toFixed(4)}
                    </td>
                    <td className="px-4 py-2">
                      <span
                        className="px-1.5 py-0.5 rounded text-[10px] font-mono"
                        style={{
                          backgroundColor:
                            trade.status === "closed"
                              ? "rgba(0,255,136,0.1)"
                              : "rgba(245,158,11,0.1)",
                          color:
                            trade.status === "closed" ? "#00ff88" : "#f59e0b",
                          border: `1px solid ${
                            trade.status === "closed"
                              ? "rgba(0,255,136,0.2)"
                              : "rgba(245,158,11,0.2)"
                          }`,
                        }}
                      >
                        {trade.status.toUpperCase()}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Summary */}
      {trades.length > 0 && (
        <div className="grid grid-cols-3 gap-4">
          {[
            {
              label: "Total Trades",
              value: trades.length.toString(),
              color: "text-terminal-text",
            },
            {
              label: "Total PnL",
              value: `${trades.reduce((s, t) => s + t.pnl, 0) >= 0 ? "+" : ""}$${trades.reduce((s, t) => s + t.pnl, 0).toFixed(4)}`,
              color: trades.reduce((s, t) => s + t.pnl, 0) >= 0 ? "text-profit" : "text-loss",
            },
            {
              label: "Win Rate",
              value: `${((trades.filter((t) => t.pnl > 0).length / trades.length) * 100).toFixed(1)}%`,
              color: "text-accent",
            },
          ].map(({ label, value, color }) => (
            <div
              key={label}
              className="bg-terminal-surface border border-terminal-border rounded-lg p-4"
            >
              <p className="text-terminal-subtle text-xs font-mono">{label}</p>
              <p className={`text-xl font-mono font-semibold tabular-nums mt-1 ${color}`}>
                {value}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
