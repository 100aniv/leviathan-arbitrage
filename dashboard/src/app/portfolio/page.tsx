'use client';

import { useEffect, useState } from 'react';
import { EquityCurve } from '@/components/EquityCurve';
import { getPortfolioMetrics, getPortfolioSummary, getEquityCurve } from '@/lib/api';

interface PortfolioMetrics {
  sharpe_ratio:      number | null;
  max_drawdown_pct:  number;
  calmar_ratio:      number | null;
  win_rate:          number;
  total_trades:      number;
  total_pnl:         number;
}

interface ExchangeBalance {
  exchange_id:   string;
  balance_usdt:  number;
  pct_of_total:  number;
  connected:     boolean;
}

interface PortfolioSummary {
  total_balance_usdt: number;
  exchange_balances:  ExchangeBalance[];
}

const PIE_COLORS = [
  '#22c55e', '#3b82f6', '#eab308', '#ef4444',
  '#a855f7', '#06b6d4', '#f97316', '#ec4899',
];

export default function PortfolioPage() {
  const [metrics, setMetrics] = useState<PortfolioMetrics | null>(null);
  const [summary, setSummary] = useState<PortfolioSummary | null>(null);
  const [curve,   setCurve]   = useState<{ date: string; equity: number; btc_benchmark: number | null }[]>([]);

  useEffect(() => {
    async function load() {
      try {
        const [metricsData, summaryData, curveData] = await Promise.all([
          getPortfolioMetrics().catch(() => null),
          getPortfolioSummary().catch(() => null),
          getEquityCurve().catch(() => null),
        ]);
        if (metricsData) setMetrics(metricsData as PortfolioMetrics);
        if (summaryData) setSummary(summaryData);
        if (curveData) setCurve(curveData.curve ?? []);
      } catch { /* ignore — engine may be offline */ }
    }
    load();
    const interval = setInterval(load, 10_000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-mono font-semibold text-terminal-text">Portfolio</h2>

      {/* Equity Curve */}
      <EquityCurve data={curve} metrics={metrics ?? undefined} />

      {/* Risk Metric Cards */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
        {[
          { label: 'Sharpe Ratio',  value: metrics?.sharpe_ratio?.toFixed(2)                         ?? '—' },
          { label: 'Max Drawdown',  value: metrics ? `${metrics.max_drawdown_pct.toFixed(2)}%`        : '—' },
          { label: 'Calmar Ratio',  value: metrics?.calmar_ratio?.toFixed(2)                          ?? '—' },
          { label: 'Win Rate',      value: metrics ? `${(metrics.win_rate * 100).toFixed(1)}%`        : '—' },
        ].map(({ label, value }) => (
          <div key={label} className="bg-terminal-surface border border-terminal-border p-3">
            <div className="text-[10px] font-mono text-terminal-subtle uppercase tracking-wider">{label}</div>
            <div className="text-lg font-mono text-terminal-text mt-1 tabular-nums">{value}</div>
          </div>
        ))}
      </div>

      {/* Asset Allocation bar */}
      <div className="bg-terminal-surface border border-terminal-border p-4">
        <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">Asset Allocation</span>
        <div className="flex h-6 mt-3 overflow-hidden border border-terminal-border">
          {summary?.exchange_balances?.map((eb, i) => (
            <div
              key={eb.exchange_id}
              style={{ width: `${eb.pct_of_total * 100}%`, backgroundColor: PIE_COLORS[i % PIE_COLORS.length] }}
              className="h-full transition-all"
              title={`${eb.exchange_id}: $${eb.balance_usdt.toLocaleString()} (${(eb.pct_of_total * 100).toFixed(1)}%)`}
            />
          ))}
        </div>
        <div className="flex flex-wrap gap-3 mt-2">
          {summary?.exchange_balances?.map((eb, i) => (
            <div key={eb.exchange_id} className="flex items-center gap-1.5">
              <div className="w-2 h-2" style={{ backgroundColor: PIE_COLORS[i % PIE_COLORS.length] }} />
              <span className="text-[10px] font-mono text-terminal-subtle">
                {eb.exchange_id} ${eb.balance_usdt.toLocaleString()}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Daily Returns placeholder */}
      <div className="bg-terminal-surface border border-terminal-border p-4">
        <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">Daily Returns</span>
        <div className="flex items-center justify-center h-24 mt-2">
          <span className="text-xs font-mono text-terminal-subtle">
            Shadow/Live 운영 이후 누적 데이터가 표시됩니다
          </span>
        </div>
      </div>
    </div>
  );
}
