'use client';

import { useState } from 'react';
import { useApi } from '@/hooks/useApi';
import { getPositions } from '@/lib/api';
import type { Position } from '@/types';

type SortKey = keyof Position;
type SortDir = 'asc' | 'desc';

const MOCK: Position[] = [
  { strategy_id: 'tri-arb',  exchange_id: 'binance', symbol: 'BTC/USDT', side: 'LONG',  quantity: 0.05,  entry_price: 64850,  mark_price: 65100,  unrealized_pnl:  12.50, realized_pnl: 0 },
  { strategy_id: 'kim-arb',  exchange_id: 'upbit',   symbol: 'ETH/USDT', side: 'LONG',  quantity: 0.8,   entry_price: 3480,   mark_price: 3495,   unrealized_pnl:  12.00, realized_pnl: 0 },
  { strategy_id: 'stat-arb', exchange_id: 'okx',     symbol: 'SOL/USDT', side: 'SHORT', quantity: 5,     entry_price: 146.5,  mark_price: 144.8,  unrealized_pnl:  -8.50, realized_pnl: 0 },
  { strategy_id: 'tri-arb',  exchange_id: 'bybit',   symbol: 'XRP/USDT', side: 'LONG',  quantity: 1000,  entry_price: 0.5812, mark_price: 0.5845, unrealized_pnl:   3.30, realized_pnl: 0 },
  { strategy_id: 'mm-eth',   exchange_id: 'binance', symbol: 'ETH/USDT', side: 'SHORT', quantity: 0.3,   entry_price: 3510,   mark_price: 3495,   unrealized_pnl:   4.50, realized_pnl: 0 },
];

function fmtPrice(price: number): string {
  if (price < 1)   return price.toFixed(5);
  if (price < 100) return price.toFixed(2);
  return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function Skeleton() {
  return (
    <div className="bg-terminal-surface border border-terminal-border p-4">
      <div className="h-3 w-28 bg-terminal-muted animate-pulse mb-4" />
      <div className="space-y-1">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-8 bg-terminal-muted/50 animate-pulse" />
        ))}
      </div>
    </div>
  );
}

const COLUMNS: { key: SortKey; label: string; align?: 'right' }[] = [
  { key: 'strategy_id',    label: 'Strategy' },
  { key: 'exchange_id',    label: 'Exchange' },
  { key: 'symbol',         label: 'Symbol' },
  { key: 'side',           label: 'Side' },
  { key: 'quantity',       label: 'Size',         align: 'right' },
  { key: 'entry_price',    label: 'Entry',        align: 'right' },
  { key: 'mark_price',     label: 'Mark',         align: 'right' },
  { key: 'unrealized_pnl', label: 'uPnL',         align: 'right' },
  { key: 'realized_pnl',   label: 'rPnL',         align: 'right' },
];

export function PositionTable() {
  const { data, error, isLoading, mutate } = useApi<Position[]>(
    '/trading/positions',
    getPositions,
    { refreshInterval: 3000 },
  );

  const [sortKey, setSortKey] = useState<SortKey>('unrealized_pnl');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const raw: Position[] =
    data && data.length > 0 ? data : MOCK;

  const sorted = [...raw].sort((a, b) => {
    const av = a[sortKey];
    const bv = b[sortKey];
    if (typeof av === 'number' && typeof bv === 'number') {
      return sortDir === 'asc' ? av - bv : bv - av;
    }
    return sortDir === 'asc'
      ? String(av).localeCompare(String(bv))
      : String(bv).localeCompare(String(av));
  });

  const handleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir(d => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortKey(key); setSortDir('desc'); }
  };

  if (isLoading && !data) return <Skeleton />;

  if (error) {
    return (
      <div className="bg-terminal-surface border border-terminal-border p-4">
        <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle block mb-4">
          Open Positions
        </span>
        <div className="flex flex-col items-center gap-2 py-8">
          <p className="text-xs font-mono text-loss">Failed to load positions</p>
          <button
            onClick={() => mutate()}
            aria-label="포지션 다시 불러오기"
            className="text-[10px] font-mono border border-terminal-border px-3 py-1 text-terminal-subtle hover:text-terminal-text transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
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
          Open Positions
        </span>
        <span className="text-[10px] font-mono text-terminal-subtle tabular-nums">
          {sorted.length} positions
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-[11px] font-mono">
          <thead>
            <tr className="text-[10px] text-terminal-subtle uppercase tracking-wider border-b border-terminal-border">
              {COLUMNS.map(({ key, label, align }) => (
                <th
                  key={key}
                  onClick={() => handleSort(key)}
                  className={`py-2 pr-3 font-normal cursor-pointer hover:text-terminal-text transition-colors select-none ${
                    align === 'right' ? 'text-right' : 'text-left'
                  }`}
                >
                  {label}
                  {sortKey === key && (
                    <span className="ml-0.5">{sortDir === 'asc' ? '↑' : '↓'}</span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((pos, i) => (
              <tr
                key={i}
                className="border-b border-terminal-border/30 hover:bg-terminal-muted/20 transition-colors"
              >
                <td className="py-1.5 pr-3 text-terminal-text">{pos.strategy_id}</td>
                <td className="py-1.5 pr-3 text-terminal-subtle uppercase">{pos.exchange_id}</td>
                <td className="py-1.5 pr-3 text-terminal-text">{pos.symbol}</td>
                <td className={`py-1.5 pr-3 font-semibold ${pos.side === 'LONG' ? 'text-profit' : 'text-loss'}`}>
                  {pos.side}
                </td>
                <td className="py-1.5 pr-3 text-right tabular-nums text-terminal-text">{pos.quantity}</td>
                <td className="py-1.5 pr-3 text-right tabular-nums text-terminal-text">{fmtPrice(pos.entry_price)}</td>
                <td className="py-1.5 pr-3 text-right tabular-nums text-terminal-text">{fmtPrice(pos.mark_price)}</td>
                <td className={`py-1.5 pr-3 text-right tabular-nums font-semibold ${pos.unrealized_pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                  {pos.unrealized_pnl >= 0 ? '+' : ''}${pos.unrealized_pnl.toFixed(2)}
                </td>
                <td className={`py-1.5 pr-3 text-right tabular-nums ${pos.realized_pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                  {pos.realized_pnl >= 0 ? '+' : ''}${pos.realized_pnl.toFixed(2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {sorted.length === 0 && (
          <div className="flex items-center justify-center py-10">
            <span className="text-xs font-mono text-terminal-subtle">No open positions</span>
          </div>
        )}
      </div>
    </div>
  );
}
