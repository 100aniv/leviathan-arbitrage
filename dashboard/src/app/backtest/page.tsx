'use client';

import { useState } from 'react';
import clsx from 'clsx';
import { useApi } from '@/hooks/useApi';

interface StrategyBreakdown {
  pnl: number;
  trades: number;
  wins: number;
  win_rate: number;
}

interface BacktestCase {
  case_id: string;
  exchange_ids: string[];
  strategy_ids: string[];
  seed_capital: number;
  period: string;
  note: string;
  snapshots_replayed: number;
  signals_generated: number;
  trades: number;
  pnl: number;
  sharpe: number;
  mdd_pct: number;
  win_rate: number;
  profit_factor: number;
  error: string;
  by_strategy: Record<string, StrategyBreakdown>;
}

async function fetchBatchResults(): Promise<BacktestCase[]> {
  // Load K-BT JSON files from /public/backtest/ (static, no engine dependency)
  const ids = Array.from({ length: 18 }, (_, i) => String(i + 1).padStart(2, '0'));
  const results = await Promise.all(
    ids.map(async id => {
      try {
        const res = await fetch(`/backtest/backtest-summary-K-BT-${id}.json`);
        if (!res.ok) return null;
        return res.json() as Promise<BacktestCase>;
      } catch {
        return null;
      }
    })
  );
  return results.filter((r): r is BacktestCase => r !== null);
}

const STRATEGY_ABBR: Record<string, string> = {
  funding_rate_v1: 'FR',
  triangular_v1: 'TRI',
  statistical_arb_v1: 'SA',
  spot_futures_v1: 'SF',
  cross_exchange_v1: 'CE',
  futures_futures_v1: 'FF',
  cex_dex_v1: 'CD',
};

function abbr(id: string) {
  return STRATEGY_ABBR[id] ?? id.split('_')[0].toUpperCase().slice(0, 3);
}

function PassBadge({ pass }: { pass: boolean }) {
  return (
    <span className={clsx(
      'text-[9px] font-mono font-bold px-1.5 py-0.5 rounded',
      pass ? 'bg-profit/20 text-profit' : 'bg-loss/20 text-loss',
    )}>
      {pass ? 'AC_PASS' : 'AC_FAIL'}
    </span>
  );
}

