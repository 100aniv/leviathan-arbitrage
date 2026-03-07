'use client';

import { useApi } from '@/hooks/useApi';
import { getHealth, getStatus } from '@/lib/api';
import type { HealthResponse, StatusResponse } from '@/types';

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  return `${m}m ${s}s`;
}

function StatusDot({ status }: { status: 'healthy' | 'degraded' | 'unhealthy' | 'unknown' }) {
  const colors = {
    healthy:   'bg-profit',
    degraded:  'bg-warn',
    unhealthy: 'bg-loss',
    unknown:   'bg-terminal-muted',
  };
  return (
    <span className={`inline-block w-2 h-2 rounded-full ${colors[status]} mr-1.5`} />
  );
}

const CONNECTIONS = [
  { label: 'Binance',   key: 'binance' },
  { label: 'Bybit',     key: 'bybit' },
  { label: 'OKX',       key: 'okx' },
  { label: 'Bitget',    key: 'bitget' },
  { label: 'Upbit',     key: 'upbit' },
  { label: 'Bithumb',   key: 'bithumb' },
  { label: 'TimescaleDB', key: 'db' },
  { label: 'Redis',     key: 'redis' },
];

export default function SystemPage() {
  const { data: health, error: healthError, isLoading: healthLoading, mutate: mutateHealth } = useApi<HealthResponse>(
    '/health',
    getHealth,
    { refreshInterval: 5000 },
  );
  const { data: status, error: statusError, isLoading: statusLoading, mutate: mutateStatus } = useApi<StatusResponse>(
    '/status',
    getStatus,
    { refreshInterval: 5000 },
  );

  const engineStatus = health?.status ?? 'unknown';
  const uptime = (status as (typeof status & { uptime_seconds?: number }) | undefined)?.uptime_seconds;
  const killActive = status?.kill_switch_active ?? false;
  const strategyCount = status?.strategy_count ?? 0;
  const environment = status?.environment ?? '—';

  const hasError = healthError || statusError;
  const isLoading = (healthLoading && !health) || (statusLoading && !status);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-mono font-semibold text-terminal-text">System Health</h2>
          <p className="text-xs font-mono text-terminal-subtle mt-1">Engine status, connectivity, and diagnostics</p>
        </div>
        {!isLoading && !hasError && (
          <span className="text-[9px] font-mono text-profit animate-pulse">● LIVE</span>
        )}
      </div>

      {/* Top stats */}
      <div className="grid grid-cols-1 xl:grid-cols-4 gap-4">
        <div className="card">
          <p className="card-header">Engine Status</p>
          <p className="stat-value">
            <StatusDot status={engineStatus as 'healthy' | 'degraded' | 'unhealthy' | 'unknown'} />
            <span className={
              engineStatus === 'healthy' ? 'text-profit' :
              engineStatus === 'degraded' ? 'text-warn' :
              'text-loss'
            }>
              {engineStatus.toUpperCase()}
            </span>
          </p>
        </div>

        <div className="card">
          <p className="card-header">Uptime</p>
          <p className="stat-value text-terminal-text">
            {uptime !== undefined ? formatUptime(uptime) : '—'}
          </p>
        </div>

        <div className="card">
          <p className="card-header">Kill Switch</p>
          <p className={`stat-value ${killActive ? 'text-loss' : 'text-profit'}`}>
            {killActive ? 'ACTIVE' : 'STANDBY'}
          </p>
        </div>

        <div className="card">
          <p className="card-header">Environment</p>
          <p className="stat-value text-terminal-text">
            {environment.toUpperCase()}
          </p>
        </div>
      </div>

      {/* Active strategies count */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {/* Exchange connections */}
        <div className="bg-terminal-surface border border-terminal-border p-4">
          <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle block mb-4">
            Exchange Connections
          </span>
          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="h-7 bg-terminal-muted/40 animate-pulse border border-terminal-border/30" />
              ))}
            </div>
          ) : (
            <div className="space-y-1.5">
              {CONNECTIONS.map(({ label }) => (
                <div
                  key={label}
                  className="flex items-center justify-between px-3 py-1.5 bg-terminal-bg border border-terminal-border/40"
                >
                  <span className="text-xs font-mono text-terminal-subtle uppercase tracking-wider">{label}</span>
                  <div className="flex items-center gap-1.5">
                    <span className={`w-1.5 h-1.5 rounded-full ${
                      hasError ? 'bg-terminal-muted' :
                      engineStatus === 'healthy' ? 'bg-profit animate-pulse' :
                      engineStatus === 'degraded' ? 'bg-warn' : 'bg-loss'
                    }`} />
                    <span className={`text-[10px] font-mono ${
                      hasError ? 'text-terminal-subtle' :
                      engineStatus === 'healthy' ? 'text-profit' :
                      engineStatus === 'degraded' ? 'text-warn' : 'text-loss'
                    }`}>
                      {hasError ? 'UNKNOWN' : engineStatus === 'healthy' ? 'CONNECTED' : 'DEGRADED'}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Engine stats */}
        <div className="bg-terminal-surface border border-terminal-border p-4">
          <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle block mb-4">
            Engine Stats
          </span>

          {hasError ? (
            <div className="flex flex-col items-center gap-2 py-8">
              <p className="text-xs font-mono text-loss">Failed to load system status</p>
              <button
                onClick={() => { mutateHealth(); mutateStatus(); }}
                className="text-[10px] font-mono border border-terminal-border px-3 py-1 text-terminal-subtle hover:text-terminal-text transition-colors"
              >
                Retry
              </button>
            </div>
          ) : (
            <div className="space-y-3">
              {[
                { label: 'Active Strategies', value: strategyCount > 0 ? String(strategyCount) : '—' },
                { label: 'Kill Switch State',  value: killActive ? 'HALTED' : 'READY' },
                { label: 'Environment',        value: environment.toUpperCase() },
                { label: 'API Health',         value: health?.status.toUpperCase() ?? '—' },
                { label: 'Uptime',             value: uptime !== undefined ? formatUptime(uptime) : '—' },
                { label: 'Engine Running',     value: status?.running ? 'YES' : status ? 'NO' : '—' },
              ].map(({ label, value }) => (
                <div
                  key={label}
                  className="flex items-center justify-between px-3 py-2 bg-terminal-bg border border-terminal-border/40"
                >
                  <span className="text-[11px] font-mono text-terminal-subtle">{label}</span>
                  <span className={`text-[11px] font-mono tabular-nums ${
                    value === 'HALTED' || value === 'NO' ? 'text-loss' :
                    value === 'READY' || value === 'YES' || value === 'HEALTHY' ? 'text-profit' :
                    'text-terminal-text'
                  }`}>
                    {isLoading && value === '—' ? (
                      <span className="inline-block w-8 h-3 bg-terminal-muted/40 animate-pulse" />
                    ) : value}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
