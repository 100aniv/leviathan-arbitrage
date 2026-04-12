'use client';

import { useState } from 'react';
import { PieChart, Pie, Cell, Tooltip as PieTooltip, ResponsiveContainer as PieResponsive } from 'recharts';
import clsx from 'clsx';
import { PnLChart }    from '@/components/PnLChart';
import { EventFeed }   from '@/components/EventFeed';
import { ModeSwitch }  from '@/components/ModeSwitch';
import { useEngineWs } from '@/hooks/useEngineWs';
import { useApi }      from '@/hooks/useApi';
import {
  getPortfolioSummary, getRiskMetrics, getStrategies, getTrades, getExchangeStatus, getLivePositions,
} from '@/lib/api';
import type { LivePositionsResponse } from '@/lib/api';
import type {
  PortfolioSummaryResponse,
  RiskMetrics,
  Strategy,
  ShadowStrategyBreakdown,
  Trade,
  ExchangeStatus,
} from '@/types';

// ─── Constants ────────────────────────────────────────────────────────────────

const STRATEGY_NAMES: Record<string, string> = {
  funding_rate_arb:    '펀딩비 수익',
  funding_rate_arb_v1: '펀딩비 수익',
  spot_futures_basis:  '현물-선물 차익',
  spot_futures_v1:     '현물-선물 차익',
  futures_futures:     '선물-선물 차익',
  futures_futures_v1:  '선물-선물 차익',
  statistical_arb:     '통계적 차익',
  statistical_arb_v1:  '통계적 차익',
  triangular:          '삼각 차익',
  triangular_v1:       '삼각 차익',
  cross_exchange_spot: '교차 거래소 차익',
  cross_exchange_v1:   '교차 거래소 차익',
  cex_dex_hybrid:      'CEX-DEX 차익',
  cex_dex_v1:          'CEX-DEX 차익',
};

const STRATEGY_ABBR: Record<string, string> = {
  funding_rate_arb:      'FR',
  funding_rate_arb_v1:   'FR',
  spot_futures_basis:    'SF',
  spot_futures_v1:       'SF',
  futures_futures:       'FF',
  futures_futures_v1:    'FF',
  statistical_arb:       'SA',
  statistical_arb_v1:    'SA',
  triangular:            'TRI',
  triangular_v1:         'TRI',
  cross_exchange_spot:   'CE',
  cross_exchange_v1:     'CE',
  cex_dex_hybrid:        'CD',
  cex_dex_v1:            'CD',
};

const STRATEGY_COLORS: Record<string, string> = {
  funding_rate_arb:    'bg-info/10 text-info',
  funding_rate_arb_v1: 'bg-info/10 text-info',
  spot_futures_basis:  'bg-success/10 text-success',
  spot_futures_v1:     'bg-success/10 text-success',
  futures_futures:     'bg-brand-subtle text-brand',
  futures_futures_v1:  'bg-brand-subtle text-brand',
  statistical_arb:     'bg-warning/10 text-warning',
  statistical_arb_v1:  'bg-warning/10 text-warning',
  triangular:          'bg-danger/10 text-danger',
  triangular_v1:       'bg-danger/10 text-danger',
  cross_exchange_spot: 'bg-brand-subtle text-brand',
  cross_exchange_v1:   'bg-brand-subtle text-brand',
  cex_dex_hybrid:      'bg-warning/10 text-warning',
  cex_dex_v1:          'bg-warning/10 text-warning',
};

const EXCHANGE_SHORT: Record<string, string> = {
  binance: 'Binance', binance_futures: 'Binance Fut', bybit: 'Bybit', bybit_futures: 'Bybit Fut',
  okx: 'OKX', okx_futures: 'OKX Fut', bitget: 'Bitget', bitget_futures: 'Bitget Fut',
  upbit: 'Upbit', bithumb: 'Bithumb', coinone: 'Coinone',
  mexc: 'MEXC', gateio: 'Gate.io',
};

function shortEx(id: string) { return EXCHANGE_SHORT[id] ?? id.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()); }

type ExchangeGroup = { key: string; label: string; spotId: string; futId: string | null };

const INTL_GROUPS: ExchangeGroup[] = [
  { key: 'binance', label: 'Binance', spotId: 'binance',  futId: 'binance_futures' },
  { key: 'bybit',   label: 'Bybit',   spotId: 'bybit',    futId: 'bybit_futures' },
  { key: 'okx',     label: 'OKX',     spotId: 'okx',      futId: 'okx_futures' },
  { key: 'bitget',  label: 'Bitget',  spotId: 'bitget',   futId: 'bitget_futures' },
  { key: 'mexc',    label: 'MEXC',    spotId: 'mexc',     futId: null },
  { key: 'gateio',  label: 'Gate.io', spotId: 'gateio',   futId: null },
];
const KRW_GROUPS: ExchangeGroup[] = [
  { key: 'upbit',   label: 'Upbit',   spotId: 'upbit',   futId: null },
  { key: 'bithumb', label: 'Bithumb', spotId: 'bithumb', futId: null },
  { key: 'coinone', label: 'Coinone', spotId: 'coinone', futId: null },
];

