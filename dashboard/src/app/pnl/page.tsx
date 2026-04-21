"use client";

import React, { useMemo } from "react";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { DataCard } from "@/components/DataCard";
import { DataTable, type DataTableColumn } from "@/components/DataTable";

/**
 * Path-B v2 W3 — Verified PnL page.
 * Source: PnLLedger.get_live_pnl_usd() via /api/v1/pnl/attributed (7-layer TCA).
 * Refresh: 30s (DESIGN.md §5).
 * Backend wiring: W3 Day 23-27.
 */

interface PnLAttribution {
  layer: string;
  usd: number;
  pct: number;
}

interface PnLPageData {
  verified_pnl_usd: number;
  engine_pnl_usd: number;
  divergence_usd: number;
  updated_at: string;
  layers: PnLAttribution[];
}

// Skeleton placeholder until /api/v1/pnl/attributed wires (Day 23-27).
const PLACEHOLDER: PnLPageData = {
  verified_pnl_usd: 0.0,
  engine_pnl_usd: 0.0,
  divergence_usd: 0.0,
  updated_at: new Date().toISOString(),
  layers: [
    { layer: "gross_spread",   usd: 0, pct: 0 },
    { layer: "fee_buy",        usd: 0, pct: 0 },
    { layer: "fee_sell",       usd: 0, pct: 0 },
    { layer: "slippage_buy",   usd: 0, pct: 0 },
    { layer: "slippage_sell",  usd: 0, pct: 0 },
    { layer: "network_cost",   usd: 0, pct: 0 },
    { layer: "rollback_cost",  usd: 0, pct: 0 },
  ],
};

export default function PnLPage() {
  const data = PLACEHOLDER;

  const columns: DataTableColumn<PnLAttribution>[] = useMemo(
    () => [
      { key: "layer", header: "레이어 (Layer)", align: "left",  render: (r) => <span className="font-mono">{r.layer}</span> },
      { key: "usd",   header: "USD",             align: "right", render: (r) => r.usd.toFixed(2) },
      { key: "pct",   header: "% of Gross",      align: "right", render: (r) => `${r.pct.toFixed(2)}%` },
    ],
    [],
  );

  const divergenceStatus = Math.abs(data.divergence_usd) < 0.5
    ? "verified"
    : Math.abs(data.divergence_usd) < 2.0
      ? "pending"
      : "diverged";

  return (
    <div className="max-w-7xl mx-auto px-4 md:px-6 py-6">
      <PageHeader
        title="Verified PnL"
        subtitle="PnLLedger (exchange income 기반) + 7-layer 분산 분해"
        lastUpdated={data.updated_at}
        right={<StatusBadge status={divergenceStatus} />}
      />

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <DataCard
          label="Verified PnL"
          value={`$${data.verified_pnl_usd.toFixed(2)}`}
          tone={data.verified_pnl_usd >= 0 ? "ok" : "bad"}
          subValue="PnLLedger source"
        />
        <DataCard
          label="Engine PnL"
          value={`$${data.engine_pnl_usd.toFixed(2)}`}
          subValue="engine self-reported"
        />
        <DataCard
          label="Divergence"
          value={`$${data.divergence_usd.toFixed(2)}`}
          tone={Math.abs(data.divergence_usd) < 0.5 ? "ok" : "bad"}
          subValue="engine − exchange"
        />
      </div>

      <h2 className="text-body font-semibold text-text-primary mb-3">
        Variance Decomposition (7-layer TCA)
      </h2>
      <DataTable
        columns={columns}
        rows={data.layers}
        emptyLabel="PnL 분산 분해 데이터를 불러오는 중입니다"
      />

      <p className="mt-4 text-small text-text-tertiary">
        Skeleton (W3 Day 22). Backend wiring: /api/v1/pnl/attributed — W3 Day 23-27.
      </p>
    </div>
  );
}
