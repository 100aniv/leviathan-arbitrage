"use client";

import { useState, useCallback } from "react";
import { useApi } from "@/hooks/useApi";
import {
  getMode,
  getStrategies,
  getExchangeStatus,
  getSettings,
  updateMode,
  toggleStrategy,
  updateSettings,
} from "@/lib/api";
import type {
  ModeResponse,
  Strategy,
  ExchangeStatus,
  SettingsResponse,
} from "@/types";
import ko from "@/i18n/ko.json";

// ─── Constants ────────────────────────────────────────────────────────────────

const STRATEGY_KO: Record<string, string> = {
  funding_rate_arb: "펀딩레이트",
  cross_exchange_spot: "크로스익스체인지",
  futures_futures: "선물선물",
  spot_futures_basis: "현선물",
  statistical_arb: "통계차익",
  triangular: "삼각차익",
  cex_dex_hybrid: "CEX-DEX",
};

const EXCHANGE_DISPLAY: Record<string, string> = {
  binance: "Binance",
  binance_futures: "Binance Fut",
  bybit: "Bybit",
  bybit_futures: "Bybit Fut",
  okx: "OKX",
  okx_futures: "OKX Fut",
  bitget: "Bitget",
  bitget_futures: "Bitget Fut",
  upbit: "Upbit",
  bithumb: "Bithumb",
  coinone: "Coinone",
  mexc: "MEXC",
  gateio: "Gate.io",
};