export default function BacktestPage() {
  const { data: cases, isLoading, error } = useApi<BacktestCase[]>(
    '/api/backtest/batch_results',
    fetchBatchResults,
    { refreshInterval: 0 },
  );
  const [expanded, setExpanded] = useState<string | null>(null);

  const validCases = cases?.filter(c => !c.error) ?? [];
  const totalPnl = validCases.reduce((s, c) => s + c.pnl, 0);
  const avgSharpe = validCases.length ? validCases.reduce((s, c) => s + c.sharpe, 0) / validCases.length : 0;
  const passCount = validCases.filter(c => c.sharpe >= 1.0 && c.pnl > 0).length;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-mono font-semibold text-terminal-text">백테스트 결과</h2>
          <p className="text-[10px] font-mono text-terminal-subtle mt-0.5">K-BT 배치 — Phase K 검증 케이스</p>
        </div>
        <span className="text-[10px] font-mono text-terminal-subtle">
          {cases?.length ?? 0}개 케이스
        </span>
      </div>

      {/* Summary KPI */}
      {!isLoading && validCases.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { label: '총 케이스', value: `${cases?.length ?? 0}개`, sub: `${passCount} AC_PASS`, up: null },
            { label: '누적 PnL', value: `+$${totalPnl.toLocaleString('en-US', { maximumFractionDigits: 0 })}`, sub: '전 케이스 합산', up: totalPnl > 0 ? true : false as boolean | null },
            { label: '평균 Sharpe', value: avgSharpe.toFixed(2), sub: '≥1.0 = 우수', up: avgSharpe >= 1 ? true : false as boolean | null },
            { label: '합격률', value: `${passCount}/${validCases.length}`, sub: 'Sharpe≥1 & PnL>0', up: passCount === validCases.length ? true : null },
          ].map(k => (
            <div key={k.label} className="card">
              <div className="card-header">{k.label}</div>
              <div className={clsx(
                'text-xl font-mono font-bold tabular-nums mt-1',
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
          <span className="card-header mb-0">케이스별 결과</span>
          <span className="text-[9px] font-mono text-terminal-subtle">행 클릭 시 전략별 상세</span>
        </div>

        {isLoading && (
          <div className="p-8 flex items-center justify-center">
            <div className="h-4 w-32 skeleton rounded" />
          </div>
        )}
        {error && (
          <div className="p-6 text-center text-[11px] font-mono text-loss">
            결과 로드 실패 — 엔진 연결 확인
          </div>
        )}
        {!isLoading && !error && (cases?.length ?? 0) === 0 && (
          <div className="p-8 text-center text-[11px] font-mono text-terminal-subtle">
            백테스트 결과 없음
          </div>
        )}

        {!isLoading && (cases?.length ?? 0) > 0 && (
          <div className="overflow-x-auto">
            {/* Header row */}
            <div className="grid grid-cols-[80px_140px_1fr_80px_80px_80px_72px_72px] gap-0 px-4 py-2 border-b border-terminal-border bg-terminal-muted/20 text-[9px] font-mono text-terminal-subtle uppercase tracking-wider">
              <span>ID</span>
              <span>기간</span>
              <span>거래소 / 전략</span>
              <span className="text-right">PnL</span>
              <span className="text-right">Sharpe</span>
              <span className="text-right">WR</span>
              <span className="text-right">MDD</span>
              <span className="text-center">결과</span>
            </div>

            {cases?.map(c => {
              const pass = !c.error && c.sharpe >= 1.0 && c.pnl > 0;
              const isExp = expanded === c.case_id;
              return (
                <div key={c.case_id}>
                  <div
                    className={clsx(
                      'grid grid-cols-[80px_140px_1fr_80px_80px_80px_72px_72px] gap-0 px-4 py-2.5 border-b border-terminal-border/50 cursor-pointer transition-colors',
                      isExp ? 'bg-terminal-muted/30' : 'hover:bg-terminal-muted/20',
                      c.error ? 'opacity-50' : '',
                    )}
                    onClick={() => setExpanded(isExp ? null : c.case_id)}
                  >
                    <span className="text-[11px] font-mono font-semibold text-accent">{c.case_id}</span>
                    <span className="text-[10px] font-mono text-terminal-subtle">{c.period}</span>
                    <div className="flex flex-wrap gap-1 items-center">
                      {c.exchange_ids.map(e => (
                        <span key={e} className="text-[8px] font-mono px-1 py-0.5 bg-terminal-bg border border-terminal-border rounded text-terminal-subtle">
                          {e.replace('_futures', 'F').replace('binance', 'BN').replace('bybit', 'BB').replace('okx', 'OKX').replace('bitget', 'BG').replace('upbit', 'UP').replace('bithumb', 'BH').replace('coinone', 'CO')}
                        </span>
                      ))}
                      {c.strategy_ids.map(s => (
                        <span key={s} className="text-[8px] font-mono px-1 py-0.5 bg-accent/10 border border-accent/20 rounded text-accent">
                          {abbr(s)}
                        </span>
                      ))}
                    </div>
                    <span className={clsx('text-[11px] font-mono font-semibold tabular-nums text-right', c.pnl > 0 ? 'text-profit' : c.pnl < 0 ? 'text-loss' : 'text-terminal-subtle')}>
                      {c.error ? '—' : `${c.pnl >= 0 ? '+' : ''}$${Math.abs(c.pnl).toLocaleString('en-US', { maximumFractionDigits: 0 })}`}
                    </span>
                    <span className={clsx('text-[11px] font-mono tabular-nums text-right', c.sharpe >= 2 ? 'text-profit' : c.sharpe >= 1 ? 'text-warn' : 'text-loss')}>
                      {c.error ? '—' : c.sharpe.toFixed(2)}
                    </span>
                    <span className="text-[11px] font-mono tabular-nums text-right text-terminal-text">
                      {c.error ? '—' : `${c.win_rate.toFixed(1)}%`}
                    </span>
                    <span className={clsx('text-[11px] font-mono tabular-nums text-right', c.mdd_pct > 5 ? 'text-loss' : c.mdd_pct > 2 ? 'text-warn' : 'text-profit')}>
                      {c.error ? '—' : `${c.mdd_pct.toFixed(1)}%`}
                    </span>
                    <div className="flex justify-center items-center">
                      {c.error ? (
                        <span className="text-[9px] font-mono text-loss">ERR</span>
                      ) : (
                        <PassBadge pass={pass} />
                      )}
                    </div>
                  </div>

                  {/* Expanded by_strategy */}
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
                              <span>{s.wins}W</span>
                            </div>
                          </div>
                        ))}
                      </div>
                      {c.note && (
                        <div className="text-[9px] font-mono text-terminal-subtle/70 mt-2">
                          Note: {c.note}
                        </div>
                      )}
                      <div className="flex gap-4 text-[9px] font-mono text-terminal-subtle mt-2">
                        <span>스냅샷: {c.snapshots_replayed.toLocaleString()}</span>
                        <span>시그널: {c.signals_generated}</span>
                        <span>체결: {c.trades}</span>
                        <span>시드: ${c.seed_capital.toLocaleString()}</span>
                        {c.profit_factor < 1e9 && <span>PF: {c.profit_factor.toFixed(2)}</span>}
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
