"use client";

import { useState, useEffect, useCallback } from "react";
import { getExchangeStatus } from "@/lib/api";
import { SkeletonCard, FriendlyError, EmptyState } from "@/components/ui";
import { Globe } from "lucide-react";
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

function formatLatency(ms: number | undefined | null): string {
  if (ms == null || isNaN(ms)) return '—';
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function latencyClassName(ms: number | undefined | null): string {
  if (ms == null || isNaN(ms)) return 'text-terminal-subtle';
  if (ms < 100) return 'text-profit';
  if (ms < 500) return 'text-warn';
  return 'text-loss';
}

function formatLastUpdate(iso: string | undefined | null): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '—';
    return d.toLocaleTimeString("en-US", { hour12: false });
  } catch {
    return '—';
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
            <p className={`text-lg font-mono font-semibold tabular-nums ${connectedCount > 0 ? "text-profit" : "text-loss"}`}>
              {connectedCount}/{entries.length}
            </p>
          </div>
        )}
      </div>

      {/* Cards */}
      {loading && entries.length === 0 ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <SkeletonCard /><SkeletonCard /><SkeletonCard /><SkeletonCard />
        </div>
      ) : error ? (
        <FriendlyError error={error} onRetry={fetchStatuses} />
      ) : entries.length === 0 ? (
        <EmptyState
          icon={Globe}
          title="거래소 데이터 없음"
          description="No exchange data available"
        />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {entries.map((ex) => {
            const label = EXCHANGE_LABELS[ex.exchange_id] ?? ex.exchange_id;
            const connBorderColor = ex.connected ? "#149E61" : "#E5484D";
            const balanceEntries = ex.balance ? Object.entries(ex.balance).filter(([, v]) => v > 0) : [];

            return (
              <div
                key={ex.exchange_id}
                className="bg-terminal-surface border border-terminal-border rounded-lg p-4 space-y-3"
                style={{ borderLeftColor: connBorderColor, borderLeftWidth: "2px" }}
              >
                {/* Title row */}
                <div className="flex items-center justify-between gap-2">
                  <div>
                    <p className="text-sm font-mono font-semibold text-terminal-text">{label}</p>
                    <p className="text-[10px] font-mono text-terminal-subtle mt-0.5">{ex.exchange_id}</p>
                  </div>
                  <span className={`flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono shrink-0 ${
                    ex.connected
                      ? "bg-profit/10 text-profit border border-profit/20"
                      : "bg-loss/10 text-loss border border-loss/20"
                  }`}>
                    <span
                      className={`w-1.5 h-1.5 rounded-full ${ex.connected ? "bg-profit" : "bg-loss"}`}
                      aria-hidden
                    />
                    {ex.connected ? "LIVE" : "DOWN"}
                  </span>
                </div>

                {/* Stats grid */}
                <div className="grid grid-cols-2 gap-2 pt-2 border-t border-terminal-border/50">
                  <div>
                    <p className="text-[9px] font-mono text-terminal-subtle uppercase tracking-wider">Latency</p>
                    <p className={`text-sm font-mono tabular-nums mt-0.5 ${latencyClassName(ex.latency_ms)}`}>
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
