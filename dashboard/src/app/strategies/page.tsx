'use client';

import { useApi } from '@/hooks/useApi';
import { getShadowStats, getStrategyMetrics } from '@/lib/api';
import { StrategyPanel } from '@/components/StrategyPanel';
import { SkeletonCard } from '@/components/ui';
import type { ShadowStats, StrategyMetric } from '@/types';

const STRATEGY_TYPES = [
  { type: 'cross_exchange',  label: 'Cross Exchange'  },
  { type: 'futures_futures', label: 'Futures/Futures' },
  { type: 'spot_futures',    label: 'Spot/Futures'    },
  { type: 'triangular',      label: 'Triangular'      },
  { type: 'funding_rate',    label: 'Funding Rate'    },
  { type: 'statistical_arb', label: 'Statistical Arb' },
  { type: 'cex_dex',         label: 'CEX/DEX'         },
] as const;

function scoreColorClass(score: number): string {
  if (score >= 70) return 'text-profit';
  if (score >= 40) return 'text-warn';
  return 'text-loss';
}

function scoreBgClass(score: number): string {
  if (score >= 70) return 'bg-profit';
  if (score >= 40) return 'bg-warn';
  return 'bg-loss';
}

function calcScore(
  breakdown: { trades: number; win_rate: number } | undefined,
  metric: StrategyMetric | undefined,
) {
  const wrScore   = breakdown && breakdown.trades > 0 ? Math.min(40, breakdown.win_rate * 40) : 0;
  const fillRate  = metric && metric.trade_requests > 0 ? metric.fills / metric.trade_requests : 0;
  const fillScore = Math.min(30, fillRate * 30);
  const sigScore  = (metric?.signals_received ?? 0) > 0 ? 15 : 0;
  const errScore  = (metric?.enabled ?? false) ? 15 : 0;
  return {
    total:     Math.round(wrScore + fillScore + sigScore + errScore),
    wrScore:   Math.round(wrScore),
    fillScore: Math.round(fillScore),
    sigScore,
    errScore,
  };
}

export default function StrategiesPage() {
  const { data: shadow, isLoading: shadowLoading } = useApi<ShadowStats>(
    '/shadow/stats',
    getShadowStats,
    { refreshInterval: 10_000 },
  );

  const { data: metricsData, isLoading: metricsLoading } = useApi<{ strategies: Record<string, StrategyMetric> }>(
    '/strategy-metrics',
    getStrategyMetrics,
    { refreshInterval: 10_000 },
  );

  const isLoading = shadowLoading || metricsLoading;
  const metricsMap = metricsData?.strategies ?? {};

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-mono font-semibold text-terminal-text">Strategies</h2>
          <p className="text-xs font-mono text-terminal-subtle mt-1">
            Health scores &amp; strategy management
          </p>
        </div>
        {!isLoading && shadow && (
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-mono text-terminal-subtle">Global WR</span>
            <span className={`text-sm font-mono tabular-nums ${
              shadow.win_rate >= 0.6 ? 'text-profit' :
              shadow.win_rate >= 0.4 ? 'text-warn'   : 'text-loss'
            }`}>
              {(shadow.win_rate * 100).toFixed(0)}%
            </span>
          </div>
        )}
      </div>

      {/* Health score cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
        {STRATEGY_TYPES.map(({ type, label }) => {
          const breakdown = shadow?.by_strategy?.find(
            s => s.strategy_id === type || s.strategy_id.includes(type),
          );
          const metric = Object.values(metricsMap).find(
            m => m.type === type || m.id.includes(type),
          );

          const enabled  = metric?.enabled ?? false;
          const hasData  = breakdown !== undefined || metric !== undefined;
          const { total, wrScore, fillScore, sigScore, errScore } = calcScore(breakdown, metric);

          return (
            <div
              key={type}
              className={`bg-terminal-surface border p-4 space-y-3 transition-opacity ${
                enabled ? 'border-terminal-border' : 'border-terminal-border/30 opacity-60'
              }`}
            >
              {/* Header row */}
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-[10px] font-mono text-terminal-text truncate">{label}</p>
                  <p className="text-[9px] font-mono text-terminal-subtle/60 mt-0.5 truncate">{type}</p>
                </div>
                <span className={`shrink-0 text-[9px] font-mono px-1.5 py-0.5 border ${
                  enabled
                    ? 'border-profit/40 text-profit bg-profit/10'
                    : 'border-terminal-muted/50 text-terminal-subtle'
                }`}>
                  {enabled ? 'ACTIVE' : 'OFF'}
                </span>
              </div>

              {/* Score display */}
              {isLoading ? (
                <div className="space-y-2">
                  <SkeletonCard lines={2} className="border-0 p-0 shadow-none" />
                </div>
              ) : (
                <>
                  <div className="flex items-end gap-1">
                    <span className={`text-2xl font-mono tabular-nums leading-none ${scoreColorClass(total)}`}>
                      {hasData ? total : '—'}
                    </span>
                    {hasData && (
                      <span className="text-[10px] font-mono text-terminal-subtle mb-0.5">/100</span>
                    )}
                  </div>

                  {hasData && (
                    <div className="space-y-1.5">
                      <div className="h-1.5 bg-terminal-muted/30 overflow-hidden">
                        <div
                          className={`h-full transition-all duration-700 ${scoreBgClass(total)}`}
                          style={{ width: `${total}%` }}
                        />
                      </div>
                      <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
                        {[
                          { label: 'WR',   pts: wrScore,   max: 40 },
                          { label: 'Fill', pts: fillScore, max: 30 },
                          { label: 'Sig',  pts: sigScore,  max: 15 },
                          { label: 'Err',  pts: errScore,  max: 15 },
                        ].map(({ label: l, pts, max }) => (
                          <div key={l} className="flex items-center justify-between">
                            <span className="text-[8px] font-mono text-terminal-subtle">{l}</span>
                            <span className="text-[8px] font-mono text-terminal-text tabular-nums">
                              {pts}/{max}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}

              {/* Shadow stats footer */}
              {breakdown && (
                <div className="border-t border-terminal-border/40 pt-2 grid grid-cols-2 gap-x-3 gap-y-0.5">
                  <span className="text-[9px] font-mono text-terminal-subtle">Trades</span>
                  <span className="text-[9px] font-mono text-terminal-text tabular-nums text-right">
                    {breakdown.trades}
                  </span>
                  <span className="text-[9px] font-mono text-terminal-subtle">PnL</span>
                  <span className={`text-[9px] font-mono tabular-nums text-right ${
                    breakdown.pnl >= 0 ? 'text-profit' : 'text-loss'
                  }`}>
                    {breakdown.pnl >= 0 ? '+' : ''}${breakdown.pnl.toFixed(2)}
                  </span>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Strategy management */}
      <StrategyPanel />
    </div>
  );
}
