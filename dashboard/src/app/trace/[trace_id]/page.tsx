"use client";

import React from "react";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge, type PathBStatus } from "@/components/StatusBadge";
import { DataCard } from "@/components/DataCard";

/**
 * Path-B v2 W3 — Trace timeline.
 * signal → validate → order → fill → PnL per trace_id.
 * DESIGN.md §5 — static per trace (no refresh).
 */

interface TraceStep {
  label: string;
  ts: string;
  status: PathBStatus;
  detail: string;
}

export default function TracePage({
  params,
}: {
  params: { trace_id: string };
}) {
  const traceId = params.trace_id;

  // Skeleton — real timeline from /api/v1/trace/{id} wired W3 Day 23-27.
  const steps: TraceStep[] = [
    { label: "SIGNAL",    ts: "--:--:--.---", status: "pending", detail: "signal generated · strategy=cross_exchange" },
    { label: "VALIDATE",  ts: "--:--:--.---", status: "pending", detail: "PreTradeValidator · 11 gates" },
    { label: "ORDER",     ts: "--:--:--.---", status: "pending", detail: "leg1 + leg2 dispatched via OrderRouter" },
    { label: "FILL",      ts: "--:--:--.---", status: "pending", detail: "fill prices + actual slippage" },
    { label: "PNL",       ts: "--:--:--.---", status: "pending", detail: "gross − fees − slippage = net" },
  ];

  return (
    <div className="max-w-5xl mx-auto px-4 md:px-6 py-6">
      <PageHeader
        title="Trace Timeline"
        subtitle={`trace_id · ${traceId}`}
        right={<StatusBadge status="pending" />}
      />

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <DataCard label="Trace ID" value={<span className="font-mono text-sm break-all">{traceId}</span>} />
        <DataCard label="Strategy" value="—" subValue="resolved from journal" />
        <DataCard label="Outcome" value="—" subValue="SUCCESS / STRANDED / ROLLED_BACK" />
      </div>

      <ol className="relative border-l-2 border-border pl-6 space-y-6">
        {steps.map((step, i) => (
          <li key={i} className="relative">
            <span
              className="absolute -left-[33px] top-1 w-4 h-4 rounded-full border-2 border-bg-base bg-border"
              aria-hidden
            />
            <div className="bg-bg-surface border border-border rounded-[12px] px-4 py-3">
              <div className="flex items-center justify-between gap-3 mb-1">
                <div className="flex items-center gap-2">
                  <span className="text-small font-mono uppercase tracking-wider text-text-secondary">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span className="text-body font-semibold text-text-primary">
                    {step.label}
                  </span>
                  <StatusBadge status={step.status} />
                </div>
                <span className="text-small font-mono tabular-nums text-text-tertiary">
                  {step.ts}
                </span>
              </div>
              <p className="text-caption text-text-secondary">{step.detail}</p>
            </div>
          </li>
        ))}
      </ol>

      <p className="mt-6 text-small text-text-tertiary">
        Skeleton. Backend: /api/v1/trace/{traceId} — W3 Day 23-27.
      </p>
    </div>
  );
}
