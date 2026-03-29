'use client';

import { useApi } from '@/hooks/useApi';
import { getShadowStats } from '@/lib/api';
import type { ShadowStats, ShadowStrategyBreakdown } from '@/types';

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmtUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return [h, m, s].map(n => String(n).padStart(2, '0')).join(':');
}

function fmtPnl(v: number): string {
  return `${v >= 0 ? '+' : ''}$${v.toFixed(2)}`;
}

// ─── KPI Card ─────────────────────────────────────────────────────────────────

function KpiCard({
  label,
  value,
  valueColor,
}: {
  label: string;
  value: string;
  valueColor?: string;
}) {
  return (
    <div className="bg-terminal-bg border border-terminal-border p-2">
      <div className="text-[10px] font-mono text-terminal-subtle uppercase tracking-wider mb-1">
        {label}
      </div>
      <div
        className="text-sm font-mono tabular-nums font-semibold"
        style={{ color: valueColor }}
      >
        {value}
      </div>
    </div>
  );
}

// ─── Strategy Breakdown Table ──────────────────────────────────────────────────

function BreakdownTable({ rows }: { rows: ShadowStrategyBreakdown[] }) {
  if (rows.length === 0) {
    return (
      <p className="text-[11px] font-mono text-terminal-subtle text-center py-4">
        No strategy data yet
      </p>
    );
  }

  return (
    <table className="w-full text-[11px] font-mono">
      <thead>
        <tr className="border-b border-terminal-border/40">
          {['Strategy', 'Trades', 'W / L', 'WR %', 'PnL'].map(h => (
            <th
              key={h}
              className="text-left text-[9px] uppercase tracking-wider text-terminal-subtle pb-1.5 pr-2 last:pr-0"
            >
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody className="divide-y divide-terminal-border/20">
        {rows.map(row => (
          <tr key={row.strategy_id} className="hover:bg-terminal-muted/10 transition-colors">
            <td className="text-terminal-text py-1.5 pr-2 truncate max-w-[120px]">
              {row.strategy_id}
            </td>
            <td className="text-terminal-text tabular-nums py-1.5 pr-2">
              {row.trades}
            </td>
            <td className="text-terminal-subtle tabular-nums py-1.5 pr-2">
              <span className="text-profit">{row.wins}</span>
              {' / '}
              <span className="text-loss">{row.losses}</span>
            </td>
            <td className="tabular-nums py-1.5 pr-2" style={{ color: row.win_rate >= 60 ? '#00ff88' : '#f59e0b' }}>
              {row.win_rate.toFixed(1)}%
            </td>
            <td
              className="tabular-nums py-1.5"
              style={{ color: row.pnl >= 0 ? '#00ff88' : '#ff4d4d' }}
            >
              {fmtPnl(row.pnl)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ─── Inactive State ───────────────────────────────────────────────────────────

function InactiveState() {
  return (
    <div className="flex flex-col items-center justify-center py-8 gap-2">
      <span className="text-[10px] font-mono text-terminal-subtle uppercase tracking-[0.2em]">
        ○ Paper mode inactive
      </span>
      <p className="text-[10px] font-mono text-terminal-subtle/60">
        Start the engine in Paper or Shadow mode to see live stats
      </p>
    </div>
  );
}

// ─── Shadow Panel ─────────────────────────────────────────────────────────────

const MODE_TITLES: Record<string, string> = {
  backtest: '백테스트 모니터',
  paper: '페이퍼 모니터',
  shadow: 'Canary 모니터',
  live: '실거래 모니터',
};

interface ShadowPanelProps {
  /** Pre-fetched shadow_stats from WebSocket — used when available */
  wsStats?: ShadowStats | null;
  mode?: string;
}

export function ShadowPanel({ wsStats, mode }: ShadowPanelProps = {}) {
  const panelTitle = MODE_TITLES[mode ?? 'shadow'] ?? '{panelTitle}';
  const { data: restData, error, isLoading } = useApi<ShadowStats>(
    '/shadow/stats',
    getShadowStats,
    { refreshInterval: 5_000 },
  );

  // WS data takes priority over REST poll
  const stats = wsStats ?? restData;

  if (isLoading && !stats) {
    return (
      <div className="bg-terminal-surface border border-terminal-border p-4">
        <div className="h-3 w-28 bg-terminal-muted animate-pulse mb-4" />
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-12 bg-terminal-muted/40 animate-pulse border border-terminal-border/30" />
          ))}
        </div>
      </div>
    );
  }

  if (error && !stats) {
    return (
      <div className="bg-terminal-surface border border-terminal-border p-4">
        <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle block mb-3">
          {panelTitle}
        </span>
        <InactiveState />
      </div>
    );
  }

  const isActive = stats?.active ?? false;

  return (
    <div className="bg-terminal-surface border border-terminal-border p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">
          {panelTitle}
        </span>
        <div className="flex items-center gap-2">
          {isActive && stats && (
            <span className="text-[10px] font-mono text-terminal-subtle tabular-nums">
              {fmtUptime(stats.uptime_seconds)}
            </span>
          )}
          <span
            className={`text-[10px] font-mono ${isActive ? 'text-profit animate-pulse' : 'text-terminal-subtle'}`}
          >
            {isActive ? '● RUNNING' : '○ INACTIVE'}
          </span>
        </div>
      </div>

      {!isActive || !stats ? (
        <InactiveState />
      ) : (
        <>
          {/* KPI cards */}
          <div className="grid grid-cols-3 gap-2 mb-4">
            <KpiCard
              label="Total PnL"
              value={fmtPnl(stats.total_pnl)}
              valueColor={stats.total_pnl >= 0 ? '#00ff88' : '#ff4d4d'}
            />
            <KpiCard
              label="Win Rate"
              value={`${stats.win_rate.toFixed(1)}%`}
              valueColor={stats.win_rate >= 60 ? '#00ff88' : '#f59e0b'}
            />
            <KpiCard
              label="Max Drawdown"
              value={fmtPnl(-Math.abs(stats.max_drawdown))}
              valueColor="#ff4d4d"
            />
          </div>

          {/* Secondary stats row */}
          <div className="grid grid-cols-4 gap-0 border border-terminal-border/40 mb-4">
            {(
              [
                { label: 'Signals',  value: String(stats.signals_detected) },
                { label: 'Executed', value: String(stats.trades_executed) },
                { label: 'Rejected', value: String(stats.trades_rejected) },
                { label: 'Peak PnL', value: fmtPnl(stats.peak_pnl) },
              ] as const
            ).map(({ label, value }) => (
              <div key={label} className="text-center py-2 px-1 border-r border-terminal-border/40 last:border-r-0">
                <div className="text-[9px] font-mono text-terminal-subtle uppercase tracking-wider">
                  {label}
                </div>
                <div className="text-[11px] font-mono tabular-nums text-terminal-text mt-0.5">
                  {value}
                </div>
              </div>
            ))}
          </div>

          {/* Strategy breakdown */}
          <div>
            <div className="text-[9px] font-mono text-terminal-subtle uppercase tracking-[0.15em] mb-2">
              By Strategy
            </div>
            <BreakdownTable rows={stats.by_strategy} />
          </div>
        </>
      )}
    </div>
  );
}
