"use client";

import React, { useMemo } from "react";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge, type PathBStatus } from "@/components/StatusBadge";
import { DataCard } from "@/components/DataCard";
import { DataTable, type DataTableColumn } from "@/components/DataTable";

/**
 * Path-B v2 W3 — Strategy health dashboard.
 * Per-strategy: last_signal, WR 24h, budget_remaining, is_halted.
 * Refresh: 30s (DESIGN.md §5).
 */

interface StrategyHealthRow {
  strategy: string;
  last_signal_at: string | null;
  wr_24h_pct: number;
  budget_remaining_usd: number;
  budget_cap_usd: number;
  is_halted: boolean;
}

const STRATEGIES: StrategyHealthRow[] = [
  { strategy: "cross_exchange",     last_signal_at: null, wr_24h_pct: 0, budget_remaining_usd: 0,  budget_cap_usd: 1000, is_halted: false },
  { strategy: "spot_futures",       last_signal_at: null, wr_24h_pct: 0, budget_remaining_usd: 0,  budget_cap_usd: 1000, is_halted: false },
  { strategy: "futures_futures",    last_signal_at: null, wr_24h_pct: 0, budget_remaining_usd: 0,  budget_cap_usd: 1000, is_halted: false },
  { strategy: "triangular",         last_signal_at: null, wr_24h_pct: 0, budget_remaining_usd: 0,  budget_cap_usd: 1000, is_halted: false },
  { strategy: "funding_rate",       last_signal_at: null, wr_24h_pct: 0, budget_remaining_usd: 0,  budget_cap_usd: 1000, is_halted: false },
  { strategy: "statistical_arb",    last_signal_at: null, wr_24h_pct: 0, budget_remaining_usd: 0,  budget_cap_usd: 1000, is_halted: false },
  { strategy: "latency_arb",        last_signal_at: null, wr_24h_pct: 0, budget_remaining_usd: 0,  budget_cap_usd: 1000, is_halted: false },
  { strategy: "cex_dex",            last_signal_at: null, wr_24h_pct: 0, budget_remaining_usd: 0,  budget_cap_usd: 1000, is_halted: false },
];

function healthStatus(row: StrategyHealthRow): PathBStatus {
  if (row.is_halted) return "halted";
  if (row.budget_remaining_usd <= 0) return "warn";
  if (!row.last_signal_at) return "pending";
  return "ok";
}

export default function StrategyHealthPage() {
  const rows = STRATEGIES;
  const haltedCount = rows.filter((r) => r.is_halted).length;
  const activeCount = rows.filter((r) => !r.is_halted && r.last_signal_at).length;

  const columns: DataTableColumn<StrategyHealthRow>[] = useMemo(
    () => [
      { key: "name",   header: "Strategy",   align: "left",  render: (r) => <span className="font-mono">{r.strategy}</span> },
      { key: "last",   header: "Last Signal",align: "left",  render: (r) => <span className="text-small text-text-secondary">{r.last_signal_at ?? "—"}</span> },
      { key: "wr",     header: "WR 24h",     align: "right", render: (r) => `${r.wr_24h_pct.toFixed(1)}%` },
      {
        key: "budget",
        header: "Budget Remaining",
        align: "right",
        render: (r) => (
          <span className="font-mono">
            ${r.budget_remaining_usd.toFixed(0)} / ${r.budget_cap_usd.toFixed(0)}
          </span>
        ),
      },
      { key: "status", header: "Status", align: "center", render: (r) => <StatusBadge status={healthStatus(r)} /> },
    ],
    [],
  );

  return (
    <div className="max-w-7xl mx-auto px-4 md:px-6 py-6">
      <PageHeader
        title="Strategy Health"
        subtitle="전략별 last_signal · WR 24h · budget_remaining · halt 상태"
        lastUpdated={new Date()}
      />

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
        <DataCard label="Total Strategies" value={rows.length.toString()} />
        <DataCard label="Active"  value={activeCount.toString()} tone={activeCount > 0 ? "ok" : "warn"} />
        <DataCard label="Halted"  value={haltedCount.toString()} tone={haltedCount > 0 ? "bad" : "ok"} />
        <DataCard label="Idle"    value={(rows.length - activeCount - haltedCount).toString()} />
      </div>

      <DataTable<StrategyHealthRow>
        columns={columns}
        rows={rows}
        getRowKey={(r) => r.strategy}
      />

      <p className="mt-4 text-small text-text-tertiary">
        Skeleton. Backend: /api/v1/strategy/health — W3 Day 23-27.
      </p>
    </div>
  );
}
