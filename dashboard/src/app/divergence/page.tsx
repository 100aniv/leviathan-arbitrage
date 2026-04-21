"use client";

import React from "react";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge, type PathBStatus } from "@/components/StatusBadge";
import { DataCard } from "@/components/DataCard";

/**
 * Path-B v2 W3 — Engine vs Exchange PnL divergence.
 * Metric: leviathan_pnl_divergence_usd.
 * Refresh: 10s (DESIGN.md §5).
 */

interface DivergenceSnapshot {
  current_divergence_usd: number;
  p95_24h_usd: number;
  max_24h_usd: number;
  updated_at: string;
  history: { ts: string; divergence_usd: number }[];
}

const PLACEHOLDER: DivergenceSnapshot = {
  current_divergence_usd: 0.0,
  p95_24h_usd: 0.0,
  max_24h_usd: 0.0,
  updated_at: new Date().toISOString(),
  history: [],
};

function gaugeStatus(abs: number): PathBStatus {
  if (abs < 0.5) return "verified";
  if (abs < 2.0) return "pending";
  return "diverged";
}

export default function DivergencePage() {
  const data = PLACEHOLDER;
  const abs = Math.abs(data.current_divergence_usd);
  const status = gaugeStatus(abs);

  // Crude circular gauge via SVG (skeleton; Recharts wired Day 23-27).
  const percent = Math.min(abs / 5.0, 1.0); // 0-5 USD range
  const strokeDashoffset = 188 - 188 * percent;

  return (
    <div className="max-w-7xl mx-auto px-4 md:px-6 py-6">
      <PageHeader
        title="PnL Divergence"
        subtitle="leviathan_pnl_divergence_usd · engine vs exchange income · 10초 새로고침"
        lastUpdated={data.updated_at}
        right={<StatusBadge status={status} pulse={status === "diverged"} />}
      />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
        <div className="bg-bg-surface border border-border rounded-[12px] p-8 flex flex-col items-center justify-center">
          <svg viewBox="0 0 80 80" width="180" height="180" aria-label="divergence gauge">
            <circle cx="40" cy="40" r="30" fill="none" stroke="var(--border)" strokeWidth="8" />
            <circle
              cx="40"
              cy="40"
              r="30"
              fill="none"
              stroke={
                status === "verified" ? "var(--success)" :
                status === "pending"  ? "var(--warning)" :
                                         "var(--danger)"
              }
              strokeWidth="8"
              strokeLinecap="round"
              strokeDasharray="188"
              strokeDashoffset={strokeDashoffset}
              transform="rotate(-90 40 40)"
              style={{ transition: "stroke-dashoffset 400ms ease" }}
            />
            <text
              x="40" y="44" textAnchor="middle"
              fontSize="12" fontFamily="monospace"
              fill="var(--text-primary)"
              fontWeight="600"
            >
              ${data.current_divergence_usd.toFixed(2)}
            </text>
          </svg>
          <div className="mt-4 text-caption text-text-secondary">current divergence (|engine − exchange|)</div>
        </div>

        <div className="space-y-4">
          <DataCard
            label="Current"
            value={`$${data.current_divergence_usd.toFixed(2)}`}
            tone={abs < 0.5 ? "ok" : abs < 2.0 ? "warn" : "bad"}
          />
          <DataCard
            label="p95 (24h)"
            value={`$${data.p95_24h_usd.toFixed(2)}`}
            tone={data.p95_24h_usd < 1.0 ? "ok" : "warn"}
          />
          <DataCard
            label="Max (24h)"
            value={`$${data.max_24h_usd.toFixed(2)}`}
            tone={data.max_24h_usd < 2.0 ? "ok" : "bad"}
          />
        </div>
      </div>

      <h2 className="text-body font-semibold text-text-primary mb-3">24h History</h2>
      <div className="bg-bg-surface border border-border rounded-[12px] p-8 text-center">
        <div className="h-48 flex items-center justify-center text-caption text-text-tertiary">
          Chart placeholder — Recharts line of 24h divergence series wired W3 Day 23-27.
        </div>
      </div>

      <p className="mt-4 text-small text-text-tertiary">
        Skeleton. Backend: /api/v1/divergence/history + Prometheus gauge — W3 Day 23-27.
      </p>
    </div>
  );
}
