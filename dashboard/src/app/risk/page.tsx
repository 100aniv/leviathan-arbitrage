'use client';

import { KillSwitch } from '@/components/KillSwitch';
import { useApi } from '@/hooks/useApi';
import { useEngineWs } from '@/hooks/useEngineWs';
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

  const { connected, data: wsData } = useEngineWs();

  // RiskMetrics has [key: string]: unknown — cast accessed fields explicitly
  const drawdown      = data?.max_drawdown_pct as number | undefined;
  const dailyLoss     = data?.daily_loss_pct as number | undefined;
  const positionCount = (data?.position_count as number | undefined) ?? 0;
  const cbState       = (data?.circuit_breaker_state as string | undefined) ?? 'UNKNOWN';
  const killActive    = (data?.kill_switch_active as boolean | undefined) ?? false;
  const corrAlert     = (data?.correlation_alert as boolean | undefined) ?? false;

  const riskScore = drawdown !== undefined
    ? Math.min(100, Math.round(Math.abs(drawdown) * 10 + positionCount * 5)).toString()
    : '—';

  // Live WS values
  const wsKillSwitch    = wsData?.kill_switch ?? false;
  const wsPositionCount = wsData?.position_count ?? 0;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-mono font-semibold text-terminal-text">리스크 대시보드</h2>
            <span
              className={`text-[9px] font-mono ${connected ? 'text-profit' : 'text-loss'}`}
              title={connected ? 'Engine connected' : 'Engine disconnected'}
            >
              {connected ? '● LIVE' : '● OFFLINE'}
            </span>
          </div>
          <p className="text-xs font-mono text-terminal-subtle mt-1">노출도, 낙폭, 비상 제어</p>

          {/* Live WS status row */}
          <div className="flex items-center gap-4 mt-1">
            <span className="text-[10px] font-mono text-terminal-subtle">
              Kill Switch:{' '}
              <span className={`tabular-nums ${wsKillSwitch ? 'text-loss' : 'text-profit'}`}>
                {wsKillSwitch ? 'ACTIVE' : 'OFF'}
              </span>
            </span>
            <span className="text-[10px] font-mono text-terminal-subtle">
              Positions:{' '}
              <span className="text-terminal-text tabular-nums">{wsPositionCount}</span>
            </span>
          </div>
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
          <p className="card-header">Daily Loss</p>
          <p className={`stat-value ${dailyLoss !== undefined && dailyLoss < 0 ? 'text-loss' : 'text-terminal-subtle'}`}>
            {fmt(dailyLoss, '%')}
          </p>
        </div>
        <div className="card">
          <p className="card-header">Risk Score</p>
          <p className={`stat-value ${riskScore !== '—' && Number(riskScore) > 70 ? 'text-loss' : 'text-terminal-subtle'}`}>
            {riskScore}
          </p>
        </div>
      </div>

      {/* Risk indicators */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="bg-terminal-surface border border-terminal-border p-4">
          <div className="flex items-center justify-between mb-4">
            <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">
              Risk Indicators
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
          ) : (
            <div className="space-y-3">
              {[
                { label: 'Circuit Breaker',  value: cbState,                           color: cbState === 'CLOSED' ? 'text-profit' : 'text-loss' },
                { label: 'Kill Switch',       value: killActive ? 'ACTIVE' : 'OFF',     color: killActive ? 'text-loss' : 'text-profit' },
                { label: 'Position Count',    value: String(positionCount),             color: 'text-terminal-text' },
                { label: 'Correlation Alert', value: corrAlert ? 'TRIGGERED' : 'CLEAR', color: corrAlert ? 'text-loss' : 'text-profit' },
              ].map(({ label, value, color }) => (
                <div key={label} className="flex justify-between items-center text-[11px] font-mono py-1.5 border-b border-terminal-border/20 last:border-b-0">
                  <span className="text-terminal-subtle uppercase tracking-wider">{label}</span>
                  <span className={`tabular-nums ${color}`}>{value}</span>
                </div>
              ))}
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
                  { label: 'Safe',     range: '0–5%',  color: 'text-profit' },
                  { label: 'Warning',  range: '5–10%', color: 'text-warn' },
                  { label: 'Critical', range: '>10%',  color: 'text-loss' },
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
                {error ? '연결에 실패했어요' : '불러오는 중…'}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