function displayExchange(id: string): string {
  return EXCHANGE_DISPLAY[id] ?? id.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function strategyName(s: Strategy): string {
  return STRATEGY_KO[s.type] ?? STRATEGY_KO[s.id] ?? s.type;
}

function fmtBalance(ex: ExchangeStatus): string | null {
  const b = ex.balance ?? {};
  const usdt = b["USDT"] ?? b["usdt"] ?? 0;
  const krw = b["KRW"] ?? b["krw"] ?? 0;
  if (krw > 0) return `₩${krw.toLocaleString("ko-KR")}`;
  if (usdt > 0) return `$${usdt.toFixed(2)}`;
  return null;
}

// ─── Toast ────────────────────────────────────────────────────────────────────

function Toast({ msg, type }: { msg: string; type: "success" | "error" }) {
  return (
    <div
      className={`fixed bottom-24 left-1/2 -translate-x-1/2 z-50 px-4 py-2.5 rounded-[12px] text-caption font-medium shadow-lg pointer-events-none
        ${type === "success" ? "bg-success text-white" : "bg-danger text-white"}`}
    >
      {msg}
    </div>
  );
}

// ─── Live Confirm Modal ───────────────────────────────────────────────────────

function LiveConfirmModal({
  onConfirm,
  onCancel,
}: {
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center px-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-sm"
        onClick={onCancel}
        aria-hidden
      />
      {/* Modal */}
      <div className="relative bg-bg-elevated border border-border rounded-[20px] p-6 w-full max-w-sm shadow-xl">
        <div className="flex items-center gap-2 mb-3">
          <span className="text-xl" aria-hidden>⚠️</span>
          <h2 className="text-body font-bold text-text-primary">
            {ko.mode.switchToLive}
          </h2>
        </div>
        <p className="text-caption text-text-secondary mb-6">
          {ko.mode.liveDesc}
          <br />
          <span className="text-danger font-medium">정말 실거래로 전환하시겠습니까?</span>
        </p>
        <div className="flex gap-3">
          <button
            onClick={onCancel}
            className="flex-1 py-2.5 rounded-[12px] text-caption font-semibold bg-bg-surface border border-border text-text-primary hover:bg-bg-elevated transition-colors"
          >
            {ko.common.cancel}
          </button>
          <button
            onClick={onConfirm}
            className="flex-1 py-2.5 rounded-[12px] text-caption font-semibold bg-danger text-white hover:opacity-90 transition-opacity"
          >
            {ko.mode.switchToLive} →
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Mode Toggle ──────────────────────────────────────────────────────────────

function ModeToggle({
  mode,
  onRequestLive,
  onSwitchPaper,
}: {
  mode: string;
  onRequestLive: () => void;
  onSwitchPaper: () => void;
}) {
  const isPaper = mode !== "live";
  const isLive = mode === "live";

  return (
    <div className="bg-bg-surface border border-border rounded-[16px] p-5">
      <p className="text-small text-text-tertiary mb-3">{ko.manage.currentMode}</p>
      <div className="flex items-center gap-3">
        <button
          onClick={onSwitchPaper}
          className={`flex-1 py-3 rounded-[12px] text-body font-semibold transition-colors ${
            isPaper
              ? "bg-brand text-white shadow-sm"
              : "bg-bg-elevated text-text-secondary hover:bg-bg-surface hover:text-text-primary"
          }`}
        >
          {ko.mode.paper}
        </button>
        <button
          onClick={onRequestLive}
          className={`flex-1 py-3 rounded-[12px] text-body font-semibold transition-colors ${
            isLive
              ? "bg-danger text-white shadow-sm"
              : "bg-bg-elevated text-text-secondary hover:bg-bg-surface hover:text-text-primary"
          }`}
        >
          {ko.mode.live}
        </button>
      </div>
      <p className={`text-small mt-2 ${isLive ? "text-danger" : "text-text-tertiary"}`}>
        {isLive ? ko.mode.liveDesc : ko.mode.paperDesc}
      </p>
    </div>
  );
}

// ─── Strategy Card ────────────────────────────────────────────────────────────

function StrategyCard({
  strategy,
  onToggle,
}: {
  strategy: Strategy;
  onToggle: (id: string) => void;
}) {
  const metrics = strategy.metrics as Record<string, number> | undefined;
  const pnl = metrics?.total_realized_pnl_usdt ?? 0;
  const fills = metrics?.fills_received ?? 0;
  const signals = metrics?.signals_received ?? 0;

  return (
    <div className="bg-bg-surface border border-border rounded-[16px] p-4 transition-shadow hover:shadow-sm">
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="min-w-0">
          <p className="text-body font-semibold text-text-primary truncate">
            {strategyName(strategy)}
          </p>
          <span
            className={`inline-flex items-center gap-1 text-small font-medium mt-0.5 ${
              strategy.enabled ? "text-success" : "text-text-tertiary"
            }`}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                strategy.enabled ? "bg-success" : "bg-text-tertiary"
              }`}
            />
            {strategy.enabled ? ko.manage.strategyEnabled : ko.manage.strategyDisabled}
          </span>
        </div>
        {/* Toggle switch */}
        <button
          onClick={() => onToggle(strategy.id)}
          role="switch"
          aria-checked={strategy.enabled}
          aria-label={`${strategyName(strategy)} ${strategy.enabled ? "비활성화" : "활성화"}`}
          className={`relative w-10 h-6 rounded-full transition-colors shrink-0 focus:outline-none focus:ring-2 focus:ring-brand focus:ring-offset-2 ${
            strategy.enabled ? "bg-brand" : "bg-bg-elevated border border-border"
          }`}
        >
          <span
            className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow-sm transition-transform ${
              strategy.enabled ? "translate-x-4" : "translate-x-0.5"
            }`}
          />
        </button>
      </div>
      <div className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-small">
        <span className="text-text-tertiary">PnL</span>
        <span
          className={`font-semibold tabular-nums text-right ${
            pnl > 0 ? "text-success" : pnl < 0 ? "text-danger" : "text-text-secondary"
          }`}
        >
          {pnl >= 0 ? "+" : ""}${Math.abs(pnl).toFixed(2)}
        </span>
        <span className="text-text-tertiary">체결</span>
        <span className="text-text-primary font-medium tabular-nums text-right">{fills}건</span>
        <span className="text-text-tertiary">시그널</span>
        <span className="text-text-tertiary tabular-nums text-right">{signals.toLocaleString()}</span>
      </div>
    </div>
  );
}

// ─── Exchange Card ────────────────────────────────────────────────────────────

function ExchangeCard({ id, status }: { id: string; status: ExchangeStatus }) {
  const bal = fmtBalance(status);
  const abbr = displayExchange(id).slice(0, 2).toUpperCase();

  return (
    <div className="bg-bg-surface border border-border rounded-[16px] p-4">
      <div className="flex items-center gap-2 mb-2">
        <div className="w-8 h-8 rounded-[8px] bg-bg-elevated flex items-center justify-center text-small font-bold text-text-primary shrink-0">
          {abbr}
        </div>
        <p className="text-small font-semibold text-text-primary truncate flex-1">
          {displayExchange(id)}
        </p>
      </div>
      <div className="flex items-center gap-1.5 mb-1">
        <span
          className={`w-1.5 h-1.5 rounded-full ${
            status.connected ? "bg-success" : "bg-danger"
          }`}
        />
        <span
          className={`text-small font-medium ${
            status.connected ? "text-success" : "text-danger"
          }`}
        >
          {status.connected ? ko.manage.connected : ko.manage.disconnected}
        </span>
        {status.connected && status.latency_ms != null && (
          <span className="text-small text-text-tertiary ml-auto">{status.latency_ms}ms</span>
        )}
      </div>
      {bal && (
        <p className="text-caption font-bold text-text-primary tabular-nums mt-1">{bal}</p>
      )}
    </div>
  );
}

// ─── Capital Settings — view/edit 2-mode ─────────────────────────────────────

function CapitalSettings({
  settings,
  onSave,
}: {
  settings: SettingsResponse;
  onSave: (s: Partial<SettingsResponse>) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [minEdge, setMinEdge] = useState(String(settings.min_edge_bps ?? ""));
  const [maxPos, setMaxPos] = useState(String(settings.max_position_usd ?? ""));
  const [dailyLoss, setDailyLoss] = useState(String(settings.max_daily_loss_usd ?? ""));
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  function handleEdit() {
    // 현재 settings 값으로 초기화
    setMinEdge(String(settings.min_edge_bps ?? ""));
    setMaxPos(String(settings.max_position_usd ?? ""));
    setDailyLoss(String(settings.max_daily_loss_usd ?? ""));
    setEditing(true);
  }

  function handleCancel() {
    setEditing(false);
  }

  async function handleSave() {
    setSaving(true);
    try {
      await onSave({
        min_edge_bps: Number(minEdge) || settings.min_edge_bps,
        max_position_usd: Number(maxPos) || settings.max_position_usd,
        max_daily_loss_usd: Number(dailyLoss) || settings.max_daily_loss_usd,
      });
      setEditing(false);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="bg-bg-surface border border-border rounded-[16px] p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-body font-semibold text-text-primary">
          {ko.manage.capitalSettings}
        </h3>
        {!editing && (
          <button
            onClick={handleEdit}
            className="text-small font-medium text-brand hover:underline"
          >
            수정
          </button>
        )}
        {saved && !editing && (
          <span className="text-small text-success font-medium">✓ 저장됐습니다</span>
        )}
      </div>

      {editing ? (
        /* ── 편집 모드 ── */
        <div className="space-y-4">
          <div>
            <label className="text-small text-text-secondary block mb-1">
              최소 엣지 (bps)
            </label>
            <input
              type="number"
              value={minEdge}
              onChange={(e) => setMinEdge(e.target.value)}
              className="w-full bg-bg-elevated border border-border rounded-[10px] px-3 py-2.5 text-body text-text-primary outline-none focus:border-brand transition-colors"
            />
            <p className="text-small text-text-tertiary mt-1">1bps = 0.01%. 현재: {settings.min_edge_bps}bps</p>
          </div>
          <div>
            <label className="text-small text-text-secondary block mb-1">
              최대 포지션 (USD)
            </label>
            <input
              type="number"
              value={maxPos}
              onChange={(e) => setMaxPos(e.target.value)}
              className="w-full bg-bg-elevated border border-border rounded-[10px] px-3 py-2.5 text-body text-text-primary outline-none focus:border-brand transition-colors"
            />
          </div>
          <div>
            <label className="text-small text-text-secondary block mb-1">
              {ko.manage.dailyLossLimit}
            </label>
            <input
              type="number"
              value={dailyLoss}
              onChange={(e) => setDailyLoss(e.target.value)}
              className="w-full bg-bg-elevated border border-border rounded-[10px] px-3 py-2.5 text-body text-text-primary outline-none focus:border-brand transition-colors"
            />
          </div>
          <div className="flex gap-3 pt-1">
            <button
              onClick={handleCancel}
              className="flex-1 py-2.5 rounded-[12px] text-caption font-semibold bg-bg-elevated border border-border text-text-primary hover:bg-bg-surface transition-colors"
            >
              {ko.common.cancel}
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className="flex-1 py-2.5 rounded-[12px] text-caption font-semibold bg-brand text-white disabled:opacity-50 transition-opacity hover:opacity-90"
            >
              {saving ? "저장 중…" : `${ko.common.save} ✓`}
            </button>
          </div>
        </div>
      ) : (
        /* ── 뷰 모드 ── */
        <div className="space-y-3">
          <div className="flex items-center justify-between py-2 border-b border-border">
            <span className="text-caption text-text-secondary">최소 엣지</span>
            <span className="text-caption font-semibold text-text-primary tabular-nums">
              {settings.min_edge_bps ?? "—"} bps
            </span>
          </div>
          <div className="flex items-center justify-between py-2 border-b border-border">
            <span className="text-caption text-text-secondary">최대 포지션</span>
            <span className="text-caption font-semibold text-text-primary tabular-nums">
              ${settings.max_position_usd?.toLocaleString() ?? "—"}
            </span>
          </div>
          <div className="flex items-center justify-between py-2">
            <span className="text-caption text-text-secondary">{ko.manage.dailyLossLimit}</span>
            <span className="text-caption font-semibold text-text-primary tabular-nums">
              ${settings.max_daily_loss_usd?.toLocaleString() ?? "—"}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function ManagePage() {
  const POLL = { refreshInterval: 5_000 };

  const [showLiveConfirm, setShowLiveConfirm] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: "success" | "error" } | null>(null);

  const { data: modeData, mutate: mutateMode } = useApi<ModeResponse>(
    "/manage/mode", getMode, POLL,
  );
  const { data: strategies, mutate: mutateStrategies } = useApi<Strategy[]>(
    "/manage/strategies", getStrategies, POLL,
  );
  const { data: exchanges } = useApi<Record<string, ExchangeStatus>>(
    "/manage/exchanges", getExchangeStatus, POLL,
  );
  const { data: settings, mutate: mutateSettings } = useApi<SettingsResponse>(
    "/manage/settings", getSettings, POLL,
  );

  const currentMode = modeData?.mode ?? "paper";

  function showToast(msg: string, type: "success" | "error") {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 3000);
  }

  // Paper→Live: 확인 모달 → 전환
  function handleRequestLive() {
    if (currentMode === "live") return; // already live
    setShowLiveConfirm(true);
  }

  async function handleConfirmLive() {
    setShowLiveConfirm(false);
    try {
      await updateMode("live");
      await mutateMode();
      showToast("실거래 모드로 전환됐습니다", "success");
    } catch {
      showToast("모드 전환 실패. 다시 시도해주세요.", "error");
    }
  }

  // Live→Paper: 즉시 전환 (안전 방향)
  async function handleSwitchPaper() {
    if (currentMode === "paper") return;
    try {
      await updateMode("paper");
      await mutateMode();
      showToast("모의 운용 모드로 전환됐습니다", "success");
    } catch {
      showToast("모드 전환 실패. 다시 시도해주세요.", "error");
    }
  }

  // 전략 토글 — optimistic update
  const handleStrategyToggle = useCallback(async (id: string) => {
    if (!strategies) return;

    // 1. Optimistic: 로컬 상태 즉시 반전
    const optimistic = strategies.map((s) =>
      s.id === id ? { ...s, enabled: !s.enabled } : s
    );
    await mutateStrategies(optimistic, false);

    // 2. API 호출
    try {
      await toggleStrategy(id);
      await mutateStrategies(); // 서버 실제값으로 갱신
    } catch {
      await mutateStrategies(); // 실패 → 서버값으로 롤백
      showToast(ko.error.strategyToggleFailed, "error");
    }
  }, [strategies, mutateStrategies]);

  async function handleSettingsSave(s: Partial<SettingsResponse>) {
    try {
      await updateSettings(s);
      await mutateSettings();
    } catch {
      showToast(ko.error.saveFailed, "error");
      throw s; // CapitalSettings에서 saving 상태 해제
    }
  }

  return (
    <div className="p-4 max-w-5xl mx-auto pb-24 space-y-6">
      <h1 className="text-heading font-bold text-text-primary">{ko.nav.manage}</h1>

      {/* Mode Toggle */}
      <ModeToggle
        mode={currentMode}
        onRequestLive={handleRequestLive}
        onSwitchPaper={handleSwitchPaper}
      />

      {/* 전략 그리드 */}
      <section>
        <h2 className="text-body font-semibold text-text-primary mb-3">
          {ko.manage.strategies}
        </h2>
        {strategies?.length === 0 ? (
          <div className="bg-bg-surface border border-border rounded-[16px] p-8 text-center">
            <p className="text-body text-text-secondary">전략 없음</p>
            <p className="text-small text-text-tertiary mt-1">엔진 시작 후 전략이 표시됩니다</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {strategies
              ? strategies.map((s) => (
                  <StrategyCard key={s.id} strategy={s} onToggle={handleStrategyToggle} />
                ))
              : Array.from({ length: 4 }).map((_, i) => (
                  <div key={i} className="h-36 bg-bg-surface border border-border rounded-[16px] animate-pulse" />
                ))}
          </div>
        )}
      </section>

      {/* 거래소 그리드 */}
      <section>
        <h2 className="text-body font-semibold text-text-primary mb-3">
          {ko.manage.exchanges}
        </h2>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          {exchanges
            ? Object.entries(exchanges).map(([id, s]) => (
                <ExchangeCard key={id} id={id} status={s} />
              ))
            : Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="h-24 bg-bg-surface border border-border rounded-[16px] animate-pulse" />
              ))}
        </div>
      </section>

      {/* 자본 설정 */}
      {settings && (
        <section>
          <CapitalSettings settings={settings} onSave={handleSettingsSave} />
        </section>
      )}

      {/* Paper→Live 확인 모달 */}
      {showLiveConfirm && (
        <LiveConfirmModal
          onConfirm={handleConfirmLive}
          onCancel={() => setShowLiveConfirm(false)}
        />
      )}

      {/* Toast 피드백 */}
      {toast && <Toast msg={toast.msg} type={toast.type} />}
    </div>
  );
}
