'use client';

import { useState, useEffect, useRef } from 'react';
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
import { ModeSwitch }        from '@/components/ModeSwitch';
import { useEngineWs }       from '@/hooks/useEngineWs';

export default function OverviewPage() {
  const { connected, data } = useEngineWs();

  const [sysInfo, setSysInfo] = useState<{
    cpu_pct?: number;
    memory_mb?: number;
    latency_avg_ms?: number;
    ws_uptime_s?: number;
  } | null>(null);

  const wsStartRef = useRef(Date.now());
  useEffect(() => { if (connected) wsStartRef.current = Date.now(); }, [connected]);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(
          `${process.env.NEXT_PUBLIC_ENGINE_URL ?? 'http://localhost:8000'}/api/v1/system`,
          { headers: { Authorization: `Bearer ${localStorage.getItem('leviathan_token') ?? ''}` } }
        );
        if (res.ok) setSysInfo(await res.json());
      } catch {}
    };
    load();
    const i = setInterval(load, 5000);
    return () => clearInterval(i);
  }, []);

  return (
    <div className="space-y-4">
      {/* Header bar: title + connection status + Kill Switch top-right */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-mono font-semibold text-terminal-text">대시보드</h2>
            <span
              className={`text-[9px] font-mono ${connected ? 'text-profit' : 'text-loss'}`}
              title={connected ? 'Engine connected' : 'Engine disconnected'}
            >
              {connected ? '● LIVE' : '● OFFLINE'}
            </span>
          </div>
          <p className="text-xs font-mono text-terminal-subtle mt-1">Real-time arbitrage engine status</p>
        </div>
        <div className="flex items-center gap-3">
          <ModeSwitch currentMode={data?.mode ?? 'shadow'} />
          <KillSwitch />
        </div>
      </div>

      {/* Portfolio summary: status badge + 5 KPI + exchange status bar */}
      <PortfolioSummary />

      {/* System Performance */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
        {[
          { label: 'CPU',         value: sysInfo?.cpu_pct        != null ? `${sysInfo.cpu_pct.toFixed(1)}%`        : '—' },
          { label: 'Memory',      value: sysInfo?.memory_mb      != null ? `${sysInfo.memory_mb.toFixed(0)} MB`    : '—' },
          { label: 'Avg Latency', value: sysInfo?.latency_avg_ms != null ? `${sysInfo.latency_avg_ms.toFixed(0)}ms`: '—' },
          { label: 'WS Uptime',   value: connected ? `${Math.floor((Date.now() - wsStartRef.current) / 1000)}s` : 'N/A' },
        ].map(({ label, value }) => (
          <div key={label} className="bg-terminal-surface border border-terminal-border p-3">
            <div className="text-[10px] font-mono text-terminal-subtle uppercase tracking-wider">{label}</div>
            <div className="text-sm font-mono text-terminal-text mt-1 tabular-nums">{value}</div>
          </div>
        ))}
      </div>

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
      <ShadowPanel wsStats={data?.shadow_stats ?? null} mode={data?.mode ?? 'shadow'} />
    </div>
  );
}
