'use client';

import { useEngineWs } from '@/hooks/useEngineWs';
import { useApi } from '@/hooks/useApi';
import { getPortfolioSummary, getShadowStats, getStrategies } from '@/lib/api';
import type { PortfolioSummaryResponse, ShadowStats, Strategy } from '@/types';

export function MissionControlStrip() {
  const { connected, data } = useEngineWs();
  const { data: portfolio } = useApi<PortfolioSummaryResponse>(
    '/portfolio-summary',
    getPortfolioSummary,
    { refreshInterval: 10000 },
  );
  const { data: shadowStats } = useApi<ShadowStats>(
    '/shadow-stats',
    getShadowStats,
    { refreshInterval: 10000 },
  );
  const { data: strategies } = useApi<Strategy[]>(
    '/strategies',
    getStrategies,
    { refreshInterval: 30000 },
  );

  const equity    = portfolio?.total_balance_usdt ?? 0;
  const todayPnl  = data?.pnl?.total ?? shadowStats?.total_pnl ?? portfolio?.total_pnl ?? 0;
  const killActive = data?.kill_switch ?? false;
  const mode       = (data?.mode ?? portfolio?.mode ?? '—').toUpperCase();
  const activeCount = data?.strategies?.filter((s: { enabled: boolean }) => s.enabled).length
    ?? strategies?.filter(s => s.enabled).length ?? 0;
  const winRate     = data?.shadow_stats?.win_rate ?? shadowStats?.win_rate;
  const pnlPos      = todayPnl >= 0;

  return (
    <div
      role="status"
      aria-live="polite"
      className="h-10 bg-terminal-surface border-b border-terminal-border flex items-center shrink-0 overflow-hidden select-none"
    >
      {/* WS connection */}
      <div className="flex items-center gap-1.5 px-3 border-r border-terminal-border/50 h-full">
        <span
          aria-label={connected ? 'Engine connected' : 'Engine disconnected'}
          className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${connected ? 'bg-profit animate-pulse' : 'bg-loss'}`}
        />
        <span className={`text-[9px] font-mono uppercase tracking-wider hidden sm:block ${connected ? 'text-profit' : 'text-loss'}`}>
          {connected ? 'LIVE' : 'DISC'}
        </span>
      </div>

      {/* EQUITY */}
      <div className="flex items-center gap-1.5 px-3 border-r border-terminal-border/50 h-full">
        <span className="text-[9px] font-mono text-terminal-subtle hidden sm:block">EQ</span>
        <span className="text-[10px] font-mono text-terminal-text tabular-nums">
          {equity > 0
            ? `$${equity.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
            : '—'}
        </span>
      </div>

      {/* TODAY PnL */}
      <div className="flex items-center gap-1.5 px-3 border-r border-terminal-border/50 h-full">
        <span className="text-[9px] font-mono text-terminal-subtle hidden sm:block">PNL</span>
        <span className={`text-[10px] font-mono tabular-nums ${pnlPos ? 'text-profit' : 'text-loss'}`}>
          {pnlPos ? '+' : ''}{todayPnl.toFixed(2)}
        </span>
      </div>

      {/* WIN% */}
      <div className="flex items-center gap-1.5 px-3 border-r border-terminal-border/50 h-full hidden md:flex">
        <span className="text-[9px] font-mono text-terminal-subtle">WIN</span>
        <span className="text-[10px] font-mono text-terminal-text tabular-nums">
          {winRate != null ? `${(winRate * 100).toFixed(0)}%` : '—'}
        </span>
      </div>

      {/* ACTIVE strategies */}
      <div className="flex items-center gap-1.5 px-3 border-r border-terminal-border/50 h-full hidden md:flex">
        <span className="text-[9px] font-mono text-terminal-subtle">STRAT</span>
        <span className="text-[10px] font-mono text-terminal-text tabular-nums">
          {activeCount}/7
        </span>
      </div>

      {/* MODE */}
      <div className="flex items-center px-3 border-r border-terminal-border/50 h-full">
        <span className={`text-[10px] font-mono ${
          mode === 'LIVE'   ? 'text-profit' :
          mode === 'SHADOW' ? 'text-accent'  :
          mode === 'PAPER'  ? 'text-warn'    : 'text-terminal-subtle'
        }`}>
          {mode}
        </span>
      </div>

      {/* KILL SWITCH — right-aligned */}
      <div className="flex items-center px-3 h-full ml-auto">
        {killActive ? (
          <span
            role="alert"
            className="flex items-center gap-1.5 px-2 py-0.5 bg-loss/15 border border-loss/50 text-loss text-[9px] font-mono uppercase tracking-wider"
          >
            <span className="w-1.5 h-1.5 rounded-full bg-loss animate-pulse flex-shrink-0" />
            <span className="hidden sm:block">KILL ACTIVE</span>
            <span className="sm:hidden font-bold">!</span>
          </span>
        ) : (
          <span className="flex items-center gap-1.5 text-terminal-subtle text-[9px] font-mono uppercase">
            <span className="w-1.5 h-1.5 rounded-full bg-terminal-muted/60 flex-shrink-0" />
            <span className="hidden sm:block">STANDBY</span>
          </span>
        )}
      </div>
    </div>
  );
}
