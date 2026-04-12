"use client";

import { useState, useMemo } from "react";
import dynamic from "next/dynamic";
import { useApi } from "@/hooks/useApi";
import {
  getEquityCurve,
  getPortfolioMetrics,
  getStrategyMetrics,
  getTrades,
} from "@/lib/api";
import type { Trade, StrategyMetric } from "@/types";
import ko from "@/i18n/ko.json";

// ─── Recharts dynamic import (SSR off) ───────────────────────────────────────

const AreaChart = dynamic(() => import("recharts").then((m) => m.AreaChart), { ssr: false });
const Area = dynamic(() => import("recharts").then((m) => m.Area), { ssr: false });
const XAxis = dynamic(() => import("recharts").then((m) => m.XAxis), { ssr: false });
const YAxis = dynamic(() => import("recharts").then((m) => m.YAxis), { ssr: false });
const CartesianGrid = dynamic(() => import("recharts").then((m) => m.CartesianGrid), { ssr: false });
const Tooltip = dynamic(() => import("recharts").then((m) => m.Tooltip), { ssr: false });
const ResponsiveContainer = dynamic(() => import("recharts").then((m) => m.ResponsiveContainer), { ssr: false });
const BarChart = dynamic(() => import("recharts").then((m) => m.BarChart), { ssr: false });
const Bar = dynamic(() => import("recharts").then((m) => m.Bar), { ssr: false });

// ─── Types ────────────────────────────────────────────────────────────────────

interface EquityCurvePoint {
  date: string;
  equity: number;
  pnl: number;
  btc_benchmark: number | null;
}

interface PortfolioMetrics {
  sharpe_ratio: number | null;
  max_drawdown_pct: number;
  calmar_ratio: number | null;
  win_rate: number;
  total_trades: number;
  total_pnl: number;
}

type Period = "today" | "week" | "month" | "all";

const PERIODS: { id: Period; label: string }[] = [
  { id: "today", label: ko.insights.period.today },
  { id: "week",  label: ko.insights.period.week  },
  { id: "month", label: ko.insights.period.month },
  { id: "all",   label: ko.insights.period.all   },
];

const STRATEGY_KO: Record<string, string> = {
  funding_rate_arb:       "펀딩비 수익",
  funding_rate_arb_v1:    "펀딩비 수익",
  cross_exchange_spot:    "교차 거래소 차익",
  cross_exchange_spot_v1: "교차 거래소 차익",
  cross_exchange_v1:      "교차 거래소 차익",
  futures_futures:        "선물-선물 차익",
  futures_futures_v1:     "선물-선물 차익",
  spot_futures_basis:     "현물-선물 차익",
  spot_futures_v1:        "현물-선물 차익",
  statistical_arb:        "통계적 차익",
  statistical_arb_v1:     "통계적 차익",
  triangular:             "삼각 차익",
  triangular_v1:          "삼각 차익",
  cex_dex_hybrid:         "CEX-DEX 차익",
  cex_dex_v1:             "CEX-DEX 차익",
};

// ─── Tooltip icon (ⓘ) ────────────────────────────────────────────────────────

function InfoTip({ text }: { text: string }) {
  const [show, setShow] = useState(false);
  return (
    <span className="relative inline-block">
      <button
        onMouseEnter={() => setShow(true)}
        onMouseLeave={() => setShow(false)}
        onFocus={() => setShow(true)}
        onBlur={() => setShow(false)}
        onClick={() => setShow((v) => !v)}
        aria-label="설명 보기"
        className="w-4 h-4 rounded-full bg-bg-elevated text-text-tertiary text-[10px] font-bold flex items-center justify-center hover:text-brand transition-colors focus:outline-none"
      >
        i
      </button>
      {show && (
        <span className="absolute bottom-6 left-1/2 -translate-x-1/2 z-20 w-52 bg-bg-elevated border border-border rounded-[10px] px-3 py-2 text-small text-text-secondary shadow-lg pointer-events-none">
          {text}
        </span>
      )}
    </span>
  );
}

// ─── KPI Card ─────────────────────────────────────────────────────────────────

function KpiCard({
  label,
  value,
  desc,
  tooltip,
}: {
  label: string;
  value: string;
  desc: string;
  tooltip: string;
}) {
  return (
    <div className="bg-bg-surface border border-border rounded-[16px] p-4">
      <div className="flex items-center justify-between mb-1">
        <p className="text-small text-text-tertiary">{label}</p>
        <InfoTip text={tooltip} />
      </div>
      <p className="text-2xl font-bold tabular-nums text-text-primary leading-tight">{value}</p>
      <p className="text-small text-text-tertiary mt-1 leading-snug">{desc}</p>
    </div>
  );
}

// ─── Trade row ────────────────────────────────────────────────────────────────

