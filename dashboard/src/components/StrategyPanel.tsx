'use client';

import { useState } from 'react';
import { useApi } from '@/hooks/useApi';
import { getStrategies, toggleStrategy } from '@/lib/api';
import { StatusBadge } from '@/components/ui/StatusBadge';
import type { Strategy } from '@/types';

interface StrategyStats {
  winRate: number;
  tradeCount: number;
  pnl: number;
  avgDuration: string;
}

// Deterministic-ish mock stats seeded by strategy id
function mockStats(id: string): StrategyStats {
  const seed = id.split('').reduce((a, c) => a + c.charCodeAt(0), 0);
  const rng  = (n: number) => ((seed * 9301 + 49297 * n) % 233280) / 233280;
  return {
    winRate:     parseFloat((rng(1) * 35 + 58).toFixed(1)),
    tradeCount:  Math.floor(rng(2) * 300 + 10),
    pnl:         parseFloat(((rng(3) - 0.28) * 3000).toFixed(2)),
    avgDuration: `${Math.floor(rng(4) * 6)}m ${Math.floor(rng(5) * 59)}s`,
  };
}

const MOCK_STRATEGIES: Strategy[] = [
  { id: 'tri-arb',  name: 'Triangle Arbitrage',   enabled: true,  exchange_a: 'Binance', exchange_b: 'OKX',     symbol: 'BTC/USDT' },
  { id: 'kim-arb',  name: 'Kimchi Premium',        enabled: true,  exchange_a: 'Upbit',   exchange_b: 'Binance', symbol: 'ETH/KRW'  },
  { id: 'stat-arb', name: 'Statistical Arbitrage', enabled: false, exchange_a: 'Bybit',   exchange_b: 'OKX',     symbol: 'SOL/USDT' },
  { id: 'mm-eth',   name: 'ETH Market Making',     enabled: true,  exchange_a: 'Binance', symbol: 'ETH/USDT' },
];

// ─── Strategy Card ────────────────────────────────────────────────────────────

