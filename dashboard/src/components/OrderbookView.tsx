'use client';

import { useEffect, useMemo, useState } from 'react';
import { getFeedManager } from '@/lib/websocket';
import { useWebSocket } from '@/hooks/useWebSocket';
import { useApi } from '@/hooks/useApi';
import { getExchangeStatus } from '@/lib/api';
import type { WsMessage, ExchangeStatus } from '@/types';

const FALLBACK_SYMBOLS   = ['BTC/USDT', 'ETH/USDT', 'XRP/USDT', 'SOL/USDT', 'DOGE/USDT'];
const FALLBACK_EXCHANGES = ['Binance', 'Bybit', 'OKX', 'Upbit'];

interface Level {
  price: number;
  size: number;
}

interface Orderbook {
  bids: Level[];
  asks: Level[];
  spread: number;
  spreadPct: number;
}

interface MarketBookPayload {
  symbol?: string;
  bids?: Level[];
  asks?: Level[];
}

function basePrice(symbol: string): number {
  if (symbol.startsWith('BTC'))  return 65000;
  if (symbol.startsWith('ETH'))  return 3500;
  if (symbol.startsWith('SOL'))  return 145;
  if (symbol.startsWith('DOGE')) return 0.12;
  return 0.58; // XRP
}

function genBook(symbol: string): Orderbook {
  const base     = basePrice(symbol);
  const tick     = base * 0.00015;
  const decimals = base < 1 ? 5 : base < 10 ? 4 : 2;

  const bids: Level[] = Array.from({ length: 10 }, (_, i) => ({
    price: parseFloat((base - i * tick).toFixed(decimals)),
    size:  parseFloat((Math.random() * 4 + 0.05).toFixed(4)),
  }));
  const asks: Level[] = Array.from({ length: 10 }, (_, i) => ({
    price: parseFloat((base + (i + 1) * tick).toFixed(decimals)),
    size:  parseFloat((Math.random() * 4 + 0.05).toFixed(4)),
  }));

  const spread = asks[0].price - bids[0].price;
  return {
    bids,
    asks,
    spread:    parseFloat(spread.toFixed(decimals)),
    spreadPct: parseFloat(((spread / bids[0].price) * 100).toFixed(4)),
  };
}

