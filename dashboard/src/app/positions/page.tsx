"use client";

import React, { useMemo } from "react";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { DataCard } from "@/components/DataCard";
import { DataTable, type DataTableColumn } from "@/components/DataTable";

/**
 * Path-B v2 W3 — Hedge-pair aggregated positions.
 * Source: /api/v1/positions/hedge-pairs (Day 3 WS-C2).
 * Refresh: 5s (DESIGN.md §5).
 */

interface HedgePair {
  symbol: string;
  binance_leg_size: number;
  binance_leg_side: "long" | "short" | "flat";
  bitget_leg_size: number;
  bitget_leg_side: "long" | "short" | "flat";
  net_unrealized_usd: number;
  updated_at: string;
}

const PLACEHOLDER: { pairs: HedgePair[]; updated_at: string } = {
  updated_at: new Date().toISOString(),
  pairs: [
    { symbol: "BTCUSDT", binance_leg_size: 0.00, binance_leg_side: "flat", bitget_leg_size: 0.00, bitget_leg_side: "flat", net_unrealized_usd: 0.00, updated_at: new Date().toISOString() },
    { symbol: "ETHUSDT", binance_leg_size: 0.00, binance_leg_side: "flat", bitget_leg_size: 0.00, bitget_leg_side: "flat", net_unrealized_usd: 0.00, updated_at: new Date().toISOString() },
    { symbol: "SOLUSDT", binance_leg_size: 0.00, binance_leg_side: "flat", bitget_leg_size: 0.00, bitget_leg_side: "flat", net_unrealized_usd: 0.00, updated_at: new Date().toISOString() },
  ],
};

export default function PositionsPage() {
  const data = PLACEHOLDER;
  const totalNet = data.pairs.reduce((s, p) => s + p.net_unrealized_usd, 0);
  const openCount = data.pairs.filter(
    (p) => p.binance_leg_side !== "flat" || p.bitget_leg_side !== "flat",
  ).length;

  const columns: DataTableColumn<HedgePair>[] = useMemo(
    () => [
      { key: "symbol", header: "Symbol", align: "left", render: (r) => <span className="font-mono font-medium">{r.symbol}</span> },
      {
        key: "binance",
        header: "Binance Leg",
        align: "right",
        render: (r) => (
          <span className="font-mono">
            {r.binance_leg_side.toUpperCase()} {r.binance_leg_size.toFixed(6)}
          </span>
        ),
      },
      {
        key: "bitget",
        header: "Bitget Leg",
        align: "right",
        render: (r) => (
          <span className="font-mono">
            {r.bitget_leg_side.toUpperCase()} {r.bitget_leg_size.toFixed(6)}
          </span>
        ),
      },
      {
        key: "net",
        header: "Net Unrealized USD",
        align: "right",
        render: (r) => (
          <span className={r.net_unrealized_usd >= 0 ? "text-success" : "text-danger"}>
            {r.net_unrealized_usd >= 0 ? "+" : ""}
            {r.net_unrealized_usd.toFixed(2)}
          </span>
        ),
      },
    ],
    [],
  );

  return (
    <div className="max-w-7xl mx-auto px-4 md:px-6 py-6">
      <PageHeader
        title="Hedge-Pair Positions"
        subtitle="Binance + Bitget leg 집계 · 5초 새로고침"
        lastUpdated={data.updated_at}
        right={<StatusBadge status="pending" label="LOADING" />}
      />

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <DataCard label="Open Pairs" value={openCount.toString()} subValue={`${data.pairs.length} universe`} />
        <DataCard
          label="Net Unrealized"
          value={`$${totalNet.toFixed(2)}`}
          tone={totalNet >= 0 ? "ok" : "bad"}
        />
        <DataCard label="Last Sync" value={new Date(data.updated_at).toLocaleTimeString()} />
      </div>

      <DataTable<HedgePair>
        columns={columns}
        rows={data.pairs}
        getRowKey={(r) => r.symbol}
        emptyLabel="활성 헤지 페어 없음"
      />

      <p className="mt-4 text-small text-text-tertiary">
        Skeleton. Backend: /api/v1/positions/hedge-pairs — W3 Day 23-27.
      </p>
    </div>
  );
}
