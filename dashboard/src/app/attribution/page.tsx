"use client";

import React, { useState, useEffect, useCallback } from "react";
import {
  PieChart,
  Pie,
  Cell,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import { getAttribution } from "@/lib/api";
import type { AttributionBreakdown, AttributionResponse } from "@/types";

type Tab = "strategy" | "exchange" | "pair" | "hour";

const TABS: { key: Tab; label: string }[] = [
  { key: "strategy", label: "전략" },
  { key: "exchange", label: "거래소" },
  { key: "pair",     label: "페어"     },
  { key: "hour",     label: "시간대"     },
];

function fmt(n: number) {
  return `${n >= 0 ? "+" : ""}$${Math.abs(n).toFixed(4)}`;
}

const PIE_COLORS = ['#059669', '#3b82f6', '#f59e0b', '#DC2626', '#a78bfa', '#34d399', '#fb923c', '#60a5fa'];

function 전략PieChart({ items }: { items: AttributionBreakdown[] }) {
  if (items.length === 0) return null;

  const totalAbs = items.reduce((s, i) => s + Math.abs(i.pnl), 0) || 1;
  const pieData = items.map((item, idx) => ({
    name: item.key,
    value: parseFloat(Math.abs(item.pnl).toFixed(4)),
    pct: ((Math.abs(item.pnl) / totalAbs) * 100).toFixed(1),
    profit: item.pnl >= 0,
    color: item.pnl >= 0 ? PIE_COLORS[idx % PIE_COLORS.length] : '#DC2626',
  }));

  return (
    <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4 space-y-3">
      <p className="text-xs font-mono text-terminal-subtle uppercase tracking-wider">전략 Distribution</p>
      <ResponsiveContainer width="100%" height={200}>
        <PieChart>
          <Pie
            data={pieData}
            cx="50%"
            cy="50%"
            innerRadius={55}
            outerRadius={85}
            paddingAngle={2}
            dataKey="value"
            isAnimationActive={false}
          >
            {pieData.map((entry, idx) => (
              <Cell key={idx} fill={entry.color} opacity={0.85} />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{
              background: '#FFFFFF',
              border: '1px solid #E5E7EB',
              borderRadius: 0,
              fontFamily: 'monospace',
              fontSize: 11,
            }}
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            formatter={(value: any, name: any, props: any) => {
              const v = typeof value === 'number' ? value : 0;
              return [`${props.payload?.profit ? '+' : '-'}$${v.toFixed(4)} (${props.payload?.pct}%)`, name ?? ''];
            }}
          />
          <Legend
            iconType="circle"
            iconSize={8}
            formatter={(value: string) => (
              <span style={{ color: '#6B7280', fontSize: 10, fontFamily: 'monospace' }}>{value}</span>
            )}
          />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}

function WaterfallChart({ items }: { items: AttributionBreakdown[] }) {
  if (items.length === 0) return null;
  const maxAbs = Math.max(...items.map((i) => Math.abs(i.pnl)), 0.0001);

  return (
    <div className="space-y-1.5">
      {items.map((item) => {
        const width = (Math.abs(item.pnl) / maxAbs) * 100;
        const color = item.pnl >= 0 ? "#059669" : "#DC2626";
        return (
          <div key={item.key} className="flex items-center gap-3 group">
            <span className="w-28 shrink-0 text-[10px] font-mono text-terminal-subtle truncate text-right">
              {item.key}
            </span>
            <div className="flex-1 h-5 bg-terminal-muted/30 rounded overflow-hidden relative">
              <div
                className="h-full rounded transition-all duration-500"
                style={{ width: `${width}%`, backgroundColor: color, opacity: 0.85 }}
              />
            </div>
            <span
              className="w-24 shrink-0 text-[10px] font-mono tabular-nums text-right"
              style={{ color }}
            >
              {fmt(item.pnl)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function Heatmap({
  pairs,
  exchanges,
}: {
  pairs: AttributionBreakdown[];
  exchanges: AttributionBreakdown[];
}) {
  if (pairs.length === 0 || exchanges.length === 0) {
    return (
      <p className="text-xs font-mono text-terminal-subtle text-center py-4">
        No heatmap data
      </p>
    );
  }

  const maxAbs = Math.max(
    ...pairs.map((p) => Math.abs(p.pnl)),
    ...exchanges.map((e) => Math.abs(e.pnl)),
    0.0001
  );

  // Show top 8 pairs × top 6 exchanges to keep grid manageable
  const display페어s = pairs.slice(0, 8);
  const display거래소s = exchanges.slice(0, 6);

  return (
    <div className="overflow-x-auto">
      <div
        className="inline-grid gap-0.5"
        style={{
          gridTemplateColumns: `auto repeat(${display거래소s.length}, minmax(64px, 1fr))`,
        }}
      >
        {/* Header row */}
        <div className="text-[9px] font-mono text-terminal-subtle px-1 py-1" />
        {display거래소s.map((ex) => (
          <div
            key={ex.key}
            className="text-[9px] font-mono text-terminal-subtle text-center px-1 py-1 truncate"
          >
            {ex.key}
          </div>
        ))}

        {/* Data rows */}
        {display페어s.map((pair) => (
          <React.Fragment key={pair.key}>
            <div
              className="text-[9px] font-mono text-terminal-subtle pr-2 py-1 flex items-center"
            >
              {pair.key}
            </div>
            {display거래소s.map((ex) => {
              // Approximate cell value: pair pnl × exchange pnl share
              const totalPnl = Math.abs(pair.pnl) + Math.abs(ex.pnl);
              const cellPnl = totalPnl > 0 ? (pair.pnl + ex.pnl) / 2 : 0;
              const intensity = Math.min(Math.abs(cellPnl) / maxAbs, 1);
              const bg =
                cellPnl >= 0
                  ? `rgba(5,150,105,${intensity * 0.7})`
                  : `rgba(220,38,38,${intensity * 0.7})`;
              return (
                <div
                  key={`${pair.key}-${ex.key}`}
                  className="h-6 rounded-sm border border-terminal-border/20 text-[8px] font-mono flex items-center justify-center tabular-nums"
                  style={{ backgroundColor: bg, color: intensity > 0.4 ? "#fff" : "#666" }}
                  title={`${pair.key} × ${ex.key}: ${fmt(cellPnl)}`}
                >
                  {cellPnl !== 0 ? (cellPnl >= 0 ? "+" : "-") : "·"}
                </div>
              );
            })}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

function DetailTable({ items }: { items: AttributionBreakdown[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="border-b border-terminal-border text-terminal-subtle">
            <th className="text-left px-4 py-2">Key</th>
            <th className="text-right px-4 py-2">PnL</th>
            <th className="text-right px-4 py-2">Trades</th>
            <th className="text-right px-4 py-2">Win Rate</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, i) => (
            <tr
              key={item.key}
              className={`border-b border-terminal-border/50 hover:bg-terminal-muted/30 transition-colors ${
                i % 2 === 0 ? "" : "bg-terminal-bg/30"
              }`}
            >
              <td className="px-4 py-2 text-terminal-text">{item.key}</td>
              <td
                className="px-4 py-2 text-right tabular-nums font-semibold"
                style={{ color: item.pnl >= 0 ? "#059669" : "#DC2626" }}
              >
                {fmt(item.pnl)}
              </td>
              <td className="px-4 py-2 text-right text-terminal-text tabular-nums">
                {item.trades}
              </td>
              <td className="px-4 py-2 text-right tabular-nums" style={{ color: item.wr * 100 >= 70 ? "#059669" : item.wr * 100 >= 50 ? "#f59e0b" : "#DC2626" }}>
                {(item.wr * 100).toFixed(1)}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function AttributionPage() {
  const [data, setData] = useState<AttributionResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("strategy");

  const fetchData = useCallback(async () => {
    try {
      const result = await getAttribution();
      setData(result);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch attribution data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const safeArr = (v: unknown): AttributionBreakdown[] =>
    Array.isArray(v) ? v : [];

  const getItems = (): AttributionBreakdown[] => {
    if (!data) return [];
    switch (activeTab) {
      case "strategy": return safeArr(data.by_strategy);
      case "exchange": return safeArr(data.by_exchange);
      case "pair":     return safeArr(data.by_pair);
      case "hour":     return safeArr(data.by_hour);
    }
  };

  const items = getItems();
  const strategyArr = safeArr(data?.by_strategy);
  const exchangeArr = safeArr(data?.by_exchange);
  const best전략 = strategyArr.length > 0 ? strategyArr.reduce((a, b) => (b.pnl > a.pnl ? b : a), strategyArr[0]) : null;
  const best거래소 = exchangeArr.length > 0 ? exchangeArr.reduce((a, b) => (b.pnl > a.pnl ? b : a), exchangeArr[0]) : null;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div>
        <h2 className="text-lg font-mono font-semibold text-terminal-text">Attribution</h2>
        <p className="text-xs font-mono text-terminal-subtle mt-0.5">
          PnL breakdown by strategy, exchange, pair &amp; hour · auto-refresh every 5s
        </p>
      </div>

      {loading && !data ? (
        <div className="bg-terminal-surface border border-terminal-border rounded-lg p-8 text-center text-terminal-subtle text-xs font-mono">
          Loading attribution data...
        </div>
      ) : error ? (
        <div
          className="bg-terminal-surface border border-terminal-border rounded-lg p-8 text-center text-xs font-mono"
          style={{ color: "#DC2626" }}
        >
          {error}
        </div>
      ) : !data ? null : (
        <>
          {/* Summary Cards */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[
              {
                label: "Total PnL",
                value: fmt(data.total_pnl),
                color: data.total_pnl >= 0 ? "#059669" : "#DC2626",
              },
              {
                label: "Total Trades",
                value: data.total_trades.toString(),
                color: undefined,
              },
              {
                label: "Best 전략",
                value: best전략?.key ?? "—",
                color: "#059669",
              },
              {
                label: "Best 거래소",
                value: best거래소?.key ?? "—",
                color: "#059669",
              },
            ].map(({ label, value, color }) => (
              <div
                key={label}
                className="bg-terminal-surface border border-terminal-border rounded-lg p-4"
              >
                <p className="text-terminal-subtle text-xs font-mono">{label}</p>
                <p
                  className="text-base font-mono font-semibold tabular-nums mt-1 truncate"
                  style={color ? { color } : undefined}
                >
                  {value}
                </p>
              </div>
            ))}
          </div>

          {/* Tab Bar */}
          <div className="flex gap-1 border-b border-terminal-border">
            {TABS.map(({ key, label }) => (
              <button
                key={key}
                onClick={() => setActiveTab(key)}
                className={`px-4 py-2 text-xs font-mono transition-colors border-b-2 -mb-px ${
                  activeTab === key
                    ? "border-accent text-terminal-text"
                    : "border-transparent text-terminal-subtle hover:text-terminal-text"
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {items.length === 0 ? (
            <div className="bg-terminal-surface border border-terminal-border rounded-lg p-8 text-center text-terminal-subtle text-xs font-mono">
              No data for this dimension
            </div>
          ) : (
            <>
              {/* 전략 PieChart — strategy tab only */}
              {activeTab === "strategy" && <전략PieChart items={items} />}

              {/* Waterfall Chart */}
              <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4 space-y-3">
                <p className="text-xs font-mono text-terminal-subtle uppercase tracking-wider">
                  PnL Waterfall
                </p>
                <WaterfallChart items={items} />
              </div>

              {/* Heatmap (only shown for pair tab) */}
              {activeTab === "pair" && (
                <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4 space-y-3">
                  <p className="text-xs font-mono text-terminal-subtle uppercase tracking-wider">
                    페어 × 거래소 Heatmap
                  </p>
                  <Heatmap pairs={data.by_pair} exchanges={data.by_exchange} />
                </div>
              )}

              {/* Detail Table */}
              <div className="bg-terminal-surface border border-terminal-border rounded-lg overflow-hidden">
                <DetailTable items={items} />
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
