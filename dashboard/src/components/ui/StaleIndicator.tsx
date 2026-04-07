'use client';

import { useEffect, useState, type ReactNode } from 'react';

// ─── Types ────────────────────────────────────────────────────────────────────

interface StaleIndicatorProps {
  /** Timestamp of last data update (ms epoch or ISO string) */
  lastUpdated: number | string | null | undefined;
  /** Polling interval in ms — data older than 2× this is "stale" */
  pollingInterval?: number;
  children: ReactNode;
}

// ─── Component ────────────────────────────────────────────────────────────────

export function StaleIndicator({
  lastUpdated,
  pollingInterval = 5_000,
  children,
}: StaleIndicatorProps) {
  const [ageMs, setAgeMs] = useState(0);

  useEffect(() => {
    if (!lastUpdated) return;

    const ts =
      typeof lastUpdated === 'string'
        ? new Date(lastUpdated).getTime()
        : lastUpdated;

    const tick = () => setAgeMs(Date.now() - ts);
    tick();
    const id = setInterval(tick, 1_000);
    return () => clearInterval(id);
  }, [lastUpdated]);

  const staleThreshold = pollingInterval * 2;
  const isStale = lastUpdated !== null && lastUpdated !== undefined && ageMs > staleThreshold;
  const ageSec = Math.round(ageMs / 1_000);

  return (
    <div className={`relative transition-opacity duration-400 ${isStale ? 'stale' : ''}`}>
      {children}
      {isStale && (
        <div className="absolute top-2 right-2 z-10">
          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-terminal-bg border border-terminal-border/60 text-[9px] font-mono text-terminal-subtle uppercase tracking-wider">
            stale {ageSec}s
          </span>
        </div>
      )}
    </div>
  );
}
