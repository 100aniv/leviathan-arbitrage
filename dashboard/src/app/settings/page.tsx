"use client";

import { useEffect, useState } from "react";
import { getSettings, updateSettings, updateMode, toggleStrategy, logout, killEngine } from "@/lib/api";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import type { SettingsResponse } from "@/types";

function InfoTip({ text }: { text: string }) {
  const [show, setShow] = useState(false);
  return (
    <span className="relative inline-flex ml-1">
      <button
        type="button"
        onMouseEnter={() => setShow(true)}
        onMouseLeave={() => setShow(false)}
        onFocus={() => setShow(true)}
        onBlur={() => setShow(false)}
        className="text-terminal-subtle hover:text-accent text-[10px] cursor-help"
        aria-label="Info"
      >
        ⓘ
      </button>
      {show && (
        <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 px-2 py-1 bg-bg-elevated border border-border rounded text-[10px] font-mono text-text-primary whitespace-nowrap z-50 shadow-card">
          {text}
        </span>
      )}
    </span>
  );
}

const ALL_EXCHANGES = [
  "binance", "binance_futures", "bybit", "okx",
  "bitget", "upbit", "bithumb", "coinone",
];

const MODE_LABELS: Record<string, string> = {
  backtest: "Backtest",
  paper: "Paper",
  shadow: "Shadow",
  live: "Live",
};

const MODE_DESCRIPTIONS: Record<string, string> = {
  backtest: "과거 데이터 + SimExecutor — 오프라인 전략 성능 검증",
  paper: "실시간 WS + SimExecutor — 실제 시장 데이터 기반 가상 거래 (주문 없음)",
  shadow: "실시간 WS + Paper — Shadow 모드 실행",
  live: "실시간 WS + AtomicExecutor 전액 — LiveGate 통과 필요",
};

type FeedbackState = { type: "success" | "error"; message: string } | null;

