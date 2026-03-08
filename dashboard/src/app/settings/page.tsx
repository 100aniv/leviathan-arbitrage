"use client";

import { useEffect, useState } from "react";
import { getSettings, updateSettings, toggleStrategy, logout } from "@/lib/api";
import type { SettingsResponse } from "@/types";

const ALL_EXCHANGES = [
  "binance",
  "binance_futures",
  "bybit",
  "okx",
  "bitget",
  "upbit",
  "bithumb",
  "coinone",
];

type FeedbackState = { type: "success" | "error"; message: string } | null;

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [minEdge, setMinEdge] = useState<number>(5);
  const [feedback, setFeedback] = useState<FeedbackState>(null);
  const [saving, setSaving] = useState(false);

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
    const next = current.includes(exchange)
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

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-mono font-semibold text-terminal-text">Settings</h2>
        <p className="text-xs font-mono text-terminal-subtle mt-1">
          Runtime configuration for the LEVIATHAN arbitrage engine
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
        <h3 className="text-sm font-mono font-semibold text-terminal-text">Trading Parameters</h3>
        <div className="flex items-center gap-3">
          <label className="text-xs font-mono text-terminal-subtle w-36 shrink-0">
            MIN_EDGE_BPS
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
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </section>

      {/* Strategy Control */}
      <section className="bg-terminal-surface border border-terminal-border rounded-lg p-5 space-y-4">
        <h3 className="text-sm font-mono font-semibold text-terminal-text">Strategy Control</h3>
        {!settings ? (
          <p className="text-xs font-mono text-terminal-subtle">Loading…</p>
        ) : (
          <div className="space-y-2">
            {settings.active_strategies.map((s) => (
              <div key={s.id} className="flex items-center justify-between">
                <div>
                  <span className="text-sm font-mono text-terminal-text">{s.type}</span>
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
        <h3 className="text-sm font-mono font-semibold text-terminal-text">Exchange Selection</h3>
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
        <h3 className="text-sm font-mono font-semibold text-terminal-text">Account</h3>
        <button
          onClick={logout}
          className="px-4 py-2 text-xs font-mono rounded border border-loss/40 text-loss hover:bg-loss/10 transition-colors"
        >
          Logout
        </button>
      </section>
    </div>
  );
}
