"use client";

import React, { useMemo } from "react";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge, type PathBStatus } from "@/components/StatusBadge";
import { DataCard } from "@/components/DataCard";
import { DataTable, type DataTableColumn } from "@/components/DataTable";

/**
 * Path-B v2 W3 — Engine-vs-exchange reconciliation.
 * Source: PnLReconciler + ExchangePnLSnapshot.
 * Refresh: 60s (DESIGN.md §5).
 */

interface ReconRow {
  exchange:         string;
  engine_pnl_usd:   number;
  exchange_pnl_usd: number;
  divergence_usd:   number;
  matched_trades:   number;
  unmatched_trades: number;
  last_sync_at:     string | null;
}

interface UnmatchedEvent {
  ts:       string;
  side:     "engine_only" | "exchange_only";
  exchange: string;
  symbol:   string;
  amount_usd: number;
}

const PLACEHOLDER: { rows: ReconRow[]; unmatched: UnmatchedEvent[]; updated_at: string } = {
  updated_at: new Date().toISOString(),
  rows: [
    { exchange: "binance",  engine_pnl_usd: 0, exchange_pnl_usd: 0, divergence_usd: 0, matched_trades: 0, unmatched_trades: 0, last_sync_at: null },
    { exchange: "bybit",    engine_pnl_usd: 0, exchange_pnl_usd: 0, divergence_usd: 0, matched_trades: 0, unmatched_trades: 0, last_sync_at: null },
    { exchange: "okx",      engine_pnl_usd: 0, exchange_pnl_usd: 0, divergence_usd: 0, matched_trades: 0, unmatched_trades: 0, last_sync_at: null },
    { exchange: "bitget",   engine_pnl_usd: 0, exchange_pnl_usd: 0, divergence_usd: 0, matched_trades: 0, unmatched_trades: 0, last_sync_at: null },
    { exchange: "upbit",    engine_pnl_usd: 0, exchange_pnl_usd: 0, divergence_usd: 0, matched_trades: 0, unmatched_trades: 0, last_sync_at: null },
    { exchange: "bithumb",  engine_pnl_usd: 0, exchange_pnl_usd: 0, divergence_usd: 0, matched_trades: 0, unmatched_trades: 0, last_sync_at: null },
    { exchange: "coinone",  engine_pnl_usd: 0, exchange_pnl_usd: 0, divergence_usd: 0, matched_trades: 0, unmatched_trades: 0, last_sync_at: null },
  ],
  unmatched: [],
};

function rowStatus(row: ReconRow): PathBStatus {
  const abs = Math.abs(row.divergence_usd);
  if (row.unmatched_trades > 0) return "diverged";
  if (abs < 0.5) return "verified";
  if (abs < 2.0) return "pending";
  return "diverged";
}

export default function ReconciliationPage() {
  const data = PLACEHOLDER;
  const totalDiv = data.rows.reduce((s, r) => s + Math.abs(r.divergence_usd), 0);
  const totalUnmatched = data.rows.reduce((s, r) => s + r.unmatched_trades, 0);

  const columns: DataTableColumn<ReconRow>[] = useMemo(
    () => [
      { key: "ex",    header: "Exchange",       align: "left",  render: (r) => <span className="font-mono">{r.exchange}</span> },
      { key: "eng",   header: "Engine PnL",     align: "right", render: (r) => `$${r.engine_pnl_usd.toFixed(2)}` },
      { key: "exch",  header: "Exchange PnL",   align: "right", render: (r) => `$${r.exchange_pnl_usd.toFixed(2)}` },
      {
        key: "div",
        header: "Divergence",
        align: "right",
        render: (r) => {
          const abs = Math.abs(r.divergence_usd);
          const cls = abs < 0.5 ? "text-success" : abs < 2.0 ? "text-warning" : "text-danger";
          return (
            <span className={cls}>
              {r.divergence_usd >= 0 ? "+" : ""}
              ${r.divergence_usd.toFixed(2)}
            </span>
          );
        },
      },
      { key: "mt", header: "Matched",   align: "right", render: (r) => r.matched_trades.toString() },
      { key: "ut", header: "Unmatched", align: "right", render: (r) => r.unmatched_trades.toString() },
      { key: "st", header: "Status",    align: "center", render: (r) => <StatusBadge status={rowStatus(r)} /> },
    ],
    [],
  );

  const unmatchedColumns: DataTableColumn<UnmatchedEvent>[] = useMemo(
    () => [
      { key: "ts",       header: "Timestamp", align: "left",  render: (r) => <span className="font-mono text-small">{r.ts}</span> },
      { key: "side",     header: "Side",      align: "left",  render: (r) => <span className="font-mono">{r.side}</span> },
      { key: "exchange", header: "Exchange",  align: "left",  render: (r) => r.exchange },
      { key: "symbol",   header: "Symbol",    align: "left",  render: (r) => r.symbol },
      { key: "amt",      header: "Amount USD",align: "right", render: (r) => r.amount_usd.toFixed(2) },
    ],
    [],
  );

  return (
    <div className="max-w-7xl mx-auto px-4 md:px-6 py-6">
      <PageHeader
        title="Reconciliation"
        subtitle="Engine ↔ 거래소 PnL 정합성 · 60초 새로고침"
        lastUpdated={data.updated_at}
        right={<StatusBadge status={totalUnmatched === 0 && totalDiv < 1.0 ? "verified" : "pending"} />}
      />

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
        <DataCard label="Exchanges" value={data.rows.length.toString()} />
        <DataCard
          label="Abs Divergence Σ"
          value={`$${totalDiv.toFixed(2)}`}
          tone={totalDiv < 1.0 ? "ok" : "bad"}
        />
        <DataCard
          label="Unmatched Trades"
          value={totalUnmatched.toString()}
          tone={totalUnmatched === 0 ? "ok" : "bad"}
        />
        <DataCard
          label="Last Sync"
          value={new Date(data.updated_at).toLocaleTimeString()}
        />
      </div>

      <h2 className="text-body font-semibold text-text-primary mb-3">Per-Exchange Reconciliation</h2>
      <DataTable<ReconRow>
        columns={columns}
        rows={data.rows}
        getRowKey={(r) => r.exchange}
      />

      <h2 className="text-body font-semibold text-text-primary mt-8 mb-3">Unmatched Events</h2>
      <DataTable<UnmatchedEvent>
        columns={unmatchedColumns}
        rows={data.unmatched}
        emptyLabel="매칭되지 않은 이벤트 없음"
      />

      <p className="mt-4 text-small text-text-tertiary">
        Skeleton. Backend: /api/v1/reconciliation + /api/v1/reconciliation/unmatched — W3 Day 23-27.
      </p>
    </div>
  );
}
