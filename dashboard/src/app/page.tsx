'use client';

import clsx from 'clsx';
import { PnLChart }    from '@/components/PnLChart';
import { EventFeed }   from '@/components/EventFeed';
import { ModeSwitch }  from '@/components/ModeSwitch';
import { useEngineWs } from '@/hooks/useEngineWs';
import { useApi }      from '@/hooks/useApi';
import {
  getPortfolioSummary, getRiskMetrics, getStrategies, getTrades, getExchangeStatus,
} from '@/lib/api';
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
  funding_rate_arb:  'Funding Rate',
  spot_futures_basis:'Spot-Futures',
  futures_futures:   'Futures-Futures',
  statistical_arb:   'Statistical Arb',
  triangular:        'Triangular',
  cross_exchange_spot:'Cross Exchange',
  cex_dex_hybrid:    'CEX-DEX',
};

const STRATEGY_ABBR: Record<string, string> = {
  funding_rate_arb:  'FR',
  spot_futures_basis:'SF',
  futures_futures:   'FF',
  statistical_arb:   'SA',
  triangular:        'TRI',
  cross_exchange_spot:'CE',
  cex_dex_hybrid:    'CD',
};

const STRATEGY_COLORS: Record<string, string> = {
  funding_rate_arb:  'bg-blue-500/20 text-blue-300',
  spot_futures_basis:'bg-teal-500/20 text-teal-300',
  futures_futures:   'bg-purple-500/20 text-purple-300',
  statistical_arb:   'bg-orange-500/20 text-orange-300',
  triangular:        'bg-pink-500/20 text-pink-300',
  cross_exchange_spot:'bg-accent/20 text-accent',
  cex_dex_hybrid:    'bg-yellow-500/20 text-yellow-300',
};

const EXCHANGE_SHORT: Record<string, string> = {
  binance: 'Binance', binance_futures: 'Binance Fut', bybit: 'Bybit', bybit_futures: 'Bybit Fut',
  okx: 'OKX', okx_futures: 'OKX Fut', bitget: 'Bitget', bitget_futures: 'Bitget Fut',
  upbit: 'Upbit', bithumb: 'Bithumb', coinone: 'Coinone',
  mexc: 'MEXC', gateio: 'Gate.io',
};

function shortEx(id: string) { return EXCHANGE_SHORT[id] ?? id.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()); }

// ─── Exchange Connection Status Bar ──────────────────────────────────────────

