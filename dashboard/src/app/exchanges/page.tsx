"use client";

import { useState, useEffect, useCallback } from "react";
import { getExchangeStatus } from "@/lib/api";
import type { ExchangeStatus } from "@/types";

const EXCHANGE_LABELS: Record<string, string> = {
  binance:          "Binance",
  binance_futures:  "Binance Futures",
  bybit:            "Bybit",
  bybit_futures:    "Bybit Futures",
  okx:              "OKX",
  okx_futures:      "OKX Futures",
  bitget:           "Bitget",
  bitget_futures:   "Bitget Futures",
  mexc:             "MEXC",
  gateio:           "Gate.io",
  upbit:            "Upbit",
  bithumb:          "Bithumb",
  coinone:          "Coinone",
};

function formatLatency(ms: number): string {
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function latencyColor(ms: number): string {
  if (ms < 100)  return '#00C896';  // green — excellent
  if (ms < 500)  return '#F59E0B';  // amber — acceptable
  return '#FF4757';                  // red — degraded
}

function formatLastUpdate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString("en-US", { hour12: false });
  } catch {
    return iso;
  }
}

export default function ExchangesPage() {
  const [statuses, setStatuses] = useState<Record<string, ExchangeStatus>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchStatuses = useCallback(async () => {
    try {
      const data = await getExchangeStatus();
      setStatuses(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch exchange status");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatuses();
    const interval = setInterval(fetchStatuses, 5000);
    return () => clearInterval(interval);
  }, [fetchStatuses]);

  const entries = Object.values(statuses);
  const connectedCount = entries.filter((e) => e.connected).length;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-lg font-mono font-semibold text-terminal-text">Exchanges</h2>
          <p className="text-xs font-mono text-terminal-subtle mt-0.5">
            Exchange connectivity · 자동 새로고침 every 5s
          </p>
        </div>
        {entries.length > 0 && (
          <div className="text-right">
            <p className="text-xs font-mono text-terminal-subtle">Connected</p>
            <p className="text-lg font-mono font-semibold tabular-nums"
              style={{ color: connectedCount > 0 ? "#00ff88" : "#ff4d4d" }}>
              {connectedCount}/{entries.length}
            </p>
          </div>
        )}
      </div>

      {/* Cards */}
      {loading && entries.length === 0 ? (
        <div className="bg-terminal-surface border border-terminal-border rounded-lg p-8 text-center text-terminal-subtle text-xs font-mono">
          Loading exchange status...
        </div>
      ) : error ? (
        <div
          className="bg-terminal-surface border border-terminal-border rounded-lg p-8 text-center text-xs font-mono"
          style={{ color: "#ff4d4d" }}
        >
          {error}
        </div>
      ) : entries.length === 0 ? (
        <div className="bg-terminal-surface border border-terminal-border rounded-lg p-8 text-center text-terminal-subtle text-xs font-mono">
          No exchange data available
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {entries.map((ex) => {
            const label = EXCHANGE_LABELS[ex.exchange_id] ?? ex.exchange_id;
            const connColor = ex.connected ? "#00C896" : "#FF4757";
            const balanceEntries = ex.balance ? Object.entries(ex.balance).filter(([, v]) => v > 0) : [];

            return (
              <div
                key={ex.exchange_id}
                className="bg-terminal-surface border border-terminal-border rounded-lg p-4 space-y-3"
                style={{ borderLeftColor: connColor, borderLeftWidth: "2px" }}
              >
                {/* Title row */}
                <div className="flex items-center justify-between gap-2">
                  <div>
                    <p className="text-sm font-mono font-semibold text-terminal-text">{label}</p>
                    <p className="text-[10px] font-mono text-terminal-subtle mt-0.5">{ex.exchange_id}</p>
                  </div>
                  <span
                    className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono shrink-0"
                    style={{
                      backgroundColor: ex.connected ? "rgba(0,200,150,0.1)" : "rgba(255,71,87,0.1)",
                      color: connColor,
                      border: `1px solid ${ex.connected ? "rgba(0,200,150,0.2)" : "rgba(255,71,87,0.2)"}`,
                    }}
                  >
                    <span
                      className="w-1.5 h-1.5 rounded-full"
                      style={{ backgroundColor: connColor }}
                      aria-hidden
                    />
                    {ex.connected ? "LIVE" : "DOWN"}
                  </span>
                </div>

                {/* Stats grid */}
                <div className="grid grid-cols-2 gap-2 pt-2 border-t border-terminal-border/50">
                  <div>
                    <p className="text-[9px] font-mono text-terminal-subtle uppercase tracking-wider">Latency</p>
                    <p className="text-sm font-mono tabular-nums mt-0.5"
                      style={{ color: latencyColor(ex.latency_ms) }}>
                      {formatLatency(ex.latency_ms)}
                    </p>
                  </div>
                  <div>
                    <p className="text-[9px] font-mono text-terminal-subtle uppercase tracking-wider">Depth</p>
                    <p className="text-sm font-mono text-terminal-text tabular-nums mt-0.5">
                      {ex.orderbook_depth}
                    </p>
                  </div>
                  <div>
                    <p className="text-[9px] font-mono text-terminal-subtle uppercase tracking-wider">Symbols</p>
                    <p className="text-sm font-mono text-terminal-text tabular-nums mt-0.5">
                      {ex.symbols_count}
                    </p>
                  </div>
                  <div>
                    <p className="text-[9px] font-mono text-terminal-subtle uppercase tracking-wider">Updated</p>
                    <p className="text-sm font-mono text-terminal-text tabular-nums mt-0.5">
                      {formatLastUpdate(ex.last_update)}
                    </p>
                  </div>
                </div>

                {/* Balance */}
                {balanceEntries.length > 0 && (
                  <div className="pt-2 border-t border-terminal-border/50 space-y-1">
                    <p className="text-[9px] font-mono text-terminal-subtle uppercase tracking-wider">Balance</p>
                    {balanceEntries.slice(0, 4).map(([asset, amount]) => (
                      <div key={asset} className="flex items-center justify-between">
                        <span className="text-[10px] font-mono text-terminal-subtle">{asset}</span>
                        <span className="text-[10px] font-mono text-terminal-text tabular-nums">
                          {amount.toFixed(4)}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
