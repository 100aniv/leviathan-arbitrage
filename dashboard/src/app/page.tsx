'use client';

import { PnLChart }          from '@/components/PnLChart';
import { GlobalHeatmap }     from '@/components/GlobalHeatmap';
import { KillSwitch }        from '@/components/KillSwitch';
import { StrategyPanel }     from '@/components/StrategyPanel';
import { OrderbookView }     from '@/components/OrderbookView';
import { ShadowPanel }       from '@/components/ShadowPanel';
import { PortfolioSummary }  from '@/components/PortfolioSummary';
import { RiskGauge }         from '@/components/RiskGauge';
import { PerformanceTrend }  from '@/components/PerformanceTrend';
import { EventFeed }         from '@/components/EventFeed';
import { useEngineWs }       from '@/hooks/useEngineWs';

export default function OverviewPage() {
  const { connected, data } = useEngineWs();

  return (
    <div className="space-y-4">
      {/* Header bar: title + connection status + Kill Switch top-right */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-mono font-semibold text-terminal-text">War Room</h2>
            <span
              className={`text-[9px] font-mono ${connected ? 'text-profit' : 'text-loss'}`}
              title={connected ? 'Engine connected' : 'Engine disconnected'}
            >
              {connected ? '● LIVE' : '● OFFLINE'}
            </span>
          </div>
          <p className="text-xs font-mono text-terminal-subtle mt-0.5">Real-time arbitrage engine status</p>
        </div>
        <KillSwitch />
      </div>

      {/* Portfolio summary: status badge + 4 KPI + exchange status bar */}
      <PortfolioSummary />

      {/* Row 1: Heatmap + PnL chart */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <GlobalHeatmap />
        <PnLChart wsPnl={data?.pnl ?? null} />
      </div>

      {/* Row 2: Risk gauge + Session performance trend */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <RiskGauge />
        <PerformanceTrend />
      </div>

      {/* Row 3: Strategies + Orderbook */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <StrategyPanel />
        <OrderbookView />
      </div>

      {/* Event feed — full width */}
      <EventFeed />

      {/* Shadow Monitor — full width, visible only when active */}
      <ShadowPanel wsStats={data?.shadow_stats ?? null} />
    </div>
  );
}
