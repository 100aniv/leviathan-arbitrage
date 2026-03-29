"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { getTrades, getStrategies, getMode } from "@/lib/api";
import { TradeDetail } from "@/components/TradeDetail";
import type { Trade, Strategy } from "@/types";

export default function TradesPage() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [selectedStrategy, setSelectedStrategy] = useState<string>("");
  const [selectedExchange, setSelectedExchange] = useState<string>("");
  const [filterSymbol, setFilterSymbol] = useState<string>("");
  const [dateFrom, setDateFrom] = useState<string>("");
  const [dateTo, setDateTo] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedTrade, setSelectedTrade] = useState<Trade | null>(null);
  const [engineMode, setEngineMode] = useState<string>("unknown");

  useEffect(() => {
    getMode().then((m) => setEngineMode(m.mode ?? m.execution_mode ?? "unknown")).catch(() => {});
  }, []);

  const fetchTrades = useCallback(async () => {
    try {
      const data = await getTrades({
        strategy: selectedStrategy || undefined,
        exchange: selectedExchange || undefined,
        symbol: filterSymbol || undefined,
        from: dateFrom || undefined,
        to: dateTo || undefined,
        limit: 100,
      });
      setTrades(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch trades");
    } finally {
      setLoading(false);
    }
  }, [selectedStrategy, selectedExchange, filterSymbol, dateFrom, dateTo]);

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

  // Derive unique exchange options from loaded trades
  const exchanges = useMemo(
    () => Array.from(new Set(trades.flatMap((t) => [t.buy_exchange, t.sell_exchange]))).sort(),
    [trades]
  );

  const handleExportCsv = () => {
    const headers = [
      "timestamp", "strategy_id", "symbol", "buy_exchange", "sell_exchange",
      "side", "size", "entry_price", "exit_price", "pnl", "status",
      "spread_bps", "fee_usd", "net_pnl",
    ];
    const rows = trades.map((t) => [
      t.timestamp, t.strategy_id, t.symbol, t.buy_exchange, t.sell_exchange,
      t.side, t.size, t.entry_price, t.exit_price, t.pnl, t.status,
      t.spread_bps ?? "", t.fee_usd ?? "", t.net_pnl ?? "",
    ]);
    const escapeCell = (v: unknown) => {
      const s = String(v ?? "");
      return s.includes(",") || s.includes('"') || s.includes("\n") ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const csv = [headers, ...rows].map((r) => r.map(escapeCell).join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `leviathan-trades-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-4">
      {selectedTrade && (
        <TradeDetail trade={selectedTrade} onClose={() => setSelectedTrade(null)} />
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-mono font-semibold text-terminal-text">
            거래 내역
            <span className={`ml-2 px-2 py-0.5 text-[10px] font-bold uppercase rounded ${
              engineMode === "live" ? "bg-red-500/20 text-red-400 border border-red-500/30" :
              engineMode === "shadow" ? "bg-yellow-500/20 text-yellow-400 border border-yellow-500/30" :
              engineMode === "paper" ? "bg-yellow-500/20 text-yellow-400 border border-yellow-500/30" :
              "bg-gray-500/20 text-gray-400 border border-gray-500/30"
            }`}>
              {engineMode}
            </span>
          </h2>
          <p className="text-xs font-mono text-terminal-subtle mt-0.5">
            실행된 아비트라지 거래 · 5초마다 자동 새로고침 · 행 클릭 시 상세 보기
          </p>
        </div>
      </div>

      {/* Filter bar */}
      <div className="bg-terminal-surface border border-terminal-border p-3 flex flex-wrap items-center gap-2">
        <input
          type="date"
          value={dateFrom}
          onChange={(e) => setDateFrom(e.target.value)}
          className="bg-terminal-bg border border-terminal-border text-terminal-text text-xs font-mono px-2 py-1 focus:outline-none focus:border-accent"
        />
        <span className="text-terminal-subtle text-xs font-mono">—</span>
        <input
          type="date"
          value={dateTo}
          onChange={(e) => setDateTo(e.target.value)}
          className="bg-terminal-bg border border-terminal-border text-terminal-text text-xs font-mono px-2 py-1 focus:outline-none focus:border-accent"
        />
        <select
          value={selectedStrategy}
          onChange={(e) => setSelectedStrategy(e.target.value)}
          className="bg-terminal-bg border border-terminal-border text-terminal-text text-xs font-mono px-2 py-1 focus:outline-none focus:border-accent"
        >
          <option value="">전체 전략</option>
          {strategies.map((s) => (
            <option key={s.id} value={s.id}>{s.id}</option>
          ))}
        </select>
        <select
          value={selectedExchange}
          onChange={(e) => setSelectedExchange(e.target.value)}
          className="bg-terminal-bg border border-terminal-border text-terminal-text text-xs font-mono px-2 py-1 focus:outline-none focus:border-accent"
        >
          <option value="">전체 거래소</option>
          {exchanges.map((ex) => (
            <option key={ex} value={ex}>{ex}</option>
          ))}
        </select>
        <input
          type="text"
          value={filterSymbol}
          onChange={(e) => setFilterSymbol(e.target.value)}
          placeholder="Symbol…"
          className="bg-terminal-bg border border-terminal-border text-terminal-text text-xs font-mono px-2 py-1 focus:outline-none focus:border-accent w-24"
        />
        <button
          onClick={handleExportCsv}
          className="ml-auto px-3 py-1 text-xs font-mono border border-terminal-border text-terminal-subtle hover:text-terminal-text hover:border-accent transition-colors"
        >
          CSV 내보내기
        </button>
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
            거래 내역이 없습니다
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="border-b border-terminal-border text-terminal-subtle">
                  <th className="text-left px-4 py-2">시간</th>
                  <th className="text-left px-4 py-2">전략</th>
                  <th className="text-left px-4 py-2">심볼</th>
                  <th className="text-left px-4 py-2">Buy → Sell</th>
                  <th className="text-right px-4 py-2">Size</th>
                  <th className="text-right px-4 py-2">진입가</th>
                  <th className="text-right px-4 py-2">청산가</th>
                  <th className="text-right px-4 py-2">손익</th>
                  <th className="text-left px-4 py-2">상태</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((trade, i) => (
                  <tr
                    key={trade.id}
                    onClick={() => setSelectedTrade(trade)}
                    className={`border-b border-terminal-border/50 hover:bg-terminal-muted/30 transition-colors cursor-pointer ${
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
                          color: trade.status === "closed" ? "#00ff88" : "#f59e0b",
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
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
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
