'use client';

import { KillSwitch } from '@/components/KillSwitch';
import { useApi } from '@/hooks/useApi';
import { getRiskMetrics } from '@/lib/api';
import type { RiskMetrics } from '@/types';

function fmt(n: number | undefined, suffix = '') {
  if (n === undefined || n === null) return '—';
  return `${n.toFixed(2)}${suffix}`;
}

export default function RiskPage() {
  const { data, error, isLoading, mutate } = useApi<RiskMetrics>(
    '/risk/metrics',
    getRiskMetrics,
    { refreshInterval: 5000 },
  );

  const drawdown = data?.drawdown;
  const exposureByExchange = data?.exposure_by_exchange ?? {};
  const totalExposure = Object.values(exposureByExchange).reduce((a, b) => a + b, 0);
  const riskScore = drawdown !== undefined
    ? Math.min(100, Math.round(Math.abs(drawdown) * 10 + (totalExposure / 1000))).toString()
    : '—';

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-mono font-semibold text-terminal-text">Risk Dashboard</h2>
          <p className="text-xs font-mono text-terminal-subtle mt-1">Exposure, drawdown, and emergency controls</p>
        </div>
        <KillSwitch />
      </div>

      {/* Risk metrics */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="card">
          <p className="card-header">Max Drawdown</p>
          <p className={`stat-value ${drawdown !== undefined && drawdown < 0 ? 'text-loss' : 'text-terminal-subtle'}`}>
            {fmt(drawdown, '%')}
          </p>
        </div>
        <div className="card">
          <p className="card-header">Total Exposure</p>
          <p className="stat-value text-terminal-text">
            {totalExposure > 0 ? `$${totalExposure.toFixed(2)}` : '—'}
            {totalExposure > 0 && <span className="text-sm ml-1 text-terminal-subtle">USDT</span>}
          </p>
        </div>
        <div className="card">
          <p className="card-header">Risk Score</p>
          <p className={`stat-value ${riskScore !== '—' && Number(riskScore) > 70 ? 'text-loss' : 'text-terminal-subtle'}`}>
            {riskScore}
          </p>
        </div>
      </div>

      {/* Exposure by exchange */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="bg-terminal-surface border border-terminal-border p-4">
          <div className="flex items-center justify-between mb-4">
            <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">
              Exposure by Exchange
            </span>
            {!isLoading && !error && (
              <span className="text-[9px] font-mono text-profit animate-pulse">● LIVE</span>
            )}
          </div>

          {error ? (
            <div className="flex flex-col items-center gap-2 py-8">
              <p className="text-xs font-mono text-loss">Failed to load risk metrics</p>
              <button
                onClick={() => mutate()}
                className="text-[10px] font-mono border border-terminal-border px-3 py-1 text-terminal-subtle hover:text-terminal-text transition-colors"
              >
                Retry
              </button>
            </div>
          ) : isLoading && !data ? (
            <div className="space-y-2">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="h-8 bg-terminal-muted/40 animate-pulse border border-terminal-border/30" />
              ))}
            </div>
          ) : Object.keys(exposureByExchange).length === 0 ? (
            <p className="text-xs font-mono text-terminal-subtle py-8 text-center">No exposure data</p>
          ) : (
            <div className="space-y-2">
              {Object.entries(exposureByExchange).map(([exchange, exposure]) => {
                const pct = totalExposure > 0 ? (exposure / totalExposure) * 100 : 0;
                return (
                  <div key={exchange}>
                    <div className="flex justify-between text-[11px] font-mono mb-1">
                      <span className="text-terminal-subtle uppercase tracking-wider">{exchange}</span>
                      <span className="text-terminal-text tabular-nums">${exposure.toFixed(2)}</span>
                    </div>
                    <div className="h-1.5 bg-terminal-muted/40 border border-terminal-border/30">
                      <div
                        className="h-full bg-profit/60 transition-all duration-500"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Drawdown indicator */}
        <div className="bg-terminal-surface border border-terminal-border p-4">
          <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle block mb-4">
            Drawdown Monitor
          </span>
          {drawdown !== undefined ? (
            <div className="space-y-4">
              <div>
                <div className="flex justify-between text-[11px] font-mono mb-1">
                  <span className="text-terminal-subtle">Current Drawdown</span>
                  <span className={drawdown < 0 ? 'text-loss tabular-nums' : 'text-profit tabular-nums'}>
                    {fmt(drawdown, '%')}
                  </span>
                </div>
                <div className="h-2 bg-terminal-muted/40 border border-terminal-border/30">
                  <div
                    className="h-full transition-all duration-500"
                    style={{
                      width: `${Math.min(100, Math.abs(drawdown))}%`,
                      background: drawdown < -10 ? '#ff4d4d' : drawdown < -5 ? '#f59e0b' : '#00ff88',
                    }}
                  />
                </div>
              </div>
              <div className="grid grid-cols-3 gap-2 text-center">
                {[
                  { label: 'Safe',     range: '0–5%',   color: 'text-profit' },
                  { label: 'Warning',  range: '5–10%',  color: 'text-warn' },
                  { label: 'Critical', range: '>10%',   color: 'text-loss' },
                ].map(({ label, range, color }) => (
                  <div key={label} className="bg-terminal-bg border border-terminal-border/30 p-2">
                    <div className={`text-[10px] font-mono uppercase tracking-wider ${color}`}>{label}</div>
                    <div className="text-[10px] font-mono text-terminal-subtle mt-0.5">{range}</div>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-center py-12">
              <p className="text-xs font-mono text-terminal-subtle">
                {error ? 'Connection error' : 'Loading…'}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
