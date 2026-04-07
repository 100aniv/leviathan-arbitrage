"use client";

import { useState, useEffect, useCallback } from "react";
import { getPortfolioSummary, getExchangeStatus } from "@/lib/api";
import { SkeletonCard, FriendlyError, EmptyState } from "@/components/ui";
import { Wallet } from "lucide-react";
import type { PortfolioSummaryResponse, ExchangeStatus } from "@/types";

const EXCHANGE_LABELS: Record<string, string> = {
  binance:         "Binance",
  binance_futures: "Binance Futures",
  bybit:           "Bybit",
  bybit_futures:   "Bybit Futures",
  okx:             "OKX",
  okx_futures:     "OKX Futures",
  bitget:          "Bitget",
  bitget_futures:  "Bitget Futures",
  mexc:            "MEXC",
  gateio:          "Gate.io",
  upbit:           "Upbit",
  bithumb:         "Bithumb",
  coinone:         "Coinone",
};

function latencyClassName(ms: number | undefined | null): string {
  if (ms == null || isNaN(ms)) return "text-terminal-subtle";
  if (ms < 100) return "text-profit";
  if (ms < 500) return "text-warn";
  return "text-loss";
}

function formatBalance(v: number): string {
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatPnl(v: number): string {
  return `${v >= 0 ? "+" : ""}$${Math.abs(v).toFixed(2)}`;
}

export default function AssetsPage() {
  const [summary, setSummary]   = useState<PortfolioSummaryResponse | null>(null);
  const [statuses, setStatuses] = useState<Record<string, ExchangeStatus>>({});
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [sum, exch] = await Promise.all([
        getPortfolioSummary(),
        getExchangeStatus(),
      ]);
      setSummary(sum);
      setStatuses(exch);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch asset data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 30_000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const balances   = summary?.exchange_balances ?? [];
  const totalBal   = summary?.total_balance_usdt ?? 0;
  const totalPnl   = summary?.total_pnl ?? 0;
  const dailyPnl   = summary?.daily_pnl ?? 0;
  const activePosn = summary?.active_positions ?? 0;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-lg font-mono font-semibold text-terminal-text">자산 현황</h2>
          <p className="text-xs font-mono text-terminal-subtle mt-0.5">
            거래소별 잔고 · 포지션 P&amp;L · 30초 자동 새로고침
          </p>
        </div>
        {summary && (
          <div className="text-right">
            <p className="text-xs font-mono text-terminal-subtle">총 자산 (USDT)</p>
            <p className="text-lg font-mono font-semibold tabular-nums text-terminal-text">
              ${formatBalance(totalBal)}
            </p>
          </div>
        )}
      </div>

      {/* KPI summary */}
      {summary && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {[
            { label: "누적 손익",   value: formatPnl(totalPnl),    className: totalPnl >= 0 ? "text-profit" : "text-loss" },
            { label: "오늘 손익",   value: formatPnl(dailyPnl),    className: dailyPnl >= 0 ? "text-profit" : "text-loss" },
            { label: "활성 포지션", value: String(activePosn),     className: "text-terminal-text" },
          ].map(({ label, value, className }) => (
            <div key={label} className="bg-terminal-surface border border-terminal-border p-4">
              <p className="text-[9px] font-mono text-terminal-subtle uppercase tracking-wider">{label}</p>
              <p className={`text-xl font-mono font-semibold tabular-nums mt-1 ${className}`}>{value}</p>
            </div>
          ))}
        </div>
      )}

      {/* Exchange cards */}
      {loading && balances.length === 0 ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <SkeletonCard /><SkeletonCard /><SkeletonCard /><SkeletonCard />
        </div>
      ) : error ? (
        <FriendlyError error={error} onRetry={fetchData} />
      ) : balances.length === 0 ? (
        <EmptyState
          icon={Wallet}
          title="자산 데이터 없음"
          description="No asset data available"
        />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {balances.map((ex) => {
            const label     = EXCHANGE_LABELS[ex.exchange_id] ?? ex.exchange_id;
            const connBorderColor = ex.connected ? "#149E61" : "#E5484D";
            const exStatus  = statuses[ex.exchange_id];

            return (
              <div
                key={ex.exchange_id}
                className="bg-terminal-surface border border-terminal-border rounded-lg p-4 space-y-3"
                style={{ borderLeftColor: connBorderColor, borderLeftWidth: "2px" }}
              >
                {/* Title + status badge */}
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
                    <span className={`w-1.5 h-1.5 rounded-full ${ex.connected ? "bg-profit" : "bg-loss"}`} aria-hidden />
                    {ex.connected ? "LIVE" : "DOWN"}
                  </span>
                </div>

                {/* Balance section */}
                <div className="pt-2 border-t border-terminal-border/50">
                  <p className="text-[9px] font-mono text-terminal-subtle uppercase tracking-wider">잔고 (USDT)</p>
                  <p className="text-lg font-mono font-semibold tabular-nums mt-0.5 text-terminal-text">
                    ${formatBalance(ex.balance_usdt)}
                  </p>
                  <div className="mt-1.5 h-1 bg-terminal-muted/30 rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-700 bg-accent"
                      style={{ width: `${Math.min(100, ex.pct_of_total)}%` }}
                    />
                  </div>
                  <p className="text-[9px] font-mono text-terminal-subtle mt-0.5 tabular-nums">
                    {ex.pct_of_total.toFixed(1)}% of total
                  </p>
                </div>

                {/* Exchange status */}
                {exStatus && (
                  <div className="grid grid-cols-2 gap-2 pt-2 border-t border-terminal-border/50">
                    <div>
                      <p className="text-[9px] font-mono text-terminal-subtle uppercase tracking-wider">Latency</p>
                      <p className={`text-xs font-mono tabular-nums mt-0.5 ${latencyClassName(exStatus.latency_ms)}`}>
                        {exStatus.latency_ms == null || isNaN(exStatus.latency_ms)
                          ? '—'
                          : exStatus.latency_ms < 1000
                            ? `${exStatus.latency_ms.toFixed(0)}ms`
                            : `${(exStatus.latency_ms / 1000).toFixed(1)}s`}
                      </p>
                    </div>
                    <div>
                      <p className="text-[9px] font-mono text-terminal-subtle uppercase tracking-wider">Symbols</p>
                      <p className="text-xs font-mono text-terminal-text tabular-nums mt-0.5">
                        {exStatus.symbols_count}
                      </p>
                    </div>
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
