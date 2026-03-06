'use client';

import { useEffect, useMemo, useState } from 'react';
import { getFeedManager } from '@/lib/websocket';
import { useWebSocket } from '@/hooks/useWebSocket';
import type { WsMessage } from '@/types';

type OrderStatus = 'filled' | 'pending' | 'cancelled';
type OrderSide   = 'BUY' | 'SELL';

interface Order {
  id: string;
  time: string;
  strategy: string;
  exchange: string;
  symbol: string;
  side: OrderSide;
  size: number;
  price: number;
  status: OrderStatus;
}

const STRATEGIES = ['tri-arb', 'kim-arb', 'stat-arb', 'mm-eth'];
const EXCHANGES  = ['Binance', 'Bybit', 'OKX', 'Upbit'];
const SYMBOLS    = ['BTC/USDT', 'ETH/USDT', 'XRP/USDT', 'SOL/USDT'];

let _id = 1;
function genOrder(): Order {
  const sym   = SYMBOLS[Math.floor(Math.random() * SYMBOLS.length)];
  const base  = sym.startsWith('BTC') ? 65000 :
                sym.startsWith('ETH') ? 3500  :
                sym.startsWith('XRP') ? 0.58  : 145;
  const price = base + (Math.random() - 0.5) * base * 0.002;
  const r     = Math.random();
  return {
    id:       String(_id++),
    time:     new Date().toLocaleTimeString('en-GB'),
    strategy: STRATEGIES[Math.floor(Math.random() * STRATEGIES.length)],
    exchange: EXCHANGES[Math.floor(Math.random() * EXCHANGES.length)],
    symbol:   sym,
    side:     Math.random() > 0.5 ? 'BUY' : 'SELL',
    size:     parseFloat((Math.random() * 3 + 0.001).toFixed(4)),
    price:    parseFloat(price.toFixed(base < 1 ? 5 : 2)),
    status:   r > 0.15 ? 'filled' : r > 0.07 ? 'pending' : 'cancelled',
  };
}

const STATUS_CLS: Record<OrderStatus, string> = {
  filled:    'text-profit',
  pending:   'text-warn animate-pulse',
  cancelled: 'text-terminal-subtle line-through',
};

export function OrderFlow() {
  const manager = useMemo(() => getFeedManager(), []);
  const { lastMessage } = useWebSocket({ manager });

  const [orders, setOrders] = useState<Order[]>(() =>
    Array.from({ length: 20 }, genOrder)
  );

  // Mock stream: inject a new order every ~1.5 s
  useEffect(() => {
    const id = setInterval(() => {
      setOrders(prev => [genOrder(), ...prev.slice(0, 99)]);
    }, 1500);
    return () => clearInterval(id);
  }, []);

  // Apply real WS order events
  useEffect(() => {
    if (!lastMessage) return;
    const d = (lastMessage as WsMessage<Partial<Order>>).data;
    if (d?.id && d?.price && d?.side) {
      setOrders(prev => [d as Order, ...prev.slice(0, 99)]);
    }
  }, [lastMessage]);

  return (
    <div className="bg-terminal-surface border border-terminal-border p-4 flex flex-col">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">
          Order Flow
        </span>
        <span className="text-[10px] font-mono text-terminal-subtle tabular-nums">
          {orders.length} orders
        </span>
      </div>

      <div
        className="overflow-y-auto max-h-72"
        style={{ scrollbarWidth: 'thin', scrollbarColor: '#2a303a transparent' }}
      >
        <table className="w-full text-[11px] font-mono">
          <thead className="sticky top-0 bg-terminal-surface z-10">
            <tr className="text-[10px] text-terminal-subtle uppercase tracking-wider border-b border-terminal-border">
              <th className="text-left py-1.5 pr-2 font-normal">Time</th>
              <th className="text-left py-1.5 pr-2 font-normal">Strategy</th>
              <th className="text-left py-1.5 pr-2 font-normal hidden md:table-cell">Exchange</th>
              <th className="text-left py-1.5 pr-2 font-normal">Symbol</th>
              <th className="text-left py-1.5 pr-2 font-normal">Side</th>
              <th className="text-right py-1.5 pr-2 font-normal">Size</th>
              <th className="text-right py-1.5 pr-2 font-normal">Price</th>
              <th className="text-right py-1.5 font-normal">Status</th>
            </tr>
          </thead>
          <tbody>
            {orders.map((o, i) => (
              <tr
                key={o.id}
                className={`border-t border-terminal-border/40 transition-colors ${
                  i === 0 ? 'bg-terminal-muted/25' : 'hover:bg-terminal-muted/10'
                }`}
              >
                <td className="py-1 pr-2 text-terminal-subtle tabular-nums">{o.time}</td>
                <td className="py-1 pr-2 text-terminal-text">{o.strategy}</td>
                <td className="py-1 pr-2 text-terminal-subtle hidden md:table-cell">{o.exchange}</td>
                <td className="py-1 pr-2 text-terminal-text">{o.symbol}</td>
                <td className={`py-1 pr-2 font-semibold ${o.side === 'BUY' ? 'text-profit' : 'text-loss'}`}>
                  {o.side}
                </td>
                <td className="py-1 pr-2 text-right tabular-nums text-terminal-text">{o.size}</td>
                <td className="py-1 pr-2 text-right tabular-nums text-terminal-text">
                  {o.price.toLocaleString()}
                </td>
                <td className={`py-1 text-right text-[10px] uppercase ${STATUS_CLS[o.status]}`}>
                  {o.status}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
