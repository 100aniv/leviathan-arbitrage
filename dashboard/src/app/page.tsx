'use client';

import { PnLChart } from '@/components/PnLChart';
import { GlobalHeatmap } from '@/components/GlobalHeatmap';
import { KillSwitch } from '@/components/KillSwitch';
import { StrategyPanel } from '@/components/StrategyPanel';
import { OrderbookView } from '@/components/OrderbookView';

export default function OverviewPage() {
  return (
    <div className="space-y-4">
      {/* Header bar: title + kill switch */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-mono font-semibold text-terminal-text">War Room</h2>
          <p className="text-xs font-mono text-terminal-subtle mt-0.5">Real-time arbitrage engine status</p>
        </div>
        <KillSwitch />
      </div>

      {/* Top row: Heatmap + PnL chart */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <GlobalHeatmap />
        <PnLChart />
      </div>

      {/* Bottom row: Strategies + Orderbook */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <StrategyPanel />
        <OrderbookView />
      </div>
    </div>
  );
}