function TradeRow({ trade }: { trade: Trade }) {
  const pnlPos = trade.pnl > 0;
  const ts = new Date(trade.timestamp).toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
  const stratLabel = STRATEGY_KO[trade.strategy_id ?? ""] ?? trade.strategy_id ?? "—";
  return (
    <div className="flex items-center gap-2 px-4 py-3 border-b border-border last:border-0 hover:bg-bg-surface transition-colors">
      <span className="text-small text-text-tertiary tabular-nums w-24 shrink-0">{ts}</span>
      <span className="text-caption font-semibold text-text-primary flex-1 truncate min-w-0">{trade.symbol}</span>
      <span className="text-small text-text-tertiary w-16 truncate hidden sm:block">{stratLabel}</span>
      <span
        className={`text-caption font-semibold tabular-nums w-20 text-right shrink-0 ${
          pnlPos ? "text-success" : trade.pnl < 0 ? "text-danger" : "text-text-secondary"
        }`}
      >
        {trade.pnl >= 0 ? "▲ +" : "▼ "}${Math.abs(trade.pnl).toFixed(2)}
      </span>
    </div>
  );
}

// ─── Equity Curve Empty State ─────────────────────────────────────────────────

function EquityEmptyState() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-2 py-8">
      <span className="text-3xl" aria-hidden>📈</span>
      <p className="text-body font-semibold text-text-secondary">{ko.insights.noData}</p>
      <p className="text-small text-text-tertiary text-center max-w-xs leading-relaxed">
        {ko.insights.noDataDesc}
        <br />
        <span className="text-brand">Shadow 모드 10분</span> 이상 실행하면 나타납니다.
      </p>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function InsightsPage() {
  const [period, setPeriod] = useState<Period>("week");
  const [stratFilter, setStratFilter] = useState<string>("all");

  const { data: curveData } = useApi<{ curve: EquityCurvePoint[] }>(
    "/insights/equity-curve", getEquityCurve, { refreshInterval: 30_000 },
  );
  const { data: metrics } = useApi<PortfolioMetrics>(
    "/insights/portfolio-metrics", getPortfolioMetrics, { refreshInterval: 10_000 },
  );
  const { data: strategyMetricsData } = useApi<{ strategies: Record<string, StrategyMetric> }>(
    "/insights/strategy-metrics", getStrategyMetrics, { refreshInterval: 10_000 },
  );
  const { data: rawTrades } = useApi<Trade[]>(
    "/insights/trades", () => getTrades({ limit: 100 }), { refreshInterval: 10_000 },
  );

  // ── Period cutoff ─────────────────────────────────────────────────────────
  const cutoffMs = useMemo(() => {
    const now = Date.now();
    if (period === "all") return 0;
    if (period === "today") return now - 86_400_000;
    if (period === "week")  return now - 7 * 86_400_000;
    return now - 30 * 86_400_000;
  }, [period]);

  // ── Equity curve filtered by period ──────────────────────────────────────
  const allCurve = curveData?.curve ?? [];
  const filteredCurve = useMemo(
    () => cutoffMs === 0
      ? allCurve
      : allCurve.filter((p) => new Date(p.date).getTime() >= cutoffMs),
    [allCurve, cutoffMs],
  );

  // ── Strategy rows for bar chart ───────────────────────────────────────────
  const strategyRows = strategyMetricsData ? Object.values(strategyMetricsData.strategies) : [];
  const strategyHasData = strategyRows.some(r => r.pnl !== 0 || r.fills > 0);

  // ── Trade filters ─────────────────────────────────────────────────────────
  const trades = useMemo(() => {
    if (!rawTrades) return [];
    return rawTrades.filter((t) => {
      const inPeriod = cutoffMs === 0 || new Date(t.timestamp).getTime() >= cutoffMs;
      const inStrat  = stratFilter === "all" || t.strategy_id === stratFilter;
      return inPeriod && inStrat;
    });
  }, [rawTrades, cutoffMs, stratFilter]);

  // Available strategies from trades
  const availableStrats = useMemo(() => {
    if (!rawTrades) return [];
    return Array.from(new Set(rawTrades.map((t) => t.strategy_id).filter(Boolean))) as string[];
  }, [rawTrades]);

  // ── KPI derived values ────────────────────────────────────────────────────
  const winRate  = metrics ? `${(metrics.win_rate * 100).toFixed(1)}%` : "—";
  const sharpe   = metrics?.sharpe_ratio != null ? metrics.sharpe_ratio.toFixed(2) : "—";
  const mdd      = metrics ? `${metrics.max_drawdown_pct.toFixed(1)}%` : "—";
  const totalPnl = metrics
    ? `${metrics.total_pnl >= 0 ? "+" : ""}$${Math.abs(metrics.total_pnl).toFixed(2)}`
    : "—";

  return (
    <div className="max-w-screen-xl mx-auto px-4 md:px-6 py-4 pb-24 space-y-6">
      <h1 className="text-heading font-bold text-text-primary">{ko.nav.insights}</h1>

      {/* ── 기간 선택 칩 ── */}
      <div className="flex items-center gap-2 flex-wrap">
        {PERIODS.map((p) => (
          <button
            key={p.id}
            onClick={() => setPeriod(p.id)}
            className={`px-4 py-1.5 rounded-full text-caption font-medium transition-colors ${
              period === p.id
                ? "bg-brand text-white shadow-sm"
                : "bg-bg-surface border border-border text-text-secondary hover:text-text-primary hover:border-brand/50"
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* ── 에쿼티 커브 ── */}
      <div className="bg-bg-surface border border-border rounded-[16px] p-4">
        <h2 className="text-body font-semibold text-text-primary mb-4">{ko.insights.equityCurve}</h2>
        <div className="h-52">
          {filteredCurve.length > 0 ? (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={filteredCurve}>
                <defs>
                  <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="var(--color-brand)" stopOpacity={0.25} />
                    <stop offset="95%" stopColor="var(--color-brand)" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 10, fill: "var(--color-text-tertiary)" }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v: string) => v.slice(5)}
                />
                <YAxis
                  tick={{ fontSize: 10, fill: "var(--color-text-tertiary)" }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v: number) => `$${v.toFixed(0)}`}
                />
                <Tooltip
                  contentStyle={{
                    background: "var(--color-bg-elevated)",
                    border: "1px solid var(--color-border)",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  labelStyle={{ color: "var(--color-text-secondary)" }}
                />
                <Area
                  type="monotone"
                  dataKey="equity"
                  stroke="var(--color-brand)"
                  strokeWidth={2}
                  fill="url(#equityGrad)"
                  dot={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <EquityEmptyState />
          )}
        </div>
      </div>

      {/* ── KPI 4개 ── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <KpiCard
          label={ko.insights.winRate}
          value={winRate}
          desc={metrics ? `${metrics.total_trades}건 체결` : "—"}
          tooltip={ko.metric.winRate}
        />
        <KpiCard
          label={ko.insights.sharpe}
          value={sharpe}
          desc={ko.insights.sharpeDesc}
          tooltip={ko.metric.sharpe}
        />
        <KpiCard
          label={ko.insights.maxDrawdown}
          value={mdd}
          desc={ko.insights.maxDrawdownDesc}
          tooltip={ko.metric.mdd}
        />
        <KpiCard
          label={ko.insights.totalPnl}
          value={totalPnl}
          desc={ko.insights.totalPnlDesc}
          tooltip={ko.insights.totalPnlTooltip}
        />
      </div>

      {/* ── 전략별 성과 바 차트 ── */}
      <div className="bg-bg-surface border border-border rounded-[16px] p-4">
        <h2 className="text-body font-semibold text-text-primary mb-4">{ko.insights.byStrategy}</h2>
        {strategyRows.length === 0 || !strategyHasData ? (
          <p className="text-small text-text-tertiary py-8 text-center">{ko.insights.byStrategyEmpty}</p>
        ) : (
        <div className="h-48">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={strategyRows} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" horizontal={false} />
                <XAxis
                  type="number"
                  tick={{ fontSize: 10, fill: "var(--color-text-tertiary)" }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v: number) => `$${v.toFixed(0)}`}
                />
                <YAxis
                  type="category"
                  dataKey="type"
                  tick={{ fontSize: 10, fill: "var(--color-text-tertiary)" }}
                  tickLine={false}
                  axisLine={false}
                  width={80}
                  tickFormatter={(v: string) => STRATEGY_KO[v] ?? v}
                />
                <Tooltip
                  contentStyle={{
                    background: "var(--color-bg-elevated)",
                    border: "1px solid var(--color-border)",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                />
                <Bar dataKey="pnl" fill="var(--color-brand)" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {/* ── 거래 내역 ── */}
      <div className="bg-bg-surface border border-border rounded-[16px] overflow-hidden">
        <div className="px-4 py-3 border-b border-border">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-body font-semibold text-text-primary">{ko.insights.tradeHistory}</h2>
            <span className="text-small text-text-tertiary">{trades.length}건</span>
          </div>
          {/* 전략 필터 칩 */}
          {availableStrats.length > 0 && (
            <div className="flex gap-2 flex-wrap mt-2">
              <button
                onClick={() => setStratFilter("all")}
                className={`px-3 py-1 rounded-full text-small font-medium transition-colors ${
                  stratFilter === "all"
                    ? "bg-brand text-white"
                    : "bg-bg-elevated border border-border text-text-secondary hover:text-text-primary"
                }`}
              >
                전체
              </button>
              {availableStrats.map((sid) => (
                <button
                  key={sid}
                  onClick={() => setStratFilter(sid)}
                  className={`px-3 py-1 rounded-full text-small font-medium transition-colors ${
                    stratFilter === sid
                      ? "bg-brand text-white"
                      : "bg-bg-elevated border border-border text-text-secondary hover:text-text-primary"
                  }`}
                >
                  {STRATEGY_KO[sid] ?? sid}
                </button>
              ))}
            </div>
          )}
        </div>
        {trades.length > 0 ? (
          <div className="divide-y divide-border max-h-96 overflow-y-auto">
            {trades.map((t) => (
              <TradeRow key={t.id} trade={t} />
            ))}
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-12 gap-2">
            <span className="text-2xl" aria-hidden>📋</span>
            <p className="text-caption text-text-secondary">{ko.empty.noTrades}</p>
            <p className="text-small text-text-tertiary">{ko.empty.noTradesDesc}</p>
          </div>
        )}
      </div>
    </div>
  );
}
