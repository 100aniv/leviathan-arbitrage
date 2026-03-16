"use client";

import { useEffect, useState } from "react";
import { getSettings, updateSettings, toggleStrategy, logout, killEngine } from "@/lib/api";
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
        <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 px-2 py-1 bg-terminal-bg border border-terminal-border rounded text-[10px] font-mono text-terminal-text whitespace-nowrap z-50 shadow-lg">
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

type FeedbackState = { type: "success" | "error"; message: string } | null;

type Dialog =
  | { kind: "emergency-stop" }
  | { kind: "reset-defaults" }
  | null;

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [minEdge, setMinEdge]   = useState<number>(5);
  const [feedback, setFeedback] = useState<FeedbackState>(null);
  const [saving, setSaving]     = useState(false);
  const [dialog, setDialog]     = useState<Dialog>(null);
  const [dangerLoading, setDangerLoading] = useState(false);

  useEffect(() => {
    getSettings()
      .then((data) => {
        setSettings(data);
        setMinEdge(data.min_edge_bps);
      })
      .catch(() => {
        setFeedback({ type: "error", message: "Failed to load settings." });
      });
  }, []);

  function showFeedback(type: "success" | "error", message: string) {
    setFeedback({ type, message });
    setTimeout(() => setFeedback(null), 3000);
  }

  async function handleSaveEdge() {
    setSaving(true);
    try {
      const updated = await updateSettings({ min_edge_bps: minEdge });
      setSettings((prev) => prev ? { ...prev, min_edge_bps: updated.min_edge_bps } : prev);
      showFeedback("success", "Saved MIN_EDGE_BPS.");
    } catch {
      showFeedback("error", "Failed to save settings.");
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
      showFeedback("success", `Strategy ${result.enabled ? "enabled" : "disabled"}.`);
    } catch {
      showFeedback("error", "Failed to toggle strategy.");
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
      showFeedback("success", "Exchanges updated.");
    } catch {
      showFeedback("error", "Failed to update exchanges.");
    }
  }

  async function handleEmergencyStop() {
    setDialog(null);
    setDangerLoading(true);
    try {
      await killEngine("Dashboard emergency stop");
      showFeedback("success", "Emergency stop activated. Engine halted.");
    } catch {
      showFeedback("error", "Kill switch API error — check engine directly.");
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
      showFeedback("success", "Settings reset to defaults.");
    } catch {
      showFeedback("error", "Failed to reset settings.");
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
        <div
          className={`px-4 py-2 rounded font-mono text-xs border ${
            feedback.type === "success"
              ? "bg-profit/10 border-profit/30 text-profit"
              : "bg-loss/10 border-loss/30 text-loss"
          }`}
        >
          {feedback.message}
        </div>
      )}

      {/* Trading Parameters */}
      <section className="bg-terminal-surface border border-terminal-border rounded-lg p-5 space-y-4">
        <h3 className="text-sm font-mono font-semibold text-terminal-text">거래 파라미터</h3>
        <div className="flex items-center gap-3">
          <label className="text-xs font-mono text-terminal-subtle w-36 shrink-0">
            최소 수익 기준 (BPS)<InfoTip text="거래 실행을 위한 최소 스프레드 (basis points)" />
          </label>
          <input
            type="number"
            min={1}
            max={1000}
            value={minEdge}
            onChange={(e) => setMinEdge(Number(e.target.value))}
            className="w-24 bg-terminal-muted border border-terminal-border rounded px-2 py-1 text-sm font-mono text-terminal-text focus:outline-none focus:border-accent"
          />
          <button
            onClick={handleSaveEdge}
            disabled={saving}
            className="px-3 py-1 text-xs font-mono rounded border border-accent/40 text-accent hover:bg-accent/10 disabled:opacity-40 transition-colors"
          >
            {saving ? "저장 중…" : "저장"}
          </button>
        </div>
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
                    className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-terminal-text transition-transform ${
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
                      <svg viewBox="0 0 10 10" className="w-2 h-2 fill-terminal-bg">
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

      {/* ── Danger Zone ─────────────────────────────────────────────────────── */}
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
          {/* Emergency Stop */}
          <div className="flex-1 bg-terminal-surface border border-loss/30 rounded p-4 space-y-2">
            <p className="text-xs font-mono font-semibold text-terminal-text">Emergency Stop</p>
            <p className="text-[10px] font-mono text-terminal-subtle">
              Kill Switch를 즉시 활성화합니다. 모든 거래가 즉시 중단됩니다.
            </p>
            <button
              onClick={() => setDialog({ kind: "emergency-stop" })}
              disabled={dangerLoading}
              className="mt-1 px-4 py-2 text-xs font-mono rounded border border-loss/60 text-loss hover:bg-loss/15 disabled:opacity-40 transition-colors"
            >
              {dangerLoading ? "처리 중…" : "Emergency Stop"}
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
        title="Emergency Stop"
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
    </div>
  );
}
