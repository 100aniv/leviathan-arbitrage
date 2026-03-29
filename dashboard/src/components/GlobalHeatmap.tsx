'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { getFeedManager } from '@/lib/websocket';
import { useWebSocket } from '@/hooks/useWebSocket';
import { useApi } from '@/hooks/useApi';
import { getExchangeStatus, getSpreads } from '@/lib/api';
import type { SpreadItem } from '@/lib/api';
import type { WsMessage, ExchangeStatus } from '@/types';

const FALLBACK_EXCHANGES = ['Binance', 'Bybit', 'OKX', 'Upbit', 'Bithumb', 'Coinone'];

const MAJOR_8 = ['BTC', 'ETH', 'XRP', 'SOL', 'BNB', 'DOGE', 'ADA', 'AVAX'];
const TOP_20  = [...MAJOR_8, 'DOT', 'LINK', 'MATIC', 'UNI', 'SHIB', 'LTC', 'TRX', 'ATOM', 'APT', 'ARB', 'OP', 'NEAR'];

type SymbolSet = 'major8' | 'top20' | 'all' | 'custom';

const SYMBOL_SET_LABELS: Record<SymbolSet, string> = {
  major8: 'Major 8',
  top20:  'Top 20',
  all:    'All',
  custom: 'Custom',
};

const LS_CUSTOM_KEY = 'leviathan_heatmap_custom';

type SpreadGrid = Record<string, Record<string, number | null>>;

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
  symbol?:   string;
  spread_bps?: number;
}