function StrategyCard({
  strategy,
  onToggle,
}: {
  strategy: Strategy;
  onToggle: (id: string) => Promise<void>;
}) {
  const [expanded, setExpanded] = useState(false);
  const [toggling, setToggling] = useState(false);
  const stats  = mockStats(strategy.id);
  const status = strategy.enabled ? 'active' : 'stopped';

  const handleToggle = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setToggling(true);
    try { await onToggle(strategy.id); } finally { setToggling(false); }
  };

  return (
    <div
      className={`border transition-colors ${
        strategy.enabled ? 'border-terminal-border' : 'border-terminal-border/40'
      } bg-terminal-bg`}
    >
      {/* Header row */}
      <div
        className="flex items-center justify-between p-3 cursor-pointer hover:bg-terminal-muted/20 transition-colors"
        onClick={() => setExpanded(e => !e)}
        role="button"
        aria-expanded={expanded}
      >
        <div className="flex items-center gap-3 min-w-0">
          <div
            className={`w-0.5 h-8 flex-shrink-0 transition-colors ${
              strategy.enabled ? 'bg-profit' : 'bg-terminal-muted'
            }`}
          />
          <div className="min-w-0">
            <div className="text-xs font-mono text-terminal-text truncate">{strategy.name}</div>
            <div className="text-[10px] font-mono text-terminal-subtle mt-0.5">
              {strategy.exchange_a}
              {strategy.exchange_b ? ` ↔ ${strategy.exchange_b}` : ''}
              {strategy.symbol ? ` · ${strategy.symbol}` : ''}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0 ml-2">
          <StatusBadge status={status} size="sm" />
          <button
            onClick={handleToggle}
            disabled={toggling}
            aria-label={strategy.enabled ? 'Pause strategy' : 'Start strategy'}
            className={`text-[10px] font-mono uppercase tracking-widest px-2 py-1 border transition-colors disabled:opacity-50 ${
              strategy.enabled
                ? 'border-loss/40 text-loss/80 hover:bg-loss/10 hover:border-loss'
                : 'border-profit/40 text-profit/80 hover:bg-profit/10 hover:border-profit'
            }`}
          >
            {toggling ? '···' : strategy.enabled ? 'Pause' : 'Start'}
          </button>
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-4 border-t border-terminal-border/40 px-3 py-2">
        {(
          [
            { label: 'Win Rate', value: `${stats.winRate}%`,                     pnlKey: false },
            { label: 'Trades',   value: String(stats.tradeCount),                pnlKey: false },
            { label: 'PnL',      value: `${stats.pnl >= 0 ? '+' : ''}$${stats.pnl.toFixed(2)}`, pnlKey: true  },
            { label: 'Avg Dur',  value: stats.avgDuration,                       pnlKey: false },
          ] as const
        ).map(({ label, value, pnlKey }) => (
          <div key={label} className="text-center">
            <div className="text-[9px] font-mono text-terminal-subtle uppercase tracking-wider">
              {label}
            </div>
            <div
              className={`text-[11px] font-mono tabular-nums mt-0.5 ${
                pnlKey
                  ? stats.pnl >= 0 ? 'text-profit' : 'text-loss'
                  : 'text-terminal-text'
              }`}
            >
              {value}
            </div>
          </div>
        ))}
      </div>

      {/* Expanded details */}
      {expanded && (
        <div className="border-t border-terminal-border/40 p-3 bg-terminal-surface/50">
          <div className="grid grid-cols-2 gap-y-1 text-[11px] font-mono">
            <div>
              <span className="text-terminal-subtle">ID: </span>
              <span className="text-terminal-text">{strategy.id}</span>
            </div>
            {strategy.exchange_a && (
              <div>
                <span className="text-terminal-subtle">Exchange A: </span>
                <span className="text-terminal-text">{strategy.exchange_a}</span>
              </div>
            )}
            {strategy.exchange_b && (
              <div>
                <span className="text-terminal-subtle">Exchange B: </span>
                <span className="text-terminal-text">{strategy.exchange_b}</span>
              </div>
            )}
            {strategy.symbol && (
              <div>
                <span className="text-terminal-subtle">Symbol: </span>
                <span className="text-terminal-text">{strategy.symbol}</span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Loading Skeleton ────────────────────────────────────────────────────────

function Skeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="h-20 bg-terminal-muted/40 animate-pulse border border-terminal-border/30" />
      ))}
    </div>
  );
}

// ─── Strategy Panel ──────────────────────────────────────────────────────────

export function StrategyPanel() {
  const { data, error, isLoading, mutate } = useApi<Strategy[]>(
    '/strategies',
    getStrategies,
    { refreshInterval: 10_000 },
  );

  const strategies = data && data.length > 0 ? data : MOCK_STRATEGIES;
  const activeCount = strategies.filter(s => s.enabled).length;

  const handleToggle = async (id: string) => {
    await toggleStrategy(id);
    await mutate();
  };

  if (isLoading && !data) {
    return (
      <div className="bg-terminal-surface border border-terminal-border p-4">
        <div className="h-3 w-24 bg-terminal-muted animate-pulse mb-4" />
        <Skeleton />
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-terminal-surface border border-terminal-border p-4">
        <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle block mb-4">
          Strategies
        </span>
        <div className="flex flex-col items-center gap-2 py-8">
          <p className="text-xs font-mono text-loss">Failed to load strategies</p>
          <button
            onClick={() => mutate()}
            className="text-[10px] font-mono border border-terminal-border px-3 py-1 text-terminal-subtle hover:text-terminal-text transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-terminal-surface border border-terminal-border p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">
          Strategies
        </span>
        <span className="text-[10px] font-mono text-terminal-subtle">
          <span className="text-profit">{activeCount}</span>/{strategies.length} active
        </span>
      </div>

      <div className="space-y-1.5">
        {strategies.map(strategy => (
          <StrategyCard
            key={strategy.id}
            strategy={strategy}
            onToggle={handleToggle}
          />
        ))}
      </div>
    </div>
  );
}