export function OrderbookView() {
  const manager = useMemo(() => getFeedManager(), []);
  const { lastMessage, connected } = useWebSocket({ manager });

  // Real exchange status from API
  const { data: exchangeStatus } = useApi<Record<string, ExchangeStatus>>(
    '/exchanges',
    getExchangeStatus,
    { refreshInterval: 5000 },
  );

  // Dynamic exchange list from API, fallback to static
  const exchanges = useMemo<string[]>(() => {
    if (exchangeStatus && Object.keys(exchangeStatus).length > 0) {
      return Object.keys(exchangeStatus)
        .filter(id => exchangeStatus[id].connected)
        .map(id => id.charAt(0).toUpperCase() + id.slice(1));
    }
    return FALLBACK_EXCHANGES;
  }, [exchangeStatus]);

  const isLiveData = exchangeStatus && Object.keys(exchangeStatus).length > 0;

  const [symbol,   setSymbol]   = useState(FALLBACK_SYMBOLS[0]);
  const [exchange, setExchange] = useState(FALLBACK_EXCHANGES[0]);
  const [book,     setBook]     = useState<Orderbook>(() => genBook(FALLBACK_SYMBOLS[0]));

  // Keep exchange selection valid when list changes
  useEffect(() => {
    if (exchanges.length > 0 && !exchanges.includes(exchange)) {
      setExchange(exchanges[0]);
    }
  }, [exchanges, exchange]);

  // Mock refresh when no real feed
  useEffect(() => {
    setBook(genBook(symbol));
    if (connected) return;
    const id = setInterval(() => setBook(genBook(symbol)), 800);
    return () => clearInterval(id);
  }, [symbol, connected]);

  // Apply live WS orderbook updates
  useEffect(() => {
    if (!lastMessage || lastMessage.type !== 'market_data') return;
    const d = (lastMessage as WsMessage<MarketBookPayload>).data;
    if (d?.symbol === symbol && d.bids && d.asks) {
      const spread = d.asks[0].price - d.bids[0].price;
      setBook({
        bids:      d.bids.slice(0, 10),
        asks:      d.asks.slice(0, 10),
        spread,
        spreadPct: (spread / d.bids[0].price) * 100,
      });
    }
  }, [lastMessage, symbol]);

  const maxBid = Math.max(...book.bids.map(b => b.size));
  const maxAsk = Math.max(...book.asks.map(a => a.size));

  return (
    <div className="bg-terminal-surface border border-terminal-border p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">
          Orderbook
        </span>
        <div className="flex items-center gap-2">
          <span className={`text-[9px] font-mono uppercase ${isLiveData ? 'text-accent' : 'text-terminal-subtle'}`}>
            {isLiveData ? '◆ API' : '◇ STATIC'}
          </span>
          <span className={`text-[9px] font-mono uppercase ${connected ? 'text-profit' : 'text-terminal-subtle'}`}>
            {connected ? '● LIVE' : '○ MOCK'}
          </span>
        </div>
      </div>

      {/* Controls — dynamic selectors */}
      <div className="flex gap-2 mb-3">
        <select
          value={symbol}
          onChange={e => setSymbol(e.target.value)}
          className="bg-terminal-bg border border-terminal-border text-terminal-text text-xs font-mono px-2 py-1 focus:outline-none focus:border-accent"
        >
          {FALLBACK_SYMBOLS.map(s => <option key={s}>{s}</option>)}
        </select>
        <select
          value={exchange}
          onChange={e => setExchange(e.target.value)}
          className="bg-terminal-bg border border-terminal-border text-terminal-text text-xs font-mono px-2 py-1 focus:outline-none focus:border-accent"
        >
          {exchanges.map(ex => <option key={ex}>{ex}</option>)}
        </select>
      </div>

      {/* Spread */}
      <div className="flex items-center justify-between bg-terminal-bg border border-terminal-border px-3 py-1.5 mb-3">
        <span className="text-[10px] font-mono text-terminal-subtle">Spread</span>
        <span className="text-[10px] font-mono text-warn tabular-nums">
          {book.spread} ({book.spreadPct.toFixed(4)}%)
        </span>
      </div>

      {/* Book columns */}
      <div className="grid grid-cols-2 gap-2">
        {/* Bids */}
        <div>
          <div className="grid grid-cols-2 text-[10px] font-mono text-terminal-subtle mb-1 px-1">
            <span>Bid</span><span className="text-right">Size</span>
          </div>
          {book.bids.map((bid, i) => (
            <div key={i} className="relative mb-0.5">
              <div
                className="absolute inset-y-0 left-0 bg-profit/15"
                style={{ width: `${(bid.size / maxBid) * 100}%` }}
              />
              <div className="relative grid grid-cols-2 px-1 py-0.5 text-[11px] font-mono tabular-nums">
                <span className="text-profit">{bid.price.toLocaleString()}</span>
                <span className="text-right text-terminal-text">{bid.size.toFixed(4)}</span>
              </div>
            </div>
          ))}
        </div>

        {/* Asks */}
        <div>
          <div className="grid grid-cols-2 text-[10px] font-mono text-terminal-subtle mb-1 px-1">
            <span>Ask</span><span className="text-right">Size</span>
          </div>
          {book.asks.map((ask, i) => (
            <div key={i} className="relative mb-0.5">
              <div
                className="absolute inset-y-0 right-0 bg-loss/15"
                style={{ width: `${(ask.size / maxAsk) * 100}%` }}
              />
              <div className="relative grid grid-cols-2 px-1 py-0.5 text-[11px] font-mono tabular-nums">
                <span className="text-loss">{ask.price.toLocaleString()}</span>
                <span className="text-right text-terminal-text">{ask.size.toFixed(4)}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
