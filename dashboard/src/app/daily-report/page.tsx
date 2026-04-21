"use client";

import React, { useMemo } from "react";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { DataCard } from "@/components/DataCard";
import { DataTable, type DataTableColumn } from "@/components/DataTable";

/**
 * Path-B v2 W3 — Daily report viewer (UTC 00:05 CSV).
 * 6-column variance decomposition + trade log.
 * DESIGN.md §5 — static per day.
 */

interface DailyReportRow {
  category:     string;
  trades:       number;
  gross_usd:    number;
  fees_usd:     number;
  slippage_usd: number;
  rollback_usd: number;
  net_usd:      number;
}

const PLACEHOLDER: { generated_at: string; date: string; rows: DailyReportRow[] } = {
  generated_at: new Date().toISOString(),
  date: new Date().toISOString().slice(0, 10),
  rows: [
    { category: "cross_exchange",  trades: 0, gross_usd: 0, fees_usd: 0, slippage_usd: 0, rollback_usd: 0, net_usd: 0 },
    { category: "spot_futures",    trades: 0, gross_usd: 0, fees_usd: 0, slippage_usd: 0, rollback_usd: 0, net_usd: 0 },
    { category: "triangular",      trades: 0, gross_usd: 0, fees_usd: 0, slippage_usd: 0, rollback_usd: 0, net_usd: 0 },
    { category: "TOTAL",           trades: 0, gross_usd: 0, fees_usd: 0, slippage_usd: 0, rollback_usd: 0, net_usd: 0 },
  ],
};

export default function DailyReportPage() {
  const data = PLACEHOLDER;

  const columns: DataTableColumn<DailyReportRow>[] = useMemo(
    () => [
      { key: "cat",   header: "Category",   align: "left",  render: (r) => <span className={r.category === "TOTAL" ? "font-semibold" : "font-mono"}>{r.category}</span> },
      { key: "tr",    header: "Trades",     align: "right", render: (r) => r.trades.toString() },
      { key: "gross", header: "Gross USD",  align: "right", render: (r) => r.gross_usd.toFixed(2) },
      { key: "fee",   header: "Fees USD",   align: "right", render: (r) => `-${r.fees_usd.toFixed(2)}` },
      { key: "slip",  header: "Slippage",   align: "right", render: (r) => `-${r.slippage_usd.toFixed(2)}` },
      { key: "rb",    header: "Rollback",   align: "right", render: (r) => `-${r.rollback_usd.toFixed(2)}` },
      {
        key: "net",
        header: "Net USD",
        align: "right",
        render: (r) => (
          <span className={r.net_usd >= 0 ? "text-success font-semibold" : "text-danger font-semibold"}>
            {r.net_usd >= 0 ? "+" : ""}
            {r.net_usd.toFixed(2)}
          </span>
        ),
      },
    ],
    [],
  );

  const total = data.rows.find((r) => r.category === "TOTAL");

  return (
    <div className="max-w-7xl mx-auto px-4 md:px-6 py-6">
      <PageHeader
        title="Daily Report"
        subtitle={`UTC 00:05 CSV export · 6-column variance decomposition`}
        lastUpdated={data.generated_at}
        right={<StatusBadge status="pending" label={`DATE ${data.date}`} />}
      />

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
        <DataCard label="Report Date"  value={data.date} />
        <DataCard label="Total Trades" value={total?.trades.toString() ?? "—"} />
        <DataCard
          label="Net PnL"
          value={total ? `$${total.net_usd.toFixed(2)}` : "—"}
          tone={total && total.net_usd >= 0 ? "ok" : "bad"}
        />
        <DataCard
          label="Total Costs"
          value={total ? `$${(total.fees_usd + total.slippage_usd + total.rollback_usd).toFixed(2)}` : "—"}
        />
      </div>

      <div className="flex items-center gap-3 mb-3">
        <h2 className="text-body font-semibold text-text-primary">Variance Decomposition</h2>
        <button
          type="button"
          className="btn-subtle text-small"
          disabled
          aria-label="CSV 다운로드 (W3 Day 23-27 활성화)"
        >
          Download CSV
        </button>
      </div>

      <DataTable<DailyReportRow>
        columns={columns}
        rows={data.rows}
        getRowKey={(r) => r.category}
      />

      <p className="mt-4 text-small text-text-tertiary">
        Skeleton. Backend: /api/v1/daily-report?date=YYYY-MM-DD — W3 Day 23-27.
      </p>
    </div>
  );
}
