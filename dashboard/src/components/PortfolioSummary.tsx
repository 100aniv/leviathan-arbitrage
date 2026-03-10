'use client';

import { useEngineWs } from '@/hooks/useEngineWs';
import { useApi } from '@/hooks/useApi';
import { getExchangeStatus, getPnl } from '@/lib/api';
import type { ExchangeStatus, PnlResponse } from '@/types';

function StatusBadge({ running, killSwitch }: { running: boolean; killSwitch: boolean }) {
  if (killSwitch) return <span className="badge-loss">● KILL ACTIVE</span>;
  if (running)    return <span className="badge-profit">● RUNNING</span>;
  return <span className="badge-warn">● STOPPED</span>;
}

function ExchangeStatusBar({ exchanges }: { exchanges: Record<string, ExchangeStatus> | undefined }) {
  const list = Object.entries(exchanges ?? {});
  if (list.length === 0) return null;

  return (
    <div className="flex gap-2 overflow-x-auto pb-1 mt-3 scrollbar-thin">
      {list.map(([id, ex]) => (
        <div
          key={id}
          className="flex items-center gap-1.5 shrink-0 px-2.5 py-1 bg-terminal-bg border border-terminal-border/50 rounded-full"
          title={`${id}: ${ex.connected ? 'connected' : 'disconnected'} · ${ex.latency_ms}ms · ${ex.symbols_count} symbols`}
        >
          <span className={`w-1.5 h-1.5 rounded-full ${ex.connected ? 'bg-profit animate-pulse' : 'bg-loss'}`} />
          <span className="text-[10px] font-mono text-terminal-subtle uppercase tracking-wide">{id}</span>
          {ex.latency_ms > 0 && (
            <span className="text-[10px] font-mono text-terminal-text tabular-nums">{ex.latency_ms}ms</span>
          )}
          <span className="text-[10px] font-mono text-terminal-subtle">{ex.symbols_count}s</span>
        </div>
      ))}
    </div>
  );
}

export function PortfolioSummary() {
  const { data } = useEngineWs();
  const { data: exchanges } = useApi<Record<string, ExchangeStatus>>(
    '/exchanges',
    getExchangeStatus,
    { refreshInterval: 10000 },
  );
  const { data: pnlRest } = useApi<PnlResponse>(
    '/pnl/total',
    getPnl,
    { refreshInterval: 5000 },
  );

  const running     = data?.running ?? false;
  const killSwitch  = data?.kill_switch ?? false;
  const todayPnl    = data?.pnl?.total ?? null;
  const totalPnl    = pnlRest?.total_pnl ?? null;
  const positions   = data?.position_count ?? null;

  // Sum USDT balance across exchanges
  const totalBalance = exchanges
    ? Object.values(exchanges).reduce((sum, ex) => {
        const usdt = ex.balance?.USDT ?? ex.balance?.usdt ?? 0;
        return sum + usdt;
      }, 0)
    : null;

  function fmtPnl(v: number | null): string {
    if (v === null) return '—';
    return `${v >= 0 ? '+' : ''}$${Math.abs(v).toFixed(2)}`;
  }

  function fmtBalance(v: number | null): string {
    if (v === null) return '—';
    return `$${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }

  const kpis = [
    { label: 'Total Balance',    value: fmtBalance(totalBalance), color: undefined as string | undefined },
    { label: 'Today PnL',        value: fmtPnl(todayPnl),        color: todayPnl  === null ? undefined : todayPnl  >= 0 ? '#00ff88' : '#ff4d4d' },
    { label: 'Total PnL',        value: fmtPnl(totalPnl),        color: totalPnl  === null ? undefined : totalPnl  >= 0 ? '#00ff88' : '#ff4d4d' },
    { label: 'Active Positions', value: positions === null ? '—' : String(positions), color: undefined },
  ];

  return (
    <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
      {/* Header: label + status badge */}
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">Portfolio</span>
        <StatusBadge running={running} killSwitch={killSwitch} />
      </div>

      {/* 4 KPI cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {kpis.map(({ label, value, color }) => (
          <div key={label} className="bg-terminal-bg border border-terminal-border/50 rounded-lg p-3">
            <p className="text-[10px] font-mono text-terminal-subtle uppercase tracking-wider mb-1">{label}</p>
            <p
              className="text-2xl font-mono tabular-nums font-semibold leading-tight"
              style={color ? { color } : undefined}
            >
              {value}
            </p>
          </div>
        ))}
      </div>

      {/* Inline exchange status bar */}
      <ExchangeStatusBar exchanges={exchanges} />
    </div>
  );
}
