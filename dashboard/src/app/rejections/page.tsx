"use client";

import React, { useMemo } from "react";
import { PageHeader } from "@/components/PageHeader";
import { DataCard } from "@/components/DataCard";
import { DataTable, type DataTableColumn } from "@/components/DataTable";

/**
 * Path-B v2 W3 — Rejections dashboard.
 * 16 ReasonCode counts + top 10 recent rejections.
 * Refresh: 10s (DESIGN.md §5).
 * Source: OPERATOR_RUNBOOK.md 16 ReasonCode dictionary.
 */

const REASON_CODES = [
  "EDGE_TOO_SMALL",
  "EDGE_EVAPORATED",
  "THIN_BOOK",
  "MARKET_IMPACT_HIGH",
  "PRICE_STALE",
  "NET_EXPOSURE_CAP",
  "POSITION_LIMIT",
  "DRAWDOWN_LIMIT",
  "CIRCUIT_BREAKER",
  "EXCHANGE_UNHEALTHY",
  "VOLATILITY_HALT",
  "ROLLBACK_BUDGET",
  "STRATEGY_CORRELATION",
  "MIN_NOTIONAL",
  "TICK_MISALIGN",
  "KILL_SWITCH",
] as const;

interface ReasonRow {
  code:  (typeof REASON_CODES)[number];
  count: number;
}

interface RecentRejection {
  ts:       string;
  code:     string;
  symbol:   string;
  strategy: string;
  detail:   string;
}

const PLACEHOLDER: { reasons: ReasonRow[]; recent: RecentRejection[]; updated_at: string } = {
  updated_at: new Date().toISOString(),
  reasons: REASON_CODES.map((c) => ({ code: c, count: 0 })),
  recent: [
    { ts: "--:--:--", code: "—", symbol: "—", strategy: "—", detail: "no recent rejections" },
  ],
};

export default function RejectionsPage() {
  const data = PLACEHOLDER;
  const total = data.reasons.reduce((s, r) => s + r.count, 0);
  const topReason = [...data.reasons].sort((a, b) => b.count - a.count)[0];

  const reasonColumns: DataTableColumn<ReasonRow>[] = useMemo(
    () => [
      { key: "code",  header: "Reason Code", align: "left",  render: (r) => <span className="font-mono">{r.code}</span> },
      { key: "count", header: "Count",       align: "right", render: (r) => r.count.toString() },
      {
        key: "share",
        header: "% of Total",
        align: "right",
        render: (r) => `${total === 0 ? "0.0" : ((r.count / total) * 100).toFixed(1)}%`,
      },
    ],
    [total],
  );

  const recentColumns: DataTableColumn<RecentRejection>[] = useMemo(
    () => [
      { key: "ts",       header: "Timestamp", align: "left", render: (r) => <span className="font-mono text-small">{r.ts}</span> },
      { key: "code",     header: "Code",      align: "left", render: (r) => <span className="font-mono text-warning">{r.code}</span> },
      { key: "symbol",   header: "Symbol",    align: "left", render: (r) => <span className="font-mono">{r.symbol}</span> },
      { key: "strategy", header: "Strategy",  align: "left", render: (r) => r.strategy },
      { key: "detail",   header: "Detail",    align: "left", render: (r) => <span className="text-text-secondary text-small">{r.detail}</span> },
    ],
    [],
  );

  return (
    <div className="max-w-7xl mx-auto px-4 md:px-6 py-6">
      <PageHeader
        title="Rejections"
        subtitle="16 ReasonCode counts + 최근 10건 · 10초 새로고침"
        lastUpdated={data.updated_at}
      />

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
        <DataCard label="Total Rejections (24h)" value={total.toString()} />
        <DataCard label="Unique Codes" value={data.reasons.filter((r) => r.count > 0).length.toString()} />
        <DataCard label="Top Reason" value={topReason?.code ?? "—"} />
        <DataCard label="Top Count" value={topReason?.count.toString() ?? "0"} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div>
          <h2 className="text-body font-semibold text-text-primary mb-3">
            Reason Code Counts (24h)
          </h2>
          <DataTable<ReasonRow>
            columns={reasonColumns}
            rows={data.reasons}
            getRowKey={(r) => r.code}
          />
        </div>

        <div>
          <h2 className="text-body font-semibold text-text-primary mb-3">
            Recent Rejections (top 10)
          </h2>
          <DataTable<RecentRejection>
            columns={recentColumns}
            rows={data.recent}
            emptyLabel="거부 로그 없음"
          />
        </div>
      </div>

      <p className="mt-4 text-small text-text-tertiary">
        Skeleton. Backend: /api/v1/rejections + /api/v1/rejections/recent — W3 Day 23-27.
      </p>
    </div>
  );
}
