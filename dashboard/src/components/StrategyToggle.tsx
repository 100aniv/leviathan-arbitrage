'use client';

import { useState } from 'react';
import { toggleStrategy } from '@/lib/api';
import type { Strategy } from '@/types';
import { StatusBadge } from './ui/StatusBadge';

type StrategyStatus = 'running' | 'paused' | 'error';

interface StrategyToggleProps {
  strategy: Strategy;
  onChange?: (id: string, enabled: boolean) => void;
}

export function StrategyToggle({ strategy, onChange }: StrategyToggleProps) {
  const [enabled, setEnabled] = useState(strategy.enabled);
  const [isPending, setIsPending] = useState(false);
  const [hasError, setHasError] = useState(false);

  const status: StrategyStatus = hasError ? 'error' : enabled ? 'running' : 'paused';
  const badgeStatus = status === 'running' ? 'active' : status === 'error' ? 'error' : 'paused';

  const handleToggle = async () => {
    if (isPending || hasError) return;

    // Optimistic update
    const next = !enabled;
    setEnabled(next);
    setIsPending(true);

    try {
      const res = await toggleStrategy(strategy.id);
      setEnabled(res.enabled);
      onChange?.(strategy.id, res.enabled);
    } catch (err) {
      // Rollback on failure
      setEnabled(enabled);
      setHasError(true);
      console.error(`[StrategyToggle] Toggle failed for ${strategy.id}:`, err);
      // Clear error after 3s to allow retry
      setTimeout(() => setHasError(false), 3000);
    } finally {
      setIsPending(false);
    }
  };

  return (
    <div className="flex items-center justify-between gap-4 border border-terminal-border bg-terminal-surface px-3 py-2.5 font-mono hover:border-terminal-muted transition-colors group">
      {/* Left: name + status dot */}
      <div className="flex items-center gap-2.5 min-w-0">
        <StatusBadge status={badgeStatus} size="sm" showLabel={false} />
        <div className="min-w-0">
          <span className="block text-xs text-terminal-text truncate tracking-wide">
            {strategy.name}
          </span>
          {(strategy.exchange_a || strategy.symbol) && (
            <span className="text-[10px] text-terminal-subtle truncate">
              {[strategy.exchange_a, strategy.exchange_b, strategy.symbol]
                .filter(Boolean)
                .join(' · ')}
            </span>
          )}
        </div>
      </div>

      {/* Right: badge + toggle */}
      <div className="flex items-center gap-3 flex-shrink-0">
        <StatusBadge status={badgeStatus} size="sm" />

        {/* Toggle switch */}
        <button
          role="switch"
          aria-checked={enabled}
          aria-label={`${enabled ? 'Pause' : 'Start'} ${strategy.name}`}
          onClick={handleToggle}
          disabled={isPending || hasError}
          className={[
            'relative w-10 h-5 flex-shrink-0 transition-opacity',
            isPending ? 'opacity-60 cursor-wait' : hasError ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer',
          ].join(' ')}
        >
          {/* Track */}
          <span
            className={[
              'block w-full h-full border transition-colors duration-200',
              enabled
                ? 'bg-profit/20 border-profit/50'
                : 'bg-terminal-muted/30 border-terminal-border',
            ].join(' ')}
          />
          {/* Thumb */}
          <span
            className={[
              'absolute top-0.5 h-4 w-4 transition-all duration-200',
              enabled ? 'translate-x-5 bg-profit' : 'translate-x-0.5 bg-terminal-subtle',
              isPending ? 'animate-pulse' : '',
            ].join(' ')}
          />
        </button>
      </div>
    </div>
  );
}
