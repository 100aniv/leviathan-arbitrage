'use client';

import { useApi } from '@/hooks/useApi';
import { getHealth, getStatus, getExchangeStatus, getSystemContainers, getSystemResources } from '@/lib/api';
import type { HealthResponse, StatusResponse, ExchangeStatus, ContainerStatus, SystemResources } from '@/types';
import { TCAWidget } from '@/components/TCAWidget';

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
  return <span className={`inline-block w-2 h-2 rounded-full ${colors[status]} mr-1.5`} />;
}

function ContainerBadge({ status }: { status: ContainerStatus['status'] }) {
  if (status === 'running') return <span className="badge-profit">running</span>;
  if (status === 'stopped') return <span className="badge-warn">stopped</span>;
  return <span className="badge-loss">error</span>;
}

export default function SystemPage() {
  const {
    data: health,
    error: healthError,
    isLoading: healthLoading,
    mutate: mutateHealth,
  } = useApi<HealthResponse>('/health', getHealth, { refreshInterval: 5000 });

  const {
    data: status,
    error: statusError,
    isLoading: statusLoading,
    mutate: mutateStatus,
  } = useApi<StatusResponse>('/status', getStatus, { refreshInterval: 5000 });

  const { data: exchanges, isLoading: exchangesLoading } = useApi<Record<string, ExchangeStatus>>(
    '/exchanges',
    getExchangeStatus,
    { refreshInterval: 5000 },
  );

  const engineStatus  = health?.status ?? 'unknown';
  const uptime        = (status as (typeof status & { uptime_seconds?: number }) | undefined)?.uptime_seconds;
  const killActive    = status?.kill_switch_active ?? false;
  const strategyCount = status?.strategy_count ?? 0;
  const environment   = status?.environment ?? '—';

  const hasError = healthError || statusError;
  const isLoading = (healthLoading && !health) || (statusLoading && !status);

  const exchangeList = Object.entries(exchanges ?? {});

  const { data: containers, isLoading: containersLoading } = useApi<ContainerStatus[]>(
    '/api/v1/system/containers',
    getSystemContainers,
    { refreshInterval: 5000 },
  );

  const { data: resources, isLoading: resourcesLoading } = useApi<SystemResources>(
    '/api/v1/system/resources',
    getSystemResources,
    { refreshInterval: 5000 },
  );

  return (
    <div className="space-y-6">
      {/* Header */}
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
              engineStatus === 'degraded' ? 'text-warn' : 'text-loss'
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
          <p className="stat-value text-terminal-text">{environment.toUpperCase()}</p>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {/* Exchange connections — real API data */}
        <div className="bg-terminal-surface border border-terminal-border p-4">
          <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle block mb-4">
            Exchange Connections
          </span>
          {(isLoading || exchangesLoading) && exchangeList.length === 0 ? (
            <div className="space-y-2">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="h-7 bg-terminal-muted/40 animate-pulse border border-terminal-border/30" />
              ))}
            </div>
          ) : exchangeList.length > 0 ? (
            <div className="space-y-1.5">
              {exchangeList.map(([id, ex]) => (
                <div
                  key={id}
                  className="flex items-center justify-between px-3 py-1.5 bg-terminal-bg border border-terminal-border/40"
                >
                  <span className="text-xs font-mono text-terminal-subtle uppercase tracking-wider">{id}</span>
                  <div className="flex items-center gap-3">
                    <span className="text-[10px] font-mono text-terminal-subtle tabular-nums">
                      {ex.latency_ms}ms · {ex.symbols_count}s
                    </span>
                    <div className="flex items-center gap-1.5">
                      <span className={`w-1.5 h-1.5 rounded-full ${ex.connected ? 'bg-profit animate-pulse' : 'bg-loss'}`} />
                      <span className={`text-[10px] font-mono ${ex.connected ? 'text-profit' : 'text-loss'}`}>
                        {ex.connected ? 'CONNECTED' : 'OFFLINE'}
                      </span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            /* Fallback: show status-based list */
            <div className="space-y-1.5">
              {['Binance', 'Bybit', 'OKX', 'Bitget', 'Upbit', 'Bithumb', 'Coinone', 'TimescaleDB'].map(label => (
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

      {/* Docker containers — live API */}
      <div className="bg-terminal-surface border border-terminal-border p-4">
        <div className="flex items-center justify-between mb-4">
          <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">
            Docker Containers
          </span>
          {containers && containers.length > 0 && (
            <span className="text-[10px] font-mono text-terminal-subtle">
              {containers.filter(c => c.status === 'running').length} / {containers.length} running
            </span>
          )}
        </div>
        {containersLoading && !containers ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-16 bg-terminal-muted/40 animate-pulse border border-terminal-border/30" />
            ))}
          </div>
        ) : containers && containers.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-2">
            {containers.map(container => (
              <div
                key={container.name}
                className="px-3 py-2 bg-terminal-bg border border-terminal-border/40"
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[10px] font-mono text-terminal-text truncate pr-2">{container.name}</span>
                  <ContainerBadge status={container.status} />
                </div>
                <div className="flex items-center gap-3 text-[10px] font-mono text-terminal-subtle tabular-nums">
                  <span>CPU {container.cpu_pct ?? '—'}%</span>
                  <span>{container.memory_mb ?? '—'}MB</span>
                  <span>{container.uptime ?? '—'}</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="flex items-center justify-center py-8">
            <span className="text-xs font-mono text-terminal-subtle">Docker 연결 대기 중...</span>
          </div>
        )}
      </div>

      {/* Resource Usage — live API */}
      <div className="bg-terminal-surface border border-terminal-border p-4">
        <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle block mb-4">
          Resource Usage
        </span>
        {resourcesLoading && !resources ? (
          <div className="space-y-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="h-8 bg-terminal-muted/40 animate-pulse border border-terminal-border/30" />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {[
              { label: 'CPU Usage', value: resources?.cpu_pct != null ? `${resources.cpu_pct.toFixed(1)}%` : '—', bar: resources?.cpu_pct ?? 0, color: '#00ff88' },
              { label: 'Memory',    value: resources?.memory_used_gb != null && resources?.memory_total_gb != null ? `${resources.memory_used_gb.toFixed(1)} / ${resources.memory_total_gb.toFixed(0)} GB` : '—', bar: resources?.memory_used_gb != null && resources?.memory_total_gb ? Math.min(resources.memory_used_gb / resources.memory_total_gb * 100, 100) : 0, color: '#3b82f6' },
              { label: 'Disk',      value: resources?.disk_used_gb != null && resources?.disk_total_gb != null ? `${resources.disk_used_gb.toFixed(0)} / ${resources.disk_total_gb.toFixed(0)} GB` : '—', bar: resources?.disk_used_gb != null && resources?.disk_total_gb ? Math.min(resources.disk_used_gb / resources.disk_total_gb * 100, 100) : 0, color: '#f59e0b' },
            ].map(({ label, value, bar, color }) => (
              <div key={label} className="space-y-2">
                <div className="flex justify-between text-[11px] font-mono">
                  <span className="text-terminal-subtle">{label}</span>
                  <span className="text-terminal-text tabular-nums">{value}</span>
                </div>
                <div className="h-1.5 bg-terminal-muted rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{ width: `${bar}%`, backgroundColor: color }}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* TCA — Execution Quality (US-116) */}
      <TCAWidget />
    </div>
  );
}
