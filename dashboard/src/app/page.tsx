'use client';

import { useState, useEffect } from 'react';
import { PnLChart }         from '@/components/PnLChart';
import { GlobalHeatmap }    from '@/components/GlobalHeatmap';
import { StrategyPanel }    from '@/components/StrategyPanel';
import { OrderbookView }    from '@/components/OrderbookView';
import { ShadowPanel }      from '@/components/ShadowPanel';
import { RiskGauge }        from '@/components/RiskGauge';
import { PerformanceTrend } from '@/components/PerformanceTrend';
import { EventFeed }        from '@/components/EventFeed';
import { ModeSwitch }       from '@/components/ModeSwitch';
import { useEngineWs }      from '@/hooks/useEngineWs';
import { getAttribution }   from '@/lib/api';
import type { AttributionResponse } from '@/types';

export default function OverviewPage() {
  const { connected, data } = useEngineWs();
  const [attribution, setAttribution] = useState<AttributionResponse | null>(null);

  useEffect(() => {
    const load = async () => {
      try { setAttribution(await getAttribution()); } catch {}
    };
    load();
    const i = setInterval(load, 15_000);
    return () => clearInterval(i);
  }, []);

  return (
    <div className="space-y-4">
      {/* Minimal page header — MissionControlStrip handles top-level status */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-mono font-semibold text-terminal-text">대시보드</h2>
          <span className={`text-[9px] font-mono ${connected ? 'text-profit' : 'text-loss'}`}>
            {connected ? '● LIVE' : '● OFFLINE'}
          </span>
        </div>
        <ModeSwitch currentMode={data?.mode ?? 'shadow'} />
      </div>

      {/* Row 1: Strategy attribution + Risk gauge — immediate decision context */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {/* Attribution by strategy */}
        <div className="bg-terminal-surface border border-terminal-border p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-terminal-subtle">
              전략별 기여도
            </span>
            {attribution && (
              <span className={`text-[10px] font-mono tabular-nums ${
                attribution.total_pnl >= 0 ? 'text-profit' : 'text-loss'
              }`}>
                {attribution.total_pnl >= 0 ? '+' : ''}${attribution.total_pnl.toFixed(2)} total
              </span>
            )}
          </div>

          {attribution && attribution.by_strategy.length > 0 ? (
            <div className="space-y-1.5">
              {attribution.by_strategy.slice(0, 5).map(s => (
                <div
                  key={s.key}
                  className="flex items-center gap-2 px-2 py-1.5 bg-terminal-bg border border-terminal-border/40"
                >
                  <span className="flex-1 min-w-0 text-[10px] font-mono text-terminal-subtle uppercase truncate">
                    {s.key}
                  </span>
                  <span className="text-[10px] font-mono text-terminal-subtle tabular-nums">
                    {s.trades}t
                  </span>
                  <span className="text-[10px] font-mono text-terminal-subtle tabular-nums">
                    {(s.wr * 100).toFixed(0)}%
                  </span>
                  <span className={`text-[10px] font-mono tabular-nums w-16 text-right ${
                    s.pnl >= 0 ? 'text-profit' : 'text-loss'
                  }`}>
                    {s.pnl >= 0 ? '+' : ''}${s.pnl.toFixed(2)}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="space-y-1.5">
              {Array.from({ length: 4 }).map((_, i) => (
                <div
                  key={i}
                  className="h-8 bg-terminal-muted/20 animate-pulse border border-terminal-border/30"
                />
              ))}
            </div>
          )}
        </div>

        <RiskGauge />
      </div>

      {/* Row 2: PnL curve — primary performance view */}
      <PnLChart wsPnl={data?.pnl ?? null} />

      {/* Row 3: Performance trend + Event feed */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <PerformanceTrend />
        <EventFeed />
      </div>

      {/* Row 4: Heatmap + Strategies */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <GlobalHeatmap />
        <StrategyPanel />
      </div>

      {/* Row 5: Orderbook */}
      <OrderbookView />

      {/* Shadow monitor — visible only when active */}
      <ShadowPanel wsStats={data?.shadow_stats ?? null} mode={data?.mode ?? 'shadow'} />
    </div>
  );
}