export function GlobalHeatmap() {
  const manager = useMemo(() => getFeedManager(), []);
  const { lastMessage, connected } = useWebSocket({ manager });

  // Real exchange status from API
  const { data: exchangeStatus } = useApi<Record<string, ExchangeStatus>>(
    '/exchanges',
    getExchangeStatus,
    { refreshInterval: 5000 },
  );

  // Derive exchange list: API keys → display names, fallback to static list
  const exchanges = useMemo<string[]>(() => {
    if (exchangeStatus && Object.keys(exchangeStatus).length > 0) {
      return Object.keys(exchangeStatus).map(id =>
        id.charAt(0).toUpperCase() + id.slice(1)
      );
    }
    return FALLBACK_EXCHANGES;
  }, [exchangeStatus]);

  const isLiveData = exchangeStatus && Object.keys(exchangeStatus).length > 0;

  // Symbol set selection
  const [symbolSet,     setSymbolSet]     = useState<SymbolSet>('major8');
  const [customInput,   setCustomInput]   = useState<string>(() => {
    if (typeof window !== 'undefined') {
      return localStorage.getItem(LS_CUSTOM_KEY) ?? '';
    }
    return '';
  });
  const [showCustomBox, setShowCustomBox] = useState(false);
  const [allSymbols,    setAllSymbols]    = useState<string[]>(TOP_20);

  // Derive all symbols from exchangeStatus when 'all' preset is selected
  useEffect(() => {
    if (symbolSet !== 'all' || !exchangeStatus) return;
    const syms = new Set<string>();
    Object.values(exchangeStatus).forEach((ex) => {
      const exData = ex as unknown as { symbols?: string[] };
      (exData.symbols ?? []).forEach((s: string) => {
        const sym = s.replace(/\/USDT$|USDT$/, '');
        if (sym) syms.add(sym);
      });
    });
    if (syms.size > 0) setAllSymbols(Array.from(syms).sort());
  }, [symbolSet, exchangeStatus]);

  const activeSymbols = useMemo<string[]>(() => {
    switch (symbolSet) {
      case 'major8': return MAJOR_8;
      case 'top20':  return TOP_20;
      case 'all':    return allSymbols;
      case 'custom': {
        const parsed = customInput
          .split(',')
          .map(s => s.trim().toUpperCase())
          .filter(Boolean);
        return parsed.length > 0 ? parsed : MAJOR_8;
      }
    }
  }, [symbolSet, customInput, allSymbols]);

  const [grid,      setGrid]      = useState<SpreadGrid>({});
  const [updatedAt, setUpdatedAt] = useState<Date>(() => new Date());

  // REST spread polling (10s fallback) — backend returns flat SpreadItem[]
  const { data: spreadsData } = useApi<SpreadItem[]>(
    '/api/v1/spreads',
    getSpreads,
    { refreshInterval: 10000 },
  );

  useEffect(() => {
    if (!spreadsData || !Array.isArray(spreadsData)) return;
    const newGrid: SpreadGrid = {};
    for (const item of spreadsData) {
      const ex = item.exchange_a.charAt(0).toUpperCase() + item.exchange_a.slice(1);
      const sym = item.symbol.replace(/\/USDT$/, '');
      if (!newGrid[ex]) newGrid[ex] = {};
      newGrid[ex][sym] = item.spread_bps;
    }
    setGrid(newGrid);
    setUpdatedAt(new Date());
  }, [spreadsData]);

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

  // Save custom symbols to localStorage on change
  const handleCustomSave = () => {
    if (typeof window !== 'undefined') {
      localStorage.setItem(LS_CUSTOM_KEY, customInput);
    }
    setSymbolSet('custom');
    setShowCustomBox(false);
  };

  const customBoxRef = useRef<HTMLDivElement>(null);

  return (
    <div className="bg-terminal-surface border border-terminal-border p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">
            Spread Heatmap
          </span>

          {/* Symbol set dropdown */}
          <div className="relative">
            <div className="flex items-center gap-1">
              {(Object.keys(SYMBOL_SET_LABELS) as SymbolSet[]).map(key => (
                <button
                  key={key}
                  onClick={() => {
                    if (key === 'custom') {
                      setShowCustomBox(v => !v);
                    } else {
                      setSymbolSet(key);
                      setShowCustomBox(false);
                    }
                  }}
                  className={`px-1.5 py-0.5 text-[9px] font-mono uppercase tracking-wider border transition-colors ${
                    symbolSet === key
                      ? 'text-accent border-accent/50 bg-accent/10'
                      : 'text-terminal-subtle border-terminal-border hover:border-terminal-text/30'
                  }`}
                >
                  {SYMBOL_SET_LABELS[key]}
                </button>
              ))}
            </div>

            {showCustomBox && (
              <div
                ref={customBoxRef}
                className="absolute top-full left-0 mt-1 z-50 bg-terminal-surface border border-terminal-border p-2 min-w-[220px]"
              >
                <p className="text-[9px] font-mono text-terminal-subtle mb-1">
                  Comma-separated symbols (e.g. BTC,ETH,SOL)
                </p>
                <input
                  type="text"
                  value={customInput}
                  onChange={e => setCustomInput(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleCustomSave()}
                  placeholder="BTC,ETH,SOL..."
                  className="w-full bg-terminal-bg border border-terminal-border text-terminal-text text-[10px] font-mono px-2 py-1 focus:outline-none focus:border-accent"
                  autoFocus
                />
                <div className="flex gap-1 mt-1.5">
                  <button
                    onClick={handleCustomSave}
                    className="px-2 py-0.5 text-[9px] font-mono bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30"
                  >
                    Apply
                  </button>
                  <button
                    onClick={() => setShowCustomBox(false)}
                    className="px-2 py-0.5 text-[9px] font-mono text-terminal-subtle border border-terminal-border hover:text-terminal-text"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="flex items-center gap-3">
          <span className={`text-[9px] font-mono uppercase ${connected ? 'text-profit' : 'text-terminal-subtle'}`}>
            {connected ? '● LIVE' : '○ OFFLINE'}
          </span>
          <span className={`text-[9px] font-mono uppercase ${isLiveData ? 'text-accent' : 'text-terminal-subtle'}`}>
            {isLiveData ? '◆ API' : '◇ STATIC'}
          </span>
          <span className="text-[10px] font-mono text-terminal-subtle tabular-nums" suppressHydrationWarning>
            {updatedAt.toLocaleTimeString()}
          </span>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full border-collapse font-mono text-[11px]">
          <thead>
            <tr>
              <th className="text-left py-1 pr-3 text-terminal-subtle font-normal w-20">EX \ SYM</th>
              {activeSymbols.map(sym => (
                <th key={sym} className="text-center py-1 px-0.5 text-terminal-subtle font-normal min-w-[52px]">
                  {sym}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {exchanges.map(ex => (
              <tr key={ex}>
                <td className="py-0.5 pr-3 text-[10px] font-mono text-terminal-subtle uppercase tracking-wider whitespace-nowrap">
                  <div className="flex items-center gap-1.5">
                    {exchangeStatus && (
                      <span className={`w-1 h-1 rounded-full ${
                        exchangeStatus[ex.toLowerCase()]?.connected ? 'bg-profit' : 'bg-terminal-muted'
                      }`} />
                    )}
                    {ex}
                  </div>
                </td>
                {activeSymbols.map(sym => {
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
          { label: '>30',   cls: 'bg-profit' },
          { label: '10–30', cls: 'bg-profit/60' },
          { label: '0–10',  cls: 'bg-profit/25' },
          { label: '-15–0', cls: 'bg-loss/25' },
          { label: '<-15',  cls: 'bg-loss/60' },
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
