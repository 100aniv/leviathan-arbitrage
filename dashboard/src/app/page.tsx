'use client';

import { PnLChart } from '@/components/PnLChart';
import { GlobalHeatmap } from '@/components/GlobalHeatmap';
import { KillSwitch } from '@/components/KillSwitch';
import { StrategyPanel } from '@/components/StrategyPanel';
import { OrderbookView } from '@/components/OrderbookView';
import { ShadowPanel } from '@/components/ShadowPanel';
import { useEngineWs } from '@/hooks/useEngineWs';

export default function OverviewPage() {
  const { connected, data } = useEngineWs();

  const strategyCount = data?.strategy_count ?? 0;
  const positionCount = data?.position_count ?? 0;
  const totalPnl      = data?.pnl?.total ?? 0;

  return (
    <div className="space-y-4">
      {/* Header bar: title + connection status + kill switch */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-mono font-semibold text-terminal-text">War Room</h2>
            {/* Connection status indicator */}
            <span
              className={`text-[9px] font-mono ${connected ? 'text-profit' : 'text-loss'}`}
              title={connected ? 'Engine connected' : 'Engine disconnected'}
            >
              {connected ? '● LIVE' : '● OFFLINE'}
            </span>
          </div>
          <p className="text-xs font-mono text-terminal-subtle mt-0.5">Real-time arbitrage engine status</p>

          {/* Live stats row */}
          {data && (
            <div className="flex items-center gap-4 mt-1">
              <span className="text-[10px] font-mono text-terminal-subtle">
                Strategies:{' '}
                <span className="text-terminal-text tabular-nums">{strategyCount}</span>
              </span>
              <span className="text-[10px] font-mono text-terminal-subtle">
                Positions:{' '}
                <span className="text-terminal-text tabular-nums">{positionCount}</span>
              </span>
              <span className="text-[10px] font-mono text-terminal-subtle">
                PnL:{' '}
                <span
                  className="tabular-nums"
                  style={{ color: totalPnl >= 0 ? '#00ff88' : '#ff4d4d' }}
                >
                  {totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}
                </span>
              </span>
            </div>
          )}
        </div>
        <KillSwitch />
      </div>

      {/* Top row: Heatmap + PnL chart */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <GlobalHeatmap />
        <PnLChart wsPnl={data?.pnl ?? null} />
      </div>

      {/* Bottom row: Strategies + Orderbook */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <StrategyPanel />
        <OrderbookView />
      </div>

      {/* Shadow Monitor — full width, visible only when active */}
      <ShadowPanel wsStats={data?.shadow_stats ?? null} />
    </div>
  );
}
