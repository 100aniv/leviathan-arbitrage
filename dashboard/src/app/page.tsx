'use client';

import clsx from 'clsx';
import { PnLChart }    from '@/components/PnLChart';
import { EventFeed }   from '@/components/EventFeed';
import { ModeSwitch }  from '@/components/ModeSwitch';
import { useEngineWs } from '@/hooks/useEngineWs';
import { useApi }      from '@/hooks/useApi';
import { getPortfolioSummary, getRiskMetrics, getStrategies } from '@/lib/api';
import type {
  PortfolioSummaryResponse,
  RiskMetrics,
  Strategy,
  ShadowStrategyBreakdown,
} from '@/types';

// ─── Display name map ─────────────────────────────────────────────────────────

const STRATEGY_NAMES: Record<string, string> = {
  funding_rate:     'Funding Rate',
  spot_futures:     'Spot-Futures',
  futures_futures:  'Futures-Futures',
  statistical_arb:  'Statistical Arb',
  triangular:       'Triangular',
  cross_exchange:   'Cross Exchange',
  cex_dex:          'CEX-DEX',
};

// ─── KPI Card ─────────────────────────────────────────────────────────────────

function KpiCard({
  label, value, sub, up, loading,
}: {
  label: string;
  value: string;
  sub?: string;
  up?: boolean | null;
  loading?: boolean;
}) {
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

function RiskStatusPanel({
  risk,
  wsKill,
}: {
  risk: RiskMetrics | null | undefined;
  wsKill: boolean;
}) {
  const killActive = wsKill || (risk?.kill_switch_active ?? false);
  const cbState    = risk?.circuit_breaker_state ?? 'CLOSED';
  const mdd        = risk?.max_drawdown_pct ?? 0;
  const dailyLoss  = (risk as Record<string, unknown>)?.daily_loss_pct as number | undefined ?? 0;

  const checks = [
    { name: 'Kill Switch',     value: killActive ? 'ON'     : 'OFF',    ok: !killActive,      limit: null  },
    { name: 'Circuit Breaker', value: cbState,                          ok: cbState === 'CLOSED', limit: null },
    { name: 'Max Drawdown',    value: `${mdd.toFixed(1)}%`,             ok: mdd < 5,           limit: '5%'  },
    { name: 'Daily Loss',      value: dailyLoss > 0 ? `${dailyLoss.toFixed(1)}%` : '0%', ok: dailyLoss < 3, limit: '3%' },
    { name: 'Net Exposure',    value: '—',                              ok: true,              limit: null  },
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
            {c.limit && (
              <span className="text-[9px] font-mono text-terminal-subtle/60">/ {c.limit}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Strategy Performance Panel ───────────────────────────────────────────────

function StrategyPerformancePanel({
  strategies,
  breakdown,
}: {
  strategies: Strategy[];
  breakdown: ShadowStrategyBreakdown[];
}) {
  const rows = (() => {
    if (strategies.length > 0) {
      return strategies.map(s => {
        const b = breakdown.find(b => b.strategy_id === s.id || b.strategy_id === s.type);
        return {
          id:      s.id,
          name:    STRATEGY_NAMES[s.type] ?? s.type,
          enabled: s.enabled,
          trades:  b?.trades  ?? 0,
          wr:      b ? (b.win_rate * 100) : 0,
          pnl:     b?.pnl     ?? 0,
        };
      });
    }
    if (breakdown.length > 0) {
      return breakdown.map(b => ({
        id:      b.strategy_id,
        name:    STRATEGY_NAMES[b.strategy_id] ?? b.strategy_id,
        enabled: b.trades > 0,
        trades:  b.trades,
        wr:      b.win_rate * 100,
        pnl:     b.pnl,
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
          <div
            key={r.id}
            className={clsx(
              'flex items-center gap-3 px-3 py-2.5 rounded-md border transition-colors',
              r.enabled
                ? 'bg-terminal-bg border-terminal-border/50 hover:border-accent/30'
                : 'bg-terminal-bg border-terminal-border/20 opacity-50',
            )}
          >
            {/* Status dot */}
            <div className={clsx(
              'w-1.5 h-1.5 rounded-full shrink-0',
              r.enabled && r.trades > 0 ? 'bg-profit' :
              r.enabled ? 'bg-warn' : 'bg-terminal-subtle/40',
            )} />

            {/* Name + trades/WR */}
            <div className="flex-1 min-w-0">
              <div className="text-[11px] font-mono font-semibold text-terminal-text truncate">{r.name}</div>
              <div className="flex gap-2 mt-0.5">
                <span className="text-[9px] font-mono text-terminal-subtle">{r.trades} trades</span>
                <span className="text-[9px] font-mono text-terminal-subtle">WR {r.wr.toFixed(0)}%</span>
              </div>
            </div>

            {/* PnL + bar */}
            <div className="text-right shrink-0 w-20">
              <div className={clsx(
                'text-[11px] font-mono font-semibold tabular-nums',
                r.pnl > 0 ? 'text-profit' : r.pnl < 0 ? 'text-loss' : 'text-terminal-subtle',
              )}>
                {r.pnl > 0 ? '+' : ''}{r.pnl === 0 ? '$0.00' : `$${Math.abs(r.pnl).toFixed(2)}`}
              </div>
              <div className="w-full h-1 bg-terminal-muted rounded-full mt-1 overflow-hidden">
                <div
                  className={clsx('h-full rounded-full transition-all', r.pnl > 0 ? 'bg-accent' : r.pnl < 0 ? 'bg-loss/60' : 'bg-terminal-subtle/30')}
                  style={{ width: `${Math.min((Math.abs(r.pnl) / maxPnl) * 100, 100)}%` }}
                />
              </div>
            </div>
          </div>
        )) : (
          Array.from({ length: 7 }).map((_, i) => (
            <div key={i} className="h-12 rounded-md skeleton" />
          ))
        )}
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
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: `28px repeat(${symbols.length}, 1fr)`,
            gap: 2,
          }}
        >
          {/* Header row */}
          <div />
          {symbols.map(s => (
            <div key={s} className="text-[8px] font-mono text-terminal-subtle text-center pb-1">
              {s}
            </div>
          ))}

          {/* Data rows */}
          {exchanges.map(ex => (
            <>
              <div key={`l-${ex}`} className="text-[8px] font-mono text-terminal-subtle flex items-center leading-none py-0.5">
                {ex}
              </div>
              {symbols.map(s => {
                const val = spreadMatrix?.[ex]?.[s] ?? null;
                const isPos = val !== null ? val > 0 : null;
                return (
                  <div
                    key={`${ex}-${s}`}
                    className="rounded flex items-center justify-center h-[22px]"
                    style={{
                      background: val === null ? 'rgba(0,0,0,0.03)' :
                        isPos ? `rgba(0,200,150,${Math.min(Math.abs(val) / 30 * 0.4, 0.4)})` :
                                `rgba(255,71,87,${Math.min(Math.abs(val) / 30 * 0.3, 0.3)})`,
                    }}
                  >
                    <span className={clsx(
                      'text-[8px] font-mono',
                      val === null  ? 'text-terminal-subtle/30' :
                      isPos         ? 'text-profit'             : 'text-loss',
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

  // ── KPI values ───────────────────────────────────────────────────────────
  const totalAssets  = portfolio?.total_balance_usdt ?? 0;
  const shadow       = data?.shadow_stats ?? null;
  const sessionPnl   = shadow?.total_pnl ?? data?.pnl?.total ?? portfolio?.total_pnl ?? 0;
  const cumulPnl     = data?.pnl?.total  ?? portfolio?.total_pnl ?? 0;
  const activePos    = data?.position_count ?? 0;
  const winRate      = shadow?.win_rate ?? 0;
  const totalTrades  = shadow?.trades_executed ?? 0;

  const sessionPos   = sessionPnl >= 0;
  const cumulPos     = cumulPnl  >= 0;

  // ── Strategy breakdown ───────────────────────────────────────────────────
  const breakdown    = shadow?.by_strategy ?? [];
  const strategyList = strategies ?? data?.strategies ?? [];

  // ── Spread matrix (future: from WS) ─────────────────────────────────────
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
          value={totalAssets > 0 ? `$${totalAssets.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '$0.00'}
          sub={totalAssets > 0 ? undefined : '포트폴리오 연결 중'}
          up={null}
          loading={!portfolio && !data}
        />
        <KpiCard
          label="일일 PNL"
          value={`${sessionPos ? '+' : ''}$${Math.abs(sessionPnl).toFixed(2)}`}
          sub={`${(winRate * 100).toFixed(0)}% WR`}
          up={sessionPos ? true : sessionPnl < 0 ? false : null}
          loading={!data && !portfolio}
        />
        <KpiCard
          label="누적 PNL"
          value={`${cumulPos ? '+' : ''}$${Math.abs(cumulPnl).toFixed(2)}`}
          sub={`${totalTrades}건 체결`}
          up={cumulPos ? true : cumulPnl < 0 ? false : null}
          loading={!data && !portfolio}
        />
        <KpiCard
          label="활성 포지션"
          value={String(activePos)}
          sub={activePos > 0 ? `${activePos}개 운용 중` : '포지션 없음'}
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

      {/* ── Row 4: Events / Alerts ──────────────────────────────────────── */}
      <EventFeed />

    </div>
  );
}