function ExchangeGroupCard({
  group, exchangeStatus, livePositions,
}: {
  group: { key: string; label: string; spotId: string; futId: string | null };
  exchangeStatus: Record<string, ExchangeStatus> | null;
  livePositions: LivePositionsResponse | null | undefined;
}) {
  const spotStatus    = exchangeStatus?.[group.spotId];
  const futStatus     = group.futId ? exchangeStatus?.[group.futId] : null;
  const spotBal       = livePositions?.exchanges.find(e => e.exchange_id === group.spotId)?.balance_usdt ?? 0;
  const futBal        = group.futId ? (livePositions?.exchanges.find(e => e.exchange_id === group.futId)?.balance_usdt ?? 0) : null;
  const spotConn      = spotStatus?.connected ?? false;
  const futConn       = futStatus?.connected ?? false;
  // count futures as active even if not in exchangeStatus (has a live balance from engine)
  const futActive     = futConn || ((futBal ?? 0) > 0);
  const anyActive     = spotConn || futActive;
  // exchange has API keys configured if it appears in exchangeStatus
  // note: futStatus=null means no futId (not unconfigured) — must check group.futId explicitly
  const isConfigured  = spotStatus !== undefined || (group.futId !== null && futStatus !== undefined);
  const fmtBal        = (b: number) => b > 0 ? `$${b.toFixed(1)}` : '$0';

  return (
    <div className={clsx(
      'bg-bg-surface border rounded-[10px] px-2.5 py-2 transition-opacity',
      anyActive ? 'border-border' : isConfigured ? 'border-border/40 opacity-60' : 'border-border/20 opacity-40',
    )}>
      <div className="flex items-center justify-between mb-1.5">
        <span className={clsx('text-[11px] font-bold leading-none', anyActive ? 'text-text-primary' : 'text-text-tertiary')}>
          {group.label}
        </span>
        <div className="flex items-center gap-0.5">
          <span className={clsx('w-1.5 h-1.5 rounded-full',
            spotConn ? 'bg-success' : isConfigured ? 'bg-text-tertiary/30' : 'bg-text-tertiary/10',
          )} title={spotConn ? '현물 연결' : isConfigured ? '현물 대기' : '미설정'} />
          {group.futId && <span className={clsx('w-1.5 h-1.5 rounded-full',
            futActive ? 'bg-success' : isConfigured ? 'bg-text-tertiary/30' : 'bg-text-tertiary/10',
          )} title={futActive ? '선물 연결' : isConfigured ? '선물 대기' : '미설정'} />}
        </div>
      </div>
      {!isConfigured ? (
        <div className="text-[9px] font-mono text-text-tertiary/50 mt-1">미설정</div>
      ) : (
        <div className="space-y-1">
          <div>
            <div className="text-[10px] text-text-tertiary mb-0.5 uppercase tracking-wide">현물</div>
            <div className={clsx('text-[13px] font-semibold tabular-nums leading-none',
              spotBal > 0 ? 'text-text-primary' : 'text-text-tertiary/50',
            )}>
              {fmtBal(spotBal)}
            </div>
          </div>
          {group.futId && (
            <div className="pt-1 border-t border-border/20">
              <div className="text-[10px] text-text-tertiary mb-0.5 uppercase tracking-wide">선물</div>
              <div className={clsx('text-[13px] font-semibold tabular-nums leading-none',
                (futBal ?? 0) > 0 ? 'text-text-primary' : 'text-text-tertiary/50',
              )}>
                {fmtBal(futBal ?? 0)}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Exchange Pie Chart ───────────────────────────────────────────────────────

const PIE_COLORS = ['#6366f1', '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#06b6d4'];

function ExchangePieChart({ livePositions }: { livePositions: LivePositionsResponse | null | undefined }) {
  const allGroups = [...INTL_GROUPS, ...KRW_GROUPS];
  const data = allGroups
    .map(g => {
      const spotBal = livePositions?.exchanges.find(e => e.exchange_id === g.spotId)?.balance_usdt ?? 0;
      const futBal  = g.futId ? (livePositions?.exchanges.find(e => e.exchange_id === g.futId)?.balance_usdt ?? 0) : 0;
      return { name: g.label, value: spotBal + futBal };
    })
    .filter(d => d.value > 0);

  const total = data.reduce((s, d) => s + d.value, 0);

  return (
    <div className="card h-full">
      <div className="text-[9px] font-mono text-text-tertiary uppercase tracking-widest mb-1">자산 분포</div>
      {data.length === 0 ? (
        <div className="flex items-center justify-center h-16 text-[10px] font-mono text-text-tertiary">잔고 없음</div>
      ) : (
        <>
          <PieResponsive width="100%" height={110}>
            <PieChart>
              <Pie data={data} cx="50%" cy="50%" innerRadius={28} outerRadius={48} dataKey="value" paddingAngle={2}>
                {data.map((_, i) => <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
              </Pie>
              <PieTooltip
                contentStyle={{ background: '#fff', border: '1px solid #e5e7eb', borderRadius: 4, fontFamily: 'monospace', fontSize: 10 }}
                formatter={(v: number | undefined) => [`${v != null ? `$${v.toFixed(2)}` : '—'}`]}
              />
            </PieChart>
          </PieResponsive>
          <div className="space-y-0.5 mt-1">
            {data.map((d, i) => (
              <div key={d.name} className="flex items-center justify-between gap-1">
                <div className="flex items-center gap-1 min-w-0">
                  <div className="w-1.5 h-1.5 rounded-sm flex-shrink-0" style={{ background: PIE_COLORS[i % PIE_COLORS.length] }} />
                  <span className="text-[9px] font-mono text-text-secondary truncate">{d.name}</span>
                </div>
                <span className="text-[9px] font-mono tabular-nums text-text-primary flex-shrink-0">
                  {total > 0 ? `${((d.value / total) * 100).toFixed(0)}%` : '—'}
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// ─── KPI Card ─────────────────────────────────────────────────────────────────

function KpiCard({
  label, value, sub, up, loading,
}: { label: string; value: string; sub?: string; up?: boolean | null; loading?: boolean }) {
  return (
    <div className="card">
      <div className="card-header">{label}</div>
      {loading ? (
        <div className="h-8 skeleton rounded mt-1" />
      ) : (
        <div className={clsx(
          'text-2xl font-bold tabular-nums leading-tight mt-0.5 font-display',
          up === true  ? 'text-profit' :
          up === false ? 'text-loss'   : 'text-text-primary',
        )}>
          {value}
        </div>
      )}
      {sub && (
        <div className={clsx(
          'text-small mt-1',
          up === true  ? 'text-profit' :
          up === false ? 'text-loss'   : 'text-text-tertiary',
        )}>
          {sub}
        </div>
      )}
    </div>
  );
}

// ─── Risk Status Panel ────────────────────────────────────────────────────────

function RiskStatusPanel({ risk, wsKill }: { risk: RiskMetrics | null | undefined; wsKill: boolean }) {
  const killActive = wsKill || (risk?.kill_switch_active ?? false);
  const cbState    = risk?.circuit_breaker_state ?? 'CLOSED';
  const mdd        = risk?.max_drawdown_pct ?? 0;
  const dailyLoss  = (risk as Record<string, unknown>)?.daily_loss_pct as number | undefined ?? 0;

  const checks = [
    { name: '킬스위치',    value: killActive ? 'ON' : 'OFF',  ok: !killActive,          limit: null },
    { name: '서킷브레이커', value: cbState,                     ok: cbState === 'CLOSED', limit: null },
    { name: '최대 낙폭',   value: `${mdd.toFixed(1)}%`,        ok: mdd < 5,              limit: '5%' },
    { name: '일일 손실',   value: dailyLoss > 0 ? `${dailyLoss.toFixed(1)}%` : '0%', ok: dailyLoss < 3, limit: '3%' },
    { name: '순 익스포저', value: '—',                         ok: true,                 limit: null },
  ];

  return (
    <div className="card">
      <div className="card-header">리스크 상태</div>
      <div className="space-y-2.5 mt-1">
        {checks.map(c => (
          <div key={c.name} className="flex items-center gap-2">
            <div className={clsx('w-1.5 h-1.5 rounded-full shrink-0', c.ok ? 'bg-profit' : 'bg-loss')} />
            <span className="text-[10px] font-mono text-terminal-subtle flex-1">{c.name}</span>
            <span className={clsx('text-[10px] font-mono font-semibold tabular-nums', c.ok ? 'text-profit' : 'text-loss')}>
              {c.value}
            </span>
            {c.limit && <span className="text-[9px] font-mono text-terminal-subtle/60">/ {c.limit}</span>}
          </div>
        ))}
      </div>
      <div className="mt-3 pt-3 border-t border-border/50 space-y-1.5">
        <div className="text-[9px] font-mono text-text-tertiary uppercase tracking-widest mb-1.5">시스템</div>
        {[
          { label: 'CB 쿨다운', value: '300s' },
          { label: '리스크 체크', value: '11 / 11' },
          { label: 'CB 상태', value: cbState === 'CLOSED' ? '정상' : cbState, ok: cbState === 'CLOSED' },
        ].map(({ label, value, ok }) => (
          <div key={label} className="flex items-center justify-between">
            <span className="text-[10px] font-mono text-text-secondary">{label}</span>
            <span className={clsx('text-[10px] font-mono tabular-nums', ok === false ? 'text-loss' : 'text-text-primary')}>
              {value}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Strategy Performance Panel ───────────────────────────────────────────────

function StrategyPerformancePanel({
  strategies, breakdown,
}: { strategies: Strategy[]; breakdown: ShadowStrategyBreakdown[] }) {
  const rows = (() => {
    if (strategies.length > 0) {
      return strategies.map(s => {
        const b = breakdown.find(b => b.strategy_id === s.id || b.strategy_id === s.type);
        return { id: s.id, name: STRATEGY_NAMES[s.type] ?? s.type, enabled: s.enabled,
                 trades: b?.trades ?? 0, wr: b ? b.win_rate * 100 : 0, pnl: b?.pnl ?? 0 };
      });
    }
    if (breakdown.length > 0) {
      return breakdown.map(b => ({
        id: b.strategy_id, name: STRATEGY_NAMES[b.strategy_id] ?? b.strategy_id,
        enabled: b.trades > 0, trades: b.trades, wr: b.win_rate * 100, pnl: b.pnl,
      }));
    }
    return [];
  })();

  const maxPnl = Math.max(...rows.map(r => Math.abs(r.pnl)), 1);

  return (
    <div className="card">
      <div className="card-header">전략 성과</div>
      <div className={clsx('mt-1', rows.length >= 4 ? 'grid grid-cols-1 xl:grid-cols-2 gap-2' : 'space-y-2')}>
        {rows.length > 0 ? rows.map(r => (
          <div key={r.id} className={clsx(
            'flex items-center gap-3 px-3 py-2.5 rounded-md border transition-colors',
            r.enabled
              ? 'bg-terminal-bg border-terminal-border/50 hover:border-accent/30'
              : 'bg-terminal-bg border-terminal-border/20 opacity-50',
          )}>
            <div className={clsx('w-1.5 h-1.5 rounded-full shrink-0',
              r.enabled && r.trades > 0 ? 'bg-profit' : r.enabled ? 'bg-warn' : 'bg-terminal-subtle/40',
            )} />
            <div className="flex-1 min-w-0">
              <div className="text-[11px] font-mono font-semibold text-terminal-text truncate">{r.name}</div>
              <div className="flex gap-2 mt-0.5">
                <span className="text-[9px] font-mono text-terminal-subtle">{r.trades}건</span>
                <span className="text-[9px] font-mono text-terminal-subtle">승률 {r.wr.toFixed(0)}%</span>
              </div>
            </div>
            <div className="text-right shrink-0 w-20">
              <div className={clsx('text-[11px] font-mono font-semibold tabular-nums',
                r.pnl > 0 ? 'text-profit' : r.pnl < 0 ? 'text-loss' : 'text-terminal-subtle',
              )}>
                {r.pnl > 0 ? '+' : ''}{r.pnl === 0 ? '$0.00' : `$${Math.abs(r.pnl).toFixed(2)}`}
              </div>
              <div className="w-full h-1 bg-terminal-muted rounded-full mt-1 overflow-hidden">
                <div
                  className={clsx('h-full rounded-full transition-all',
                    r.pnl > 0 ? 'bg-accent' : r.pnl < 0 ? 'bg-loss/60' : 'bg-terminal-subtle/30',
                  )}
                  style={{ width: `${Math.min((Math.abs(r.pnl) / maxPnl) * 100, 100)}%` }}
                />
              </div>
            </div>
          </div>
        )) : Array.from({ length: 7 }).map((_, i) => (
          <div key={i} className="h-12 rounded-md skeleton" />
        ))}
      </div>
    </div>
  );
}

// ─── Spread Heatmap Panel ─────────────────────────────────────────────────────

const HEATMAP_EXCHANGE_FULL: Record<string, string> = {
  BN: 'Binance', BNF: 'Binance Futures', UP: 'Upbit',
  BH: 'Bithumb', CO: 'Coinone', BG: 'Bitget',
};

function SpreadHeatmapPanel({ spreadMatrix }: { spreadMatrix: Record<string, Record<string, number>> | null }) {
  const exchanges = ['BN', 'BNF', 'UP', 'BH', 'CO', 'BG'];
  const symbols   = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE'];

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <div className="card-header mb-0">스프레드 히트맵 (bps)</div>
        <span className="text-[9px] font-mono text-terminal-subtle/60">실시간</span>
      </div>
      <div className="overflow-x-auto">
        <div style={{ display: 'grid', gridTemplateColumns: `28px repeat(${symbols.length}, 1fr)`, gap: 2 }}>
          <div />
          {symbols.map(s => (
            <div key={s} className="text-[8px] font-mono text-terminal-subtle text-center pb-1">{s}</div>
          ))}
          {exchanges.map(ex => (
            <>
              <div key={`l-${ex}`} className="text-[8px] font-mono text-terminal-subtle flex items-center leading-none py-0.5" title={HEATMAP_EXCHANGE_FULL[ex]}>{ex}</div>
              {symbols.map(s => {
                const val = spreadMatrix?.[ex]?.[s] ?? null;
                const isPos = val !== null ? val > 0 : null;
                return (
                  <div key={`${ex}-${s}`}
                    className="rounded flex items-center justify-center h-[22px]"
                    style={{
                      background: val === null ? 'rgba(0,0,0,0.02)' :
                        isPos ? `rgba(20,158,97,${Math.min(Math.abs(val) / 30 * 0.4, 0.4)})` :
                                `rgba(229,72,77,${Math.min(Math.abs(val) / 30 * 0.3, 0.3)})`,
                    }}
                  >
                    <span className={clsx('text-[8px] font-mono',
                      val === null ? 'text-terminal-subtle/30' : isPos ? 'text-profit' : 'text-loss',
                    )}>
                      {val !== null ? `${val > 0 ? '+' : ''}${val.toFixed(1)}` : '—'}
                    </span>
                  </div>
                );
              })}
            </>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─── Active Positions Strip ───────────────────────────────────────────────────

function ActivePositionsPanel({
  positions,
}: {
  positions: { strategy_id: string; exchange_id: string; symbol: string; side: string; pnl: number }[];
}) {
  if (positions.length === 0) {
    return (
      <div className="card">
        <div className="card-header">활성 포지션</div>
        <div className="flex items-center justify-center py-6 text-[11px] font-mono text-terminal-subtle/60">
          열린 포지션 없음
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="card-header">활성 포지션 ({positions.length})</div>
      <div className="space-y-1 mt-1">
        {positions.map((p, i) => (
          <div key={i} className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-terminal-muted/20 transition-colors">
            <div className={clsx(
              'text-[9px] font-mono font-bold px-1.5 py-0.5 rounded shrink-0',
              p.side === 'LONG' || p.side === 'BUY'
                ? 'bg-profit/10 text-profit'
                : 'bg-loss/10 text-loss',
            )}>
              {p.side}
            </div>
            <span className="text-[10px] font-mono text-terminal-subtle shrink-0 w-[28px]">
              {shortEx(p.exchange_id)}
            </span>
            <span className="text-[11px] font-mono text-terminal-text flex-1">{p.symbol}</span>
            <span className="text-[9px] font-mono text-terminal-subtle/60 shrink-0">
              {STRATEGY_NAMES[p.strategy_id] ?? p.strategy_id}
            </span>
            <span className={clsx('text-[11px] font-mono font-semibold tabular-nums shrink-0 w-16 text-right',
              p.pnl > 0 ? 'text-profit' : p.pnl < 0 ? 'text-loss' : 'text-terminal-subtle',
            )}>
              {p.pnl > 0 ? '+' : ''}{p.pnl === 0 ? '$0.00' : `$${p.pnl.toFixed(2)}`}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Recent Fills Panel ───────────────────────────────────────────────────────

function RecentFillsPanel({ trades, loading }: { trades: Trade[]; loading: boolean }) {
  const recent = trades.slice(0, 8);

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <div className="card-header mb-0">최근 체결</div>
        <a href="/trades" className="text-[10px] font-mono text-terminal-subtle hover:text-accent transition-colors">
          전체 보기 →
        </a>
      </div>

      {loading ? (
        <div className="space-y-1">
          {Array.from({ length: 5 }).map((_, i) => <div key={i} className="h-8 skeleton rounded" />)}
        </div>
      ) : recent.length === 0 ? (
        <div className="flex items-center justify-center py-6 text-[11px] font-mono text-terminal-subtle/60">
          체결 내역 없음 — 전략 신호 대기 중
        </div>
      ) : (
        <div className="space-y-0.5">
          {recent.map(t => {
            const isProfit = t.pnl > 0;
            const ts = new Date(t.timestamp).toLocaleTimeString('en-GB', {
              hour: '2-digit', minute: '2-digit', second: '2-digit',
            });
            const abbr  = STRATEGY_ABBR[t.strategy_id] ?? t.strategy_id.slice(0, 2).toUpperCase();
            const color = STRATEGY_COLORS[t.strategy_id] ?? 'bg-accent/20 text-accent';
            return (
              <div key={t.id} className="flex items-center gap-2 px-2 py-1.5 hover:bg-terminal-muted/30 rounded-lg transition-colors">
                {/* Time */}
                <span className="text-[10px] font-mono text-terminal-subtle/70 tabular-nums w-16 shrink-0">{ts}</span>
                {/* Strategy badge */}
                <span className={clsx('text-[9px] font-mono font-bold px-1.5 py-0.5 rounded shrink-0 min-w-[28px] text-center', color)}>
                  {abbr}
                </span>
                {/* Symbol */}
                <span className="text-[11px] font-mono text-terminal-text font-semibold w-24 shrink-0">{t.symbol}</span>
                {/* Side — only show recognized values */}
                {(t.side === 'LONG' || t.side === 'SHORT' || t.side === 'BUY' || t.side === 'SELL') && (
                  <span className={clsx('text-[10px] font-mono font-semibold shrink-0 w-12',
                    t.side === 'LONG' || t.side === 'BUY' ? 'text-profit' : 'text-loss',
                  )}>
                    {t.side === 'LONG' ? '매수' : t.side === 'SHORT' ? '매도' : t.side === 'BUY' ? '매수' : '매도'}
                  </span>
                )}
                {/* Route */}
                <span className="text-[10px] font-mono text-terminal-subtle flex-1 truncate">
                  {shortEx(t.buy_exchange)} → {shortEx(t.sell_exchange)}
                </span>
                {/* PnL */}
                <span className={clsx(
                  'text-[11px] font-mono font-semibold tabular-nums shrink-0 w-16 text-right',
                  isProfit ? 'text-profit' : t.pnl < 0 ? 'text-loss' : 'text-terminal-subtle',
                )}>
                  {t.pnl > 0 ? '+' : ''}${Math.abs(t.pnl).toFixed(2)}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ─── Backtest View ────────────────────────────────────────────────────────────

interface BtCase {
  case_id: string; exchange_ids: string[]; strategy_ids: string[];
  seed_capital: number; period: string; snapshots_replayed: number;
  signals_generated: number; trades: number; pnl: number; sharpe: number;
  mdd_pct: number; win_rate: number; profit_factor: number; error: string;
  ac_pass?: boolean;
  by_strategy: Record<string, { pnl: number; trades: number; wins: number; win_rate: number }>;
}

const BT_ABBR: Record<string, string> = {
  funding_rate_v1: 'FR', triangular_v1: 'TRI', statistical_arb_v1: 'SA',
  spot_futures_v1: 'SF', cross_exchange_v1: 'CE', futures_futures_v1: 'FF', cex_dex_v1: 'CD',
};
const btAbbr = (id: string) => BT_ABBR[id] ?? id.split('_')[0].toUpperCase().slice(0, 3);
const btEx = (id: string) => id.replace('_futures','F').replace('binance','BN').replace('bybit','BB')
  .replace('okx','OKX').replace('bitget','BG').replace('upbit','UP').replace('bithumb','BH').replace('coinone','CO');

async function _fetchBtStaticFallback(): Promise<BtCase[]> {
  const ids = Array.from({ length: 18 }, (_, i) => String(i + 1).padStart(2, '0'));
  const results = await Promise.all(ids.map(async id => {
    try {
      const res = await fetch(`/backtest/backtest-summary-K-BT-${id}.json`);
      if (!res.ok) return null;
      return res.json() as Promise<BtCase>;
    } catch { return null; }
  }));
  return results.filter((r): r is BtCase => r !== null);
}

function BacktestView() {
  const { data: casesData, isLoading: loading } = useApi<BtCase[]>(
    '/api/backtest/batch_results',
    _fetchBtStaticFallback,
    { refreshInterval: 0 },
  );
  const cases = casesData ?? [];
  const [expanded, setExpanded] = useState<string | null>(null);

  const valid = cases.filter(c => !c.error);
  const totalPnl = valid.reduce((s, c) => s + c.pnl, 0);
  const avgSharpe = valid.length ? valid.reduce((s, c) => s + c.sharpe, 0) / valid.length : 0;
  const passCount = valid.filter(c => c.ac_pass ?? (c.sharpe >= 1.0 && c.pnl > 0)).length;

  return (
    <div className="space-y-4">
      {/* Summary KPI */}
      {!loading && valid.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { label: '총 케이스', value: `${cases.length}개`, sub: `${passCount} AC_PASS`, up: null },
            { label: '누적 PnL', value: `+$${totalPnl.toLocaleString('en-US', { maximumFractionDigits: 0 })}`, sub: '전 케이스 합산', up: totalPnl > 0 as boolean | null },
            { label: '평균 Sharpe', value: avgSharpe.toFixed(2), sub: '≥1.0 = 우수', up: avgSharpe >= 1 as boolean | null },
            { label: '합격률', value: `${passCount}/${valid.length}`, sub: 'Sharpe≥1 & PnL>0', up: passCount === valid.length ? true : null },
          ].map(k => (
            <div key={k.label} className="card">
              <div className="card-header">{k.label}</div>
              <div className={clsx('text-xl font-mono font-bold tabular-nums mt-1',
                k.up === true ? 'text-profit' : k.up === false ? 'text-loss' : 'text-terminal-text',
              )}>{k.value}</div>
              <div className="text-[10px] font-mono text-terminal-subtle mt-0.5">{k.sub}</div>
            </div>
          ))}
        </div>
      )}

      {/* Table */}
      <div className="card p-0 overflow-hidden">
        <div className="px-4 py-3 border-b border-terminal-border flex items-center justify-between">
          <span className="card-header mb-0">K-BT 배치 결과 — Phase K 검증</span>
          <span className="text-[9px] font-mono text-terminal-subtle">행 클릭 시 전략별 상세</span>
        </div>
        {loading && <div className="p-8 flex justify-center"><div className="h-4 w-32 skeleton rounded" /></div>}
        {!loading && cases.length === 0 && (
          <div className="p-8 text-center text-[11px] font-mono text-terminal-subtle">백테스트 결과 없음</div>
        )}
        {!loading && cases.length > 0 && (
          <div className="overflow-x-auto">
            <div className="grid grid-cols-[80px_140px_1fr_80px_80px_72px_72px] gap-0 px-4 py-2 border-b border-terminal-border bg-terminal-muted/20 text-[9px] font-mono text-terminal-subtle uppercase tracking-wider">
              <span>ID</span><span>기간</span><span>거래소 / 전략</span>
              <span className="text-right">PnL</span><span className="text-right">Sharpe</span>
              <span className="text-right">MDD</span><span className="text-center">결과</span>
            </div>
            {cases.map(c => {
              const pass = !c.error && (c.ac_pass ?? (c.sharpe >= 1.0 && c.pnl > 0));
              const isExp = expanded === c.case_id;
              return (
                <div key={c.case_id}>
                  <div className={clsx(
                    'grid grid-cols-[80px_140px_1fr_80px_80px_72px_72px] gap-0 px-4 py-2.5 border-b border-terminal-border/50 cursor-pointer transition-colors',
                    isExp ? 'bg-terminal-muted/30' : 'hover:bg-terminal-muted/20',
                    c.error ? 'opacity-50' : '',
                  )} onClick={() => setExpanded(isExp ? null : c.case_id)}>
                    <span className="text-[11px] font-mono font-semibold text-accent">{c.case_id}</span>
                    <span className="text-[10px] font-mono text-terminal-subtle">{c.period}</span>
                    <div className="flex flex-wrap gap-1 items-center">
                      {c.exchange_ids.map(e => (
                        <span key={e} className="text-[8px] font-mono px-1 py-0.5 bg-terminal-bg border border-terminal-border rounded text-terminal-subtle">{btEx(e)}</span>
                      ))}
                      {c.strategy_ids.map(s => (
                        <span key={s} className="text-[8px] font-mono px-1 py-0.5 bg-accent/10 border border-accent/20 rounded text-accent">{btAbbr(s)}</span>
                      ))}
                    </div>
                    <span className={clsx('text-[11px] font-mono font-semibold tabular-nums text-right', c.pnl > 0 ? 'text-profit' : c.pnl < 0 ? 'text-loss' : 'text-terminal-subtle')}>
                      {c.error ? '—' : `${c.pnl >= 0 ? '+' : ''}$${Math.abs(c.pnl).toLocaleString('en-US', { maximumFractionDigits: 0 })}`}
                    </span>
                    <span className={clsx('text-[11px] font-mono tabular-nums text-right', c.sharpe >= 2 ? 'text-profit' : c.sharpe >= 1 ? 'text-warn' : 'text-loss')}>
                      {c.error ? '—' : c.sharpe.toFixed(2)}
                    </span>
                    <span className={clsx('text-[11px] font-mono tabular-nums text-right', c.mdd_pct > 5 ? 'text-loss' : c.mdd_pct > 2 ? 'text-warn' : 'text-profit')}>
                      {c.error ? '—' : `${c.mdd_pct.toFixed(1)}%`}
                    </span>
                    <div className="flex justify-center items-center">
                      {c.error ? <span className="text-[9px] font-mono text-loss">ERR</span> : (
                        <span className={clsx('text-[9px] font-mono font-bold px-1.5 py-0.5 rounded',
                          pass ? 'bg-profit/20 text-profit' : 'bg-loss/20 text-loss',
                        )}>{pass ? 'PASS' : 'FAIL'}</span>
                      )}
                    </div>
                  </div>
                  {isExp && !c.error && (
                    <div className="px-4 py-3 bg-terminal-muted/10 border-b border-terminal-border space-y-2">
                      <div className="text-[9px] font-mono text-terminal-subtle uppercase tracking-wider mb-2">전략별 상세 — {c.case_id}</div>
                      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                        {Object.entries(c.by_strategy).map(([sid, s]) => (
                          <div key={sid} className="bg-terminal-bg border border-terminal-border rounded p-2.5">
                            <div className="flex items-center justify-between mb-1.5">
                              <span className="text-[10px] font-mono font-semibold text-terminal-text">{sid}</span>
                              <span className={clsx('text-[10px] font-mono font-semibold tabular-nums', s.pnl > 0 ? 'text-profit' : s.pnl < 0 ? 'text-loss' : 'text-terminal-subtle')}>
                                {s.pnl >= 0 ? '+' : ''}${s.pnl.toFixed(2)}
                              </span>
                            </div>
                            <div className="flex gap-3 text-[9px] font-mono text-terminal-subtle">
                              <span>{s.trades} trades</span>
                              <span>WR {(s.win_rate * 100).toFixed(0)}%</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function OverviewPage() {
  const { connected, data } = useEngineWs();

  const { data: portfolio } = useApi<PortfolioSummaryResponse>(
    '/portfolio-summary', getPortfolioSummary, { refreshInterval: 10_000 },
  );
  const { data: riskMetrics } = useApi<RiskMetrics>(
    '/risk-metrics', getRiskMetrics, { refreshInterval: 5_000 },
  );
  const { data: strategies } = useApi<Strategy[]>(
    '/strategies', getStrategies, { refreshInterval: 15_000 },
  );
  const { data: recentTrades, isLoading: tradesLoading } = useApi<Trade[]>(
    '/trades', () => getTrades({ limit: 8 }), { refreshInterval: 8_000 },
  );
  const { data: exchangeStatus } = useApi<Record<string, ExchangeStatus>>(
    '/exchanges', getExchangeStatus, { refreshInterval: 10_000 },
  );
  const { data: livePositions } = useApi<LivePositionsResponse>(
    '/positions-live', getLivePositions, { refreshInterval: 10_000 },
  );

  // ── KPI values ───────────────────────────────────────────────────────────
  // Live exchange balance takes priority over in-memory portfolio summary
  const liveBalance  = livePositions?.total_balance_usdt ?? 0;
  const totalAssets  = liveBalance > 0 ? liveBalance : (portfolio?.total_balance_usdt ?? 0);
  const shadow       = data?.shadow_stats ?? null;
  // Live positions count: WS engine data OR direct exchange query
  const livePosList  = livePositions?.exchanges.flatMap(e => e.positions) ?? [];
  const activePos    = livePosList.length > 0 ? livePosList.length : (data?.position_count ?? 0);
  const winRate     = shadow?.win_rate ?? 0;
  const totalTrades = shadow?.trades_executed ?? 0;

  // 누적 PnL = shadow session total (가장 신뢰할 수 있는 소스)
  const cumulPnl    = shadow?.total_pnl ?? portfolio?.total_pnl ?? data?.pnl?.total ?? 0;
  // 일일 PnL: portfolio.daily_pnl 우선, 없으면 세션 PnL fallback
  const dailyPnl    = portfolio?.daily_pnl ?? shadow?.total_pnl ?? 0;

  // ── Derived ──────────────────────────────────────────────────────────────
  const breakdown    = shadow?.by_strategy ?? [];
  const strategyList = strategies ?? data?.strategies ?? [];
  const positions    = data?.positions ?? [];
  const spreadMatrix = (data as unknown as Record<string, unknown>)?.spread_matrix as
    Record<string, Record<string, number>> | null ?? null;
  const isBacktest   = (data?.mode ?? 'paper').toLowerCase() === 'backtest';

  return (
    <div className="max-w-screen-xl mx-auto space-y-4 px-4 md:px-6 py-4 pb-24 md:pb-6">

      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-mono font-semibold text-terminal-text">
            {isBacktest ? '백테스트 결과' : '대시보드'}
          </h2>
          <span className={clsx('text-[9px] font-mono', connected ? 'text-profit' : 'text-loss')}>
            {connected ? '● LIVE' : '● OFFLINE'}
          </span>
        </div>
        <ModeSwitch currentMode={data?.mode ?? 'paper'} />
      </div>

      {/* ── Mode-conditional content ────────────────────────────────────── */}
      {isBacktest ? <BacktestView /> : (<>

      {/* ── Row 1: 4 KPI Cards ──────────────────────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <KpiCard
          label="총 자산"
          value={totalAssets > 0
            ? `$${totalAssets.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
            : '$0.00'}
          sub={totalAssets > 0 ? undefined : '포트폴리오 연결 중'}
          up={null}
          loading={!portfolio && !data}
        />
        <KpiCard
          label="누적 PnL"
          value={`${cumulPnl >= 0 ? '+' : ''}$${Math.abs(cumulPnl).toFixed(2)}`}
          sub={`WR ${(winRate * 100).toFixed(0)}%`}
          up={cumulPnl > 0 ? true : cumulPnl < 0 ? false : null}
          loading={!data && !portfolio}
        />
        <KpiCard
          label="일일 PnL"
          value={`${dailyPnl >= 0 ? '+' : ''}$${Math.abs(dailyPnl).toFixed(2)}`}
          sub={`${totalTrades}건 체결`}
          up={dailyPnl > 0 ? true : dailyPnl < 0 ? false : null}
          loading={!data && !portfolio}
        />
        <KpiCard
          label="활성 포지션"
          value={String(activePos)}
          sub={activePos > 0 ? `${activePos}개 운용 중` : '대기 중'}
          up={null}
          loading={!data}
        />
      </div>

      {/* ── Row 1.5: Exchange Balance (grouped: 해외/국내) + Pie ──────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_168px] gap-4">
        <div className="space-y-2">
          <div>
            <div className="text-small font-medium text-text-tertiary uppercase tracking-widest mb-2">해외 거래소</div>
            <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
              {INTL_GROUPS.map(g => (
                <ExchangeGroupCard key={g.key} group={g} exchangeStatus={exchangeStatus ?? null} livePositions={livePositions} />
              ))}
            </div>
          </div>
          <div>
            <div className="text-small font-medium text-text-tertiary uppercase tracking-widest mb-2">국내 거래소</div>
            <div className="grid grid-cols-3 gap-2">
              {KRW_GROUPS.map(g => (
                <ExchangeGroupCard key={g.key} group={g} exchangeStatus={exchangeStatus ?? null} livePositions={livePositions} />
              ))}
            </div>
          </div>
        </div>
        <div className="hidden lg:block self-start">
          <ExchangePieChart livePositions={livePositions} />
        </div>
      </div>

      {/* ── Row 2: PnL Curve (2/3) | Risk + Strategy stacked (1/3) ──────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <PnLChart wsPnl={
            shadow?.total_pnl
              ? { total: shadow.total_pnl, realized: shadow.total_pnl, unrealized: 0 }
              : (data?.pnl ?? null)
          } />
        </div>
        <div className="space-y-4">
          <RiskStatusPanel risk={riskMetrics} wsKill={data?.kill_switch ?? false} />
          <StrategyPerformancePanel
            strategies={strategyList as Strategy[]}
            breakdown={breakdown}
          />
          {spreadMatrix && <SpreadHeatmapPanel spreadMatrix={spreadMatrix} />}
        </div>
      </div>

      {/* ── Row 4: Recent Fills (2/3) + Active Positions (1/3) ──────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <RecentFillsPanel trades={recentTrades ?? []} loading={tradesLoading && !recentTrades} />
        </div>
        {/* Live exchange positions > WebSocket engine positions */}
        {livePosList.length > 0 ? (
          <div className="card">
            <div className="card-header">실시간 포지션 ({livePosList.length})</div>
            <div className="space-y-1 mt-1">
              {livePositions!.hedge_pairs.map((pair) => (
                <div key={pair.symbol} className="px-2 py-2 rounded hover:bg-terminal-muted/20 transition-colors">
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] font-mono font-bold text-terminal-text">{pair.symbol}</span>
                    {pair.is_hedged && (
                      <span className="text-[8px] font-mono bg-profit/20 text-profit px-1 py-0.5">헤지</span>
                    )}
                    <span className={clsx('text-[10px] font-mono font-semibold tabular-nums ml-auto',
                      pair.net_pnl > 0 ? 'text-profit' : pair.net_pnl < 0 ? 'text-loss' : 'text-terminal-subtle',
                    )}>
                      {pair.net_pnl >= 0 ? '+' : ''}${pair.net_pnl.toFixed(4)}
                    </span>
                  </div>
                  <div className="flex gap-3 mt-0.5">
                    {pair.binance_futures && (
                      <span className={clsx('text-[9px] font-mono', pair.binance_futures.side === 'long' ? 'text-profit' : 'text-loss')}>
                        BNF {pair.binance_futures.side.toUpperCase()} {Math.abs(pair.binance_futures.size).toFixed(4)}
                      </span>
                    )}
                    {pair.bitget_futures && (
                      <span className={clsx('text-[9px] font-mono', pair.bitget_futures.side === 'long' ? 'text-profit' : 'text-loss')}>
                        BGF {pair.bitget_futures.side.toUpperCase()} {Math.abs(pair.bitget_futures.size).toFixed(4)}
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
            <div className="mt-2 pt-2 border-t border-terminal-border/30 flex items-center justify-between">
              <span className="text-[9px] font-mono text-terminal-subtle">합산 미실현</span>
              <span className={clsx('text-[10px] font-mono font-semibold tabular-nums',
                (livePositions?.total_unrealized_pnl ?? 0) >= 0 ? 'text-profit' : 'text-loss',
              )}>
                {(livePositions?.total_unrealized_pnl ?? 0) >= 0 ? '+' : ''}${(livePositions?.total_unrealized_pnl ?? 0).toFixed(4)}
              </span>
            </div>
          </div>
        ) : (
          <ActivePositionsPanel positions={positions} />
        )}
      </div>

      {/* Row 5: Exchange Connection Status — removed (duplicates top exchange cards) */}

      {/* ── Row 6: Event Feed ────────────────────────────────────────────── */}
      <EventFeed />

      </>)}

    </div>
  );
}