function ExchangeStatusBar({ exchanges }: { exchanges: Record<string, ExchangeStatus> | null }) {
  if (!exchanges) return null;

  const items = Object.entries(exchanges).filter(([, s]) => s.connected);
  const total = Object.keys(exchanges).length;

  const latencyColor = (ms: number) =>
    ms < 100 ? 'text-profit' : ms < 500 ? 'text-warn' : 'text-loss';

  const fmtBalance = (ex: ExchangeStatus) => {
    const b = ex.balance ?? {};
    const usdt = b['USDT'] ?? b['usdt'] ?? 0;
    const krw  = b['KRW']  ?? b['krw']  ?? 0;
    if (krw > 0) return `₩${(krw/1000).toFixed(0)}K`;
    if (usdt > 0) return `$${usdt.toFixed(0)}`;
    return null;
  };

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <div className="card-header mb-0">거래소 연결 상태</div>
        <span className={clsx('text-[10px] font-mono font-semibold',
          items.length > 0 ? 'text-profit' : 'text-loss',
        )}>
          {items.length}/{total} 연결됨
        </span>
      </div>
      <div className="flex flex-wrap gap-2">
        {Object.entries(exchanges).map(([id, s]) => {
          const bal = fmtBalance(s);
          return (
            <div key={id} className={clsx(
              'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-[10px] font-mono transition-colors',
              s.connected
                ? 'border-profit/20 bg-profit/5'
                : 'border-terminal-border/30 bg-terminal-muted/30 opacity-50',
            )}>
              <div className={clsx('w-1.5 h-1.5 rounded-full shrink-0',
                s.connected ? 'bg-profit' : 'bg-terminal-subtle/40',
              )} />
              <span className="text-terminal-text font-semibold">{shortEx(id)}</span>
              {s.connected && (
                <>
                  <span className={clsx('tabular-nums', latencyColor(s.latency_ms))}>
                    {s.latency_ms}ms
                  </span>
                  {bal && <span className="text-terminal-subtle">{bal}</span>}
                </>
              )}
            </div>
          );
        })}
      </div>
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
          'text-2xl font-mono font-bold tabular-nums leading-tight mt-1',
          up === true  ? 'text-profit' :
          up === false ? 'text-loss'   : 'text-terminal-text',
        )}>
          {value}
        </div>
      )}
      {sub && (
        <div className={clsx(
          'text-[10px] font-mono mt-1',
          up === true  ? 'text-profit' :
          up === false ? 'text-loss'   : 'text-terminal-subtle',
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
    { name: 'Kill Switch',     value: killActive ? 'ON' : 'OFF',  ok: !killActive,          limit: null },
    { name: 'Circuit Breaker', value: cbState,                     ok: cbState === 'CLOSED', limit: null },
    { name: 'Max Drawdown',    value: `${mdd.toFixed(1)}%`,        ok: mdd < 5,              limit: '5%' },
    { name: 'Daily Loss',      value: dailyLoss > 0 ? `${dailyLoss.toFixed(1)}%` : '0%', ok: dailyLoss < 3, limit: '3%' },
    { name: 'Net Exposure',    value: '—',                         ok: true,                 limit: null },
  ];

  return (
    <div className="card h-full">
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
      <div className="space-y-2 mt-1">
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
                <span className="text-[9px] font-mono text-terminal-subtle">{r.trades} trades</span>
                <span className="text-[9px] font-mono text-terminal-subtle">WR {r.wr.toFixed(0)}%</span>
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
              <div key={`l-${ex}`} className="text-[8px] font-mono text-terminal-subtle flex items-center leading-none py-0.5">{ex}</div>
              {symbols.map(s => {
                const val = spreadMatrix?.[ex]?.[s] ?? null;
                const isPos = val !== null ? val > 0 : null;
                return (
                  <div key={`${ex}-${s}`}
                    className="rounded flex items-center justify-center h-[22px]"
                    style={{
                      background: val === null ? 'rgba(0,0,0,0.03)' :
                        isPos ? `rgba(0,200,150,${Math.min(Math.abs(val) / 30 * 0.4, 0.4)})` :
                                `rgba(255,71,87,${Math.min(Math.abs(val) / 30 * 0.3, 0.3)})`,
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
                {/* Side */}
                <span className={clsx('text-[10px] font-mono font-semibold shrink-0 w-12',
                  t.side === 'LONG' || t.side === 'BUY' ? 'text-profit' : 'text-loss',
                )}>
                  {t.side}
                </span>
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

  // ── KPI values ───────────────────────────────────────────────────────────
  const totalAssets = portfolio?.total_balance_usdt ?? 0;
  const shadow      = data?.shadow_stats ?? null;
  const activePos   = data?.position_count ?? 0;
  const winRate     = shadow?.win_rate ?? 0;
  const totalTrades = shadow?.trades_executed ?? 0;

  // 누적 PnL = shadow session total (가장 신뢰할 수 있는 소스)
  const cumulPnl    = shadow?.total_pnl ?? portfolio?.total_pnl ?? data?.pnl?.total ?? 0;
  // 일일 PnL = WS real-time pnl (오늘치만), 없으면 portfolio daily_pnl
  const dailyPnl    = (data?.pnl?.total != null && data?.pnl?.total !== 0)
    ? data.pnl.total
    : (portfolio?.daily_pnl ?? 0);

  const cumulPos = cumulPnl >= 0;
  const dailyPos = dailyPnl >= 0;

  // ── Derived ──────────────────────────────────────────────────────────────
  const breakdown    = shadow?.by_strategy ?? [];
  const strategyList = strategies ?? data?.strategies ?? [];
  const positions    = data?.positions ?? [];
  const spreadMatrix = (data as unknown as Record<string, unknown>)?.spread_matrix as
    Record<string, Record<string, number>> | null ?? null;

  return (
    <div className="space-y-4">

      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-mono font-semibold text-terminal-text">대시보드</h2>
          <span className={clsx('text-[9px] font-mono', connected ? 'text-profit' : 'text-loss')}>
            {connected ? '● LIVE' : '● OFFLINE'}
          </span>
        </div>
        <ModeSwitch currentMode={data?.mode ?? 'paper'} />
      </div>

      {/* ── Row 1: 4 KPI Cards ──────────────────────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
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

      {/* ── Row 2: PnL Curve (2/3) + Risk Status (1/3) ──────────────────── */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="xl:col-span-2">
          <PnLChart wsPnl={data?.pnl ?? null} />
        </div>
        <RiskStatusPanel risk={riskMetrics} wsKill={data?.kill_switch ?? false} />
      </div>

      {/* ── Row 3: Strategy Performance (1/2) + Spread Heatmap (1/2) ────── */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <StrategyPerformancePanel
          strategies={strategyList as Strategy[]}
          breakdown={breakdown}
        />
        <SpreadHeatmapPanel spreadMatrix={spreadMatrix} />
      </div>

      {/* ── Row 4: Recent Fills (2/3) + Active Positions (1/3) ──────────── */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="xl:col-span-2">
          <RecentFillsPanel trades={recentTrades ?? []} loading={tradesLoading && !recentTrades} />
        </div>
        <ActivePositionsPanel positions={positions} />
      </div>

      {/* ── Row 5: Exchange Connection Status ───────────────────────────── */}
      <ExchangeStatusBar exchanges={exchangeStatus ?? null} />

      {/* ── Row 6: Event Feed ────────────────────────────────────────────── */}
      <EventFeed />

    </div>
  );
}
