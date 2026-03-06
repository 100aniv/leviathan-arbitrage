'use client';

import { useEffect, useMemo, useState } from 'react';
import { getFeedManager } from '@/lib/websocket';
import { useWebSocket } from '@/hooks/useWebSocket';
import type { WsMessage } from '@/types';

const EXCHANGES = ['Binance', 'Bybit', 'OKX', 'Upbit', 'Bithumb', 'Coinone'];
const SYMBOLS   = ['BTC', 'ETH', 'XRP', 'SOL', 'DOGE', 'ADA', 'AVAX', 'DOT'];

type SpreadGrid = Record<string, Record<string, number | null>>;

function genMockGrid(): SpreadGrid {
  const g: SpreadGrid = {};
  for (const ex of EXCHANGES) {
    g[ex] = {};
    for (const sym of SYMBOLS) {
      g[ex][sym] = parseFloat(((Math.random() - 0.35) * 120).toFixed(2));
    }
  }
  return g;
}

function cellClass(bps: number | null): string {
  if (bps === null) return 'bg-terminal-muted text-terminal-subtle';
  if (bps > 30)  return 'bg-profit text-black font-bold';
  if (bps > 10)  return 'bg-profit/60 text-profit';
  if (bps > 0)   return 'bg-profit/25 text-profit/80';
  if (bps > -15) return 'bg-loss/25 text-loss/80';
  return 'bg-loss/60 text-loss';
}

interface MarketDataPayload {
  exchange?: string;
  symbol?: string;
  spread_bps?: number;
}

export function GlobalHeatmap() {
  const manager = useMemo(() => getFeedManager(), []);
  const { lastMessage, connected } = useWebSocket({ manager });
  const [grid, setGrid] = useState<SpreadGrid>(genMockGrid);
  const [updatedAt, setUpdatedAt] = useState<Date>(() => new Date());

  // Simulate live updates when no real WS data
  useEffect(() => {
    if (connected) return;
    const interval = setInterval(() => {
      setGrid(genMockGrid());
      setUpdatedAt(new Date());
    }, 3000);
    return () => clearInterval(interval);
  }, [connected]);

  // Apply real WS market_data updates
  useEffect(() => {
    if (!lastMessage || lastMessage.type !== 'market_data') return;
    const d = (lastMessage as WsMessage<MarketDataPayload>).data;
    if (d?.exchange && d?.symbol && d?.spread_bps !== undefined) {
      setGrid(prev => ({
        ...prev,
        [d.exchange!]: { ...prev[d.exchange!], [d.symbol!]: d.spread_bps! },
      }));
      setUpdatedAt(new Date());
    }
  }, [lastMessage]);

  return (
    <div className="bg-terminal-surface border border-terminal-border p-4">
      <div className="flex items-center justify-between mb-4">
        <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">
          Spread Heatmap
        </span>
        <div className="flex items-center gap-3">
          <span className={`text-[9px] font-mono uppercase ${connected ? 'text-profit' : 'text-terminal-subtle'}`}>
            {connected ? '● LIVE' : '○ MOCK'}
          </span>
          <span className="text-[10px] font-mono text-terminal-subtle tabular-nums">
            {updatedAt.toLocaleTimeString()}
          </span>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full border-collapse font-mono text-[11px]">
          <thead>
            <tr>
              <th className="text-left py-1 pr-3 text-terminal-subtle font-normal w-20">EX \ SYM</th>
              {SYMBOLS.map(sym => (
                <th key={sym} className="text-center py-1 px-0.5 text-terminal-subtle font-normal min-w-[52px]">
                  {sym}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {EXCHANGES.map(ex => (
              <tr key={ex}>
                <td className="py-0.5 pr-3 text-[10px] font-mono text-terminal-subtle uppercase tracking-wider whitespace-nowrap">
                  {ex}
                </td>
                {SYMBOLS.map(sym => {
                  const bps = grid[ex]?.[sym] ?? null;
                  return (
                    <td key={sym} className="py-0.5 px-0.5">
                      <div
                        className={`text-center py-1 px-0.5 text-[10px] tabular-nums transition-colors duration-500 ${cellClass(bps)}`}
                        title={`${ex} ${sym}: ${bps?.toFixed(2) ?? 'N/A'} bps`}
                      >
                        {bps !== null ? (bps > 0 ? '+' : '') + bps.toFixed(1) : '—'}
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-4 mt-3 flex-wrap">
        <span className="text-[10px] font-mono text-terminal-subtle">bps:</span>
        {[
          { label: '>30',    cls: 'bg-profit' },
          { label: '10–30',  cls: 'bg-profit/60' },
          { label: '0–10',   cls: 'bg-profit/25' },
          { label: '-15–0',  cls: 'bg-loss/25' },
          { label: '<-15',   cls: 'bg-loss/60' },
        ].map(({ label, cls }) => (
          <div key={label} className="flex items-center gap-1">
            <div className={`w-3 h-3 ${cls}`} />
            <span className="text-[10px] font-mono text-terminal-subtle">{label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
