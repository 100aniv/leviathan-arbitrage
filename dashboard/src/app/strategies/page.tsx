'use client';

import { StrategyPanel } from '@/components/StrategyPanel';

export default function StrategiesPage() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-mono font-semibold text-terminal-text">Strategies</h2>
        <p className="text-xs font-mono text-terminal-subtle mt-1">Manage and monitor active arbitrage strategies</p>
      </div>

      <StrategyPanel />
    </div>
  );
}