type Dialog =
  | { kind: "emergency-stop" }
  | { kind: "reset-defaults" }
  | { kind: "mode-change"; mode: "backtest" | "paper" | "shadow" | "live" }
  | null;

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [minEdge, setMinEdge]   = useState<number>(5);
  const [maxPosition, setMaxPosition] = useState<number>(5000);
  const [capitalPerExchange, setCapitalPerExchange] = useState<number>(70);
  const [maxDailyLoss, setMaxDailyLoss] = useState<number>(500);
  const [capitalSaving, setCapitalSaving] = useState(false);
  const [modeSaving, setModeSaving] = useState(false);
  const [feedback, setFeedback] = useState<FeedbackState>(null);
  const [saving, setSaving]     = useState(false);
  const [dialog, setDialog]     = useState<Dialog>(null);
  const [dangerLoading, setDangerLoading] = useState(false);

  useEffect(() => {
    getSettings()
      .then((data) => {
        setSettings(data);
        setMinEdge(data.min_edge_bps);
        if (data.max_position_usd != null) setMaxPosition(data.max_position_usd);
        if (data.capital_per_exchange_usd != null) setCapitalPerExchange(data.capital_per_exchange_usd);
        if (data.max_daily_loss_usd != null) setMaxDailyLoss(data.max_daily_loss_usd);
      })
      .catch(() => {
        setFeedback({ type: "error", message: "설정을 불러오지 못했어요." });
      });
  }, []);

  function showFeedback(type: "success" | "error", message: string) {
    setFeedback({ type, message });
    setTimeout(() => setFeedback(null), 3000);
  }

  function handleSelectMode(mode: "backtest" | "paper" | "shadow" | "live") {
    if (settings?.execution_mode === mode) return;
    setDialog({ kind: "mode-change", mode });
  }

  async function confirmModeChange(mode: "backtest" | "paper" | "shadow" | "live") {
    setDialog(null);
    const prevMode = settings?.execution_mode;
    setSettings((prev) => prev ? { ...prev, execution_mode: mode } : prev);
    setModeSaving(true);
    try {
      await updateMode(mode);
      const msg = mode === "live"
        ? `"Live" 모드로 변경되었습니다. 엔진 재시작이 필요합니다.`
        : `실행 모드가 "${MODE_LABELS[mode]}"로 변경되었습니다.`;
      showFeedback("success", msg);
    } catch {
      setSettings((prev) => prev ? { ...prev, execution_mode: prevMode } : prev);
      showFeedback("error", "실행 모드 변경에 실패했습니다.");
    } finally {
      setModeSaving(false);
    }
  }

  async function handleSaveCapital() {
    setCapitalSaving(true);
    try {
      const updated = await updateSettings({
        capital_per_exchange_usd: capitalPerExchange,
        max_position_usd: maxPosition,
        max_daily_loss_usd: maxDailyLoss,
      });
      setSettings((prev) => prev ? { ...prev, ...updated } : prev);
      showFeedback("success", "자본 설정이 저장되었습니다.");
    } catch {
      showFeedback("error", "자본 설정 저장에 실패했습니다.");
    } finally {
      setCapitalSaving(false);
    }
  }

  async function handleSaveEdge() {
    setSaving(true);
    try {
      const updated = await updateSettings({ min_edge_bps: minEdge });
      setSettings((prev) => prev ? { ...prev, min_edge_bps: updated.min_edge_bps } : prev);
      showFeedback("success", "최소 엣지(MIN_EDGE_BPS)가 저장되었습니다.");
    } catch {
      showFeedback("error", "설정 저장에 실패했어요. 잠시 후 다시 시도해 주세요.");
    } finally {
      setSaving(false);
    }
  }

  async function handleToggleStrategy(id: string) {
    try {
      const result = await toggleStrategy(id);
      setSettings((prev) =>
        prev
          ? {
              ...prev,
              active_strategies: prev.active_strategies.map((s) =>
                s.id === id ? { ...s, enabled: result.enabled } : s
              ),
            }
          : prev
      );
      showFeedback("success", `전략이 ${result.enabled ? "활성화" : "비활성화"}되었습니다.`);
    } catch {
      showFeedback("error", "전략 상태 변경에 실패했어요.");
    }
  }

  async function handleExchangeToggle(exchange: string) {
    if (!settings) return;
    const current = settings.active_exchanges;
    const next    = current.includes(exchange)
      ? current.filter((e) => e !== exchange)
      : [...current, exchange];
    try {
      const updated = await updateSettings({ active_exchanges: next });
      setSettings((prev) => prev ? { ...prev, active_exchanges: updated.active_exchanges } : prev);
      showFeedback("success", "거래소 설정이 업데이트되었습니다.");
    } catch {
      showFeedback("error", "거래소 설정 변경에 실패했어요.");
    }
  }

  async function handleEmergencyStop() {
    setDialog(null);
    setDangerLoading(true);
    try {
      await killEngine("Dashboard emergency stop");
      showFeedback("success", "긴급 정지가 활성화되었습니다. 엔진이 중단되었습니다.");
    } catch {
      showFeedback("error", "킬스위치 API 오류 — 엔진을 직접 확인해 주세요.");
    } finally {
      setDangerLoading(false);
    }
  }

  async function handleResetDefaults() {
    setDialog(null);
    setDangerLoading(true);
    try {
      const updated = await updateSettings({ min_edge_bps: 5, active_exchanges: ALL_EXCHANGES });
      setSettings((prev) => prev ? { ...prev, ...updated } : prev);
      setMinEdge(updated.min_edge_bps);
      showFeedback("success", "설정이 기본값으로 초기화되었습니다.");
    } catch {
      showFeedback("error", "초기화에 실패했어요. 잠시 후 다시 시도해 주세요.");
    } finally {
      setDangerLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-mono font-semibold text-terminal-text">설정</h2>
        <p className="text-xs font-mono text-terminal-subtle mt-1">
          LEVIATHAN 차익거래 엔진 런타임 설정
        </p>
      </div>

      {/* Feedback */}
      {feedback && (
        <div className={`px-4 py-2 rounded font-mono text-xs border ${
          feedback.type === "success"
            ? "bg-profit/10 border-profit/30 text-profit"
            : "bg-loss/10 border-loss/30 text-loss"
        }`}>
          {feedback.message}
        </div>
      )}

      {/* Execution Mode */}
      <section className="bg-terminal-surface border border-terminal-border rounded-lg p-5 space-y-4">
        <h3 className="text-sm font-mono font-semibold text-terminal-text">
          실행 모드<InfoTip text="엔진의 거래 실행 방식을 선택합니다" />
        </h3>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {(["backtest", "paper", "shadow", "live"] as const).map((mode) => {
            const active = settings?.execution_mode === mode;
            const isLive = mode === "live";
            return (
              <button
                key={mode}
                onClick={() => handleSelectMode(mode)}
                disabled={modeSaving}
                className={`flex flex-col gap-1 p-4 rounded border text-left transition-colors disabled:opacity-50 ${
                  active
                    ? "border-accent bg-accent/10 text-terminal-text"
                    : "border-terminal-border hover:border-accent/50 text-terminal-subtle hover:text-terminal-text"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className="text-sm font-mono font-semibold">
                    {MODE_LABELS[mode]}
                  </span>
                  {isLive && (
                    <span className="text-[10px] font-mono text-warn border border-warn/40 rounded px-1">
                      ⚠ LiveGate 필요
                    </span>
                  )}
                  {active && (
                    <span className="ml-auto text-[10px] font-mono text-accent">● 활성</span>
                  )}
                </div>
                <span className="text-[10px] font-mono text-terminal-subtle leading-relaxed">
                  {MODE_DESCRIPTIONS[mode]}
                </span>
              </button>
            );
          })}
        </div>
      </section>

      {/* 자본 설정 */}
      <section className="bg-terminal-surface border border-terminal-border rounded-lg p-5 space-y-4">
        <h3 className="text-sm font-mono font-semibold text-terminal-text">자본 설정</h3>
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <label htmlFor="capital-per-exchange" className="text-xs font-mono text-terminal-subtle w-40 shrink-0">
              거래소당 자본 ($)<InfoTip text="거래소당 할당 자본 (alpha 기본값 $70)" />
            </label>
            <input
              id="capital-per-exchange"
              type="number"
              min={1}
              value={capitalPerExchange}
              onChange={(e) => setCapitalPerExchange(Number(e.target.value))}
              className="w-28 bg-terminal-muted border border-terminal-border rounded px-2 py-1 text-sm font-mono text-terminal-text focus:outline-none focus:border-accent"
            />
          </div>
          <div className="flex items-center gap-3">
            <label htmlFor="max-position-capital" className="text-xs font-mono text-terminal-subtle w-40 shrink-0">
              최대 포지션 ($)<InfoTip text="단일 포지션 최대 규모" />
            </label>
            <input
              id="max-position-capital"
              type="number"
              min={1}
              value={maxPosition}
              onChange={(e) => setMaxPosition(Number(e.target.value))}
              className="w-28 bg-terminal-muted border border-terminal-border rounded px-2 py-1 text-sm font-mono text-terminal-text focus:outline-none focus:border-accent"
            />
          </div>
          <div className="flex items-center gap-3">
            <label htmlFor="max-daily-loss" className="text-xs font-mono text-terminal-subtle w-40 shrink-0">
              최대 일일 손실 ($)<InfoTip text="일일 최대 허용 손실액 초과 시 Kill Switch 발동" />
            </label>
            <input
              id="max-daily-loss"
              type="number"
              min={1}
              value={maxDailyLoss}
              onChange={(e) => setMaxDailyLoss(Number(e.target.value))}
              className="w-28 bg-terminal-muted border border-terminal-border rounded px-2 py-1 text-sm font-mono text-terminal-text focus:outline-none focus:border-accent"
            />
          </div>
        </div>
        <button
          onClick={handleSaveCapital}
          disabled={capitalSaving}
          className="px-3 py-1 text-xs font-mono rounded border border-accent/40 text-accent hover:bg-accent/10 disabled:opacity-40 transition-colors"
        >
          {capitalSaving ? "저장 중…" : "저장"}
        </button>
      </section>

      {/* Trading Parameters */}
      <section className="bg-terminal-surface border border-terminal-border rounded-lg p-5 space-y-4">
        <h3 className="text-sm font-mono font-semibold text-terminal-text">거래 파라미터</h3>
        <div className="flex items-center gap-3">
          <label htmlFor="min-edge-bps" className="text-xs font-mono text-terminal-subtle w-36 shrink-0">
            최소 수익 기준 (BPS)<InfoTip text="거래 실행을 위한 최소 스프레드 (basis points)" />
          </label>
          <input
            id="min-edge-bps"
            type="number"
            min={1}
            max={1000}
            value={minEdge}
            onChange={(e) => setMinEdge(Number(e.target.value))}
            className="w-24 bg-terminal-muted border border-terminal-border rounded px-2 py-1 text-sm font-mono text-terminal-text focus:outline-none focus:border-accent"
          />
        </div>
        <div className="flex items-center gap-3">
          <label htmlFor="max-position-usd" className="text-xs font-mono text-terminal-subtle w-36 shrink-0">
            최대 포지션 USD<InfoTip text="최대 단일 포지션 규모 (USD)" />
          </label>
          <input
            id="max-position-usd"
            type="number"
            min={1}
            value={maxPosition}
            onChange={(e) => setMaxPosition(Number(e.target.value))}
            className="w-24 bg-terminal-muted border border-terminal-border rounded px-2 py-1 text-sm font-mono text-terminal-text focus:outline-none focus:border-accent"
            readOnly
          />
          <span className="text-[10px] font-mono text-terminal-subtle">&ldquo;자본 설정&rdquo;에서 관리</span>
        </div>
        <button
          onClick={handleSaveEdge}
          disabled={saving}
          className="px-3 py-1 text-xs font-mono rounded border border-accent/40 text-accent hover:bg-accent/10 disabled:opacity-40 transition-colors"
        >
          {saving ? "저장 중…" : "저장"}
        </button>
      </section>

      {/* Strategy Control */}
      <section className="bg-terminal-surface border border-terminal-border rounded-lg p-5 space-y-4">
        <h3 className="text-sm font-mono font-semibold text-terminal-text">전략 관리</h3>
        {!settings ? (
          <p className="text-xs font-mono text-terminal-subtle">Loading…</p>
        ) : (
          <div className="space-y-2">
            {settings.active_strategies.map((s) => (
              <div key={s.id} className="flex items-center justify-between">
                <div>
                  <span className="text-sm font-mono text-terminal-text">{s.type}<InfoTip text="전략 활성화/비활성화" /></span>
                  <span className="ml-2 text-[10px] font-mono text-terminal-subtle">{s.id}</span>
                </div>
                <button
                  role="switch"
                  aria-checked={s.enabled}
                  onClick={() => handleToggleStrategy(s.id)}
                  className={`relative w-10 h-5 rounded-full transition-colors ${
                    s.enabled ? "bg-profit/70" : "bg-terminal-muted border border-terminal-border"
                  }`}
                >
                  <span
                    className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-text-primary transition-transform ${
                      s.enabled ? "translate-x-5" : "translate-x-0"
                    }`}
                  />
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Exchange Selection */}
      <section className="bg-terminal-surface border border-terminal-border rounded-lg p-5 space-y-4">
        <h3 className="text-sm font-mono font-semibold text-terminal-text">거래소 선택<InfoTip text="활성화할 거래소를 선택합니다" /></h3>
        {!settings ? (
          <p className="text-xs font-mono text-terminal-subtle">Loading…</p>
        ) : (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {ALL_EXCHANGES.map((ex) => {
              const active = settings.active_exchanges.includes(ex);
              return (
                <label
                  key={ex}
                  className={`flex items-center gap-2 px-3 py-2 rounded border cursor-pointer transition-colors ${
                    active
                      ? "border-accent/40 bg-accent/5 text-terminal-text"
                      : "border-terminal-border text-terminal-subtle hover:border-terminal-text/30"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={active}
                    onChange={() => handleExchangeToggle(ex)}
                    className="sr-only"
                  />
                  <span
                    className={`w-3 h-3 rounded-sm border flex-shrink-0 flex items-center justify-center ${
                      active ? "bg-accent border-accent" : "border-terminal-subtle"
                    }`}
                  >
                    {active && (
                      <svg viewBox="0 0 10 10" className="w-2 h-2 fill-bg-elevated">
                        <path d="M1.5 5l2.5 2.5 4.5-4.5" stroke="currentColor" strokeWidth="1.5" fill="none" />
                      </svg>
                    )}
                  </span>
                  <span className="text-xs font-mono">{ex}</span>
                </label>
              );
            })}
          </div>
        )}
      </section>

      {/* Account */}
      <section className="bg-terminal-surface border border-terminal-border rounded-lg p-5 space-y-4">
        <h3 className="text-sm font-mono font-semibold text-terminal-text">계정</h3>
        <button
          onClick={logout}
          className="px-4 py-2 text-xs font-mono rounded border border-loss/40 text-loss hover:bg-loss/10 transition-colors"
        >
          로그아웃
        </button>
      </section>

      {/* Danger Zone */}
      <section className="border-2 border-loss/50 rounded-lg p-5 space-y-4 bg-loss/5">
        <div>
          <h3 className="text-sm font-mono font-semibold text-loss uppercase tracking-[0.15em]">
            ⚠ Danger Zone
          </h3>
          <p className="text-[10px] font-mono text-terminal-subtle mt-1">
            복구 불가 작업입니다. 신중하게 실행하세요.
          </p>
        </div>

        <div className="flex flex-col sm:flex-row gap-3">
          {/* 비상 정지 */}
          <div className="flex-1 bg-terminal-surface border border-loss/30 rounded p-4 space-y-2">
            <p className="text-xs font-mono font-semibold text-terminal-text">비상 정지</p>
            <p className="text-[10px] font-mono text-terminal-subtle">
              Kill Switch를 즉시 활성화합니다. 모든 거래가 즉시 중단됩니다.
            </p>
            <button
              onClick={() => setDialog({ kind: "emergency-stop" })}
              disabled={dangerLoading}
              className="mt-1 px-4 py-2 text-xs font-mono rounded border border-loss/60 text-loss hover:bg-loss/15 disabled:opacity-40 transition-colors"
            >
              {dangerLoading ? "처리 중…" : "비상 정지"}
            </button>
          </div>

          {/* Reset Defaults */}
          <div className="flex-1 bg-terminal-surface border border-warn/30 rounded p-4 space-y-2">
            <p className="text-xs font-mono font-semibold text-terminal-text">Reset Defaults</p>
            <p className="text-[10px] font-mono text-terminal-subtle">
              모든 설정을 기본값으로 초기화합니다 (MIN_EDGE=5bps, 전체 거래소).
            </p>
            <button
              onClick={() => setDialog({ kind: "reset-defaults" })}
              disabled={dangerLoading}
              className="mt-1 px-4 py-2 text-xs font-mono rounded border border-warn/50 text-warn hover:bg-warn/10 disabled:opacity-40 transition-colors"
            >
              {dangerLoading ? "처리 중…" : "Reset Defaults"}
            </button>
          </div>
        </div>
      </section>

      {/* Confirm Dialogs */}
      <ConfirmDialog
        isOpen={dialog?.kind === "emergency-stop"}
        title="비상 정지"
        message="Kill Switch를 즉시 활성화합니다. 엔진의 모든 거래 실행이 즉시 중단됩니다. 재시작하려면 엔진을 수동으로 재부팅해야 합니다."
        confirmLabel="지금 중단"
        cancelLabel="취소"
        danger="critical"
        onConfirm={handleEmergencyStop}
        onCancel={() => setDialog(null)}
      />
      <ConfirmDialog
        isOpen={dialog?.kind === "reset-defaults"}
        title="Reset Defaults"
        message="모든 설정을 공장 기본값으로 초기화합니다. MIN_EDGE_BPS=5, 전체 거래소 활성화. 커스터마이즈된 설정이 모두 삭제됩니다."
        confirmLabel="초기화"
        cancelLabel="취소"
        danger="warning"
        onConfirm={handleResetDefaults}
        onCancel={() => setDialog(null)}
      />
      <ConfirmDialog
        isOpen={dialog?.kind === "mode-change"}
        title={`모드 전환: ${dialog?.kind === "mode-change" ? MODE_LABELS[dialog.mode] : ""}`}
        message={
          dialog?.kind === "mode-change" && dialog.mode === "live"
            ? `실행 모드를 "Live"로 전환합니다.\n\n⚠ 엔진 재시작이 필요합니다. 2~5초 다운타임이 발생하며, LiveGate 조건을 통과해야 거래가 시작됩니다.\n\n실제 자본으로 거래가 실행됩니다. 계속하시겠습니까?`
            : `실행 모드를 "${dialog?.kind === "mode-change" ? MODE_LABELS[dialog.mode] : ""}"로 전환합니다.\n\n엔진 재시작이 필요합니다. 계속하시겠습니까?`
        }
        confirmLabel="전환"
        cancelLabel="취소"
        danger={dialog?.kind === "mode-change" && dialog.mode === "live" ? "critical" : "warning"}
        onConfirm={() => dialog?.kind === "mode-change" && confirmModeChange(dialog.mode)}
        onCancel={() => setDialog(null)}
      />
    </div>
  );
}
