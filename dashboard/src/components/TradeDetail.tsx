'use client';

import type { Trade } from '@/types';

interface TradeDetailProps {
  trade: Trade;
  onClose: () => void;
}

export function TradeDetail({ trade, onClose }: TradeDetailProps) {
  const netPnl = trade.net_pnl ?? trade.pnl;
  const pnlColor = netPnl >= 0 ? 'text-profit' : 'text-loss';

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 bg-black/50 z-40" onClick={onClose} />

      {/* Side panel */}
      <div className="fixed right-0 top-0 h-full w-full sm:w-80 bg-terminal-surface border-l border-terminal-border z-50 overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-terminal-border sticky top-0 bg-terminal-surface">
          <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-terminal-subtle">
            Trade Detail
          </span>
          <button
            onClick={onClose}
            className="text-terminal-subtle hover:text-terminal-text font-mono text-sm leading-none"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="p-4 space-y-4 text-xs font-mono">
          {/* ID */}
          <Row label="ID">
            <span className="text-terminal-text break-all">{trade.id}</span>
          </Row>

          {/* Symbol + Strategy */}
          <div className="grid grid-cols-2 gap-3">
            <Row label="Symbol">
              <span className="text-sm font-semibold text-terminal-text">{trade.symbol}</span>
            </Row>
            <Row label="Strategy">
              <span className="text-accent">{trade.strategy_id}</span>
            </Row>
          </div>

          {/* Route */}
          <Row label="Route">
            <span className="text-terminal-text">
              {trade.buy_exchange}{' '}
              <span className="text-terminal-subtle">→</span>{' '}
              {trade.sell_exchange}
            </span>
          </Row>

          {/* Prices */}
          <div className="grid grid-cols-2 gap-3">
            <Row label="Entry">
              <span className="tabular-nums text-terminal-text">${trade.entry_price.toFixed(4)}</span>
            </Row>
            <Row label="Exit">
              <span className="tabular-nums text-terminal-text">${trade.exit_price.toFixed(4)}</span>
            </Row>
          </div>

          {/* Size */}
          <Row label="Size">
            <span className="tabular-nums text-terminal-text">{trade.size.toFixed(6)}</span>
          </Row>

          {/* Spread */}
          {trade.spread_bps !== undefined && (
            <Row label="Spread">
              <span className={`tabular-nums ${trade.spread_bps >= 0 ? 'text-profit' : 'text-loss'}`}>
                {trade.spread_bps >= 0 ? '+' : ''}{trade.spread_bps.toFixed(2)} bps
              </span>
            </Row>
          )}

          {/* Expected PnL */}
          {trade.expected_pnl !== undefined && (
            <Row label="Expected PnL">
              <span className="tabular-nums text-terminal-text">
                {trade.expected_pnl >= 0 ? '+' : ''}${trade.expected_pnl.toFixed(4)}
              </span>
            </Row>
          )}

          {/* Fee */}
          {trade.fee_usd !== undefined && (
            <Row label="Fee">
              <span className="tabular-nums text-loss">-${trade.fee_usd.toFixed(4)}</span>
            </Row>
          )}

          {/* Net PnL — highlighted */}
          <div className="border-t border-terminal-border pt-3">
            <Row label="Net PnL">
              <span className={`text-base font-semibold tabular-nums ${pnlColor}`}>
                {netPnl >= 0 ? '+' : ''}${netPnl.toFixed(4)}
              </span>
            </Row>
          </div>

          {/* Status */}
          <Row label="Status">
            <span
              className="px-1.5 py-0.5 text-[10px]"
              style={{
                backgroundColor: trade.status === 'closed' ? 'rgba(5,150,105,0.1)' : 'rgba(245,158,11,0.1)',
                color: trade.status === 'closed' ? '#059669' : '#f59e0b',
                border: `1px solid ${trade.status === 'closed' ? 'rgba(5,150,105,0.2)' : 'rgba(245,158,11,0.2)'}`,
              }}
            >
              {trade.status.toUpperCase()}
            </span>
          </Row>

          {/* Reason */}
          {trade.reason && (
            <Row label="Reason">
              <span className="text-terminal-subtle">{trade.reason}</span>
            </Row>
          )}

          {/* Timestamp */}
          <Row label="Time">
            <span className="tabular-nums text-terminal-subtle">
              {new Date(trade.timestamp).toLocaleString()}
            </span>
          </Row>
        </div>
      </div>
    </>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[9px] font-mono text-terminal-subtle uppercase tracking-wider mb-0.5">
        {label}
      </div>
      {children}
    </div>
  );
}
