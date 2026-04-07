"use client";

import React, { useState, useEffect, useCallback } from "react";
import { ShieldOff, X, AlertTriangle } from "lucide-react";
import ko from "@/i18n/ko.json";

const COUNTDOWN_SECONDS = 5;

interface EmergencyStopProps {
  onConfirm: (password: string) => Promise<void>;
  disabled?: boolean;
}

type Phase = "idle" | "confirm" | "countdown" | "executing" | "done";

export function EmergencyStop({ onConfirm, disabled = false }: EmergencyStopProps) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [countdown, setCountdown] = useState(COUNTDOWN_SECONDS);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  function handleCancel() {
    setPhase("idle");
    setCountdown(COUNTDOWN_SECONDS);
    setPassword("");
    setError("");
  }

  // handleExecute 반드시 useEffect 보다 먼저 선언
  const handleExecute = useCallback(async () => {
    setPhase("executing");
    try {
      await onConfirm(password);
      setPhase("done");
    } catch {
      setError("실행 중 오류가 발생했습니다. 다시 시도해 주세요.");
      setPhase("confirm");
    }
  }, [password, onConfirm]);

  // 카운트다운 타이머
  useEffect(() => {
    if (phase !== "countdown") return;
    if (countdown <= 0) {
      handleExecute();
      return;
    }
    const id = setTimeout(() => setCountdown((v) => v - 1), 1000);
    return () => clearTimeout(id);
  }, [phase, countdown, handleExecute]);

  // 키보드 단축키 (Ctrl+Shift+K = 긴급정지 트리거)
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.ctrlKey && e.shiftKey && e.key === "K") {
        e.preventDefault();
        if (phase === "idle") setPhase("confirm");
      }
      if (e.key === "Escape") handleCancel();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [phase]);

  function handleConfirmClick() {
    if (!password) {
      setError("비밀번호를 입력해 주세요.");
      return;
    }
    setError("");
    setPhase("countdown");
  }

  return (
    <>
      {/* 트리거 버튼 */}
      <button
        onClick={() => setPhase("confirm")}
        disabled={disabled || phase !== "idle"}
        aria-label={ko.safety.emergencyStop}
        className="
          flex items-center justify-center gap-2 w-full
          bg-danger text-white font-semibold text-body
          rounded-[12px] px-6 py-4
          hover:bg-[#CC3344] active:scale-[0.98]
          disabled:opacity-50 disabled:cursor-not-allowed
          transition-all duration-150
          focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger focus-visible:ring-offset-2
        "
      >
        <ShieldOff size={20} aria-hidden />
        {ko.safety.emergencyStop}
        <span className="text-small font-normal opacity-75 ml-1">(Ctrl+Shift+K)</span>
      </button>

      {/* 확인 모달 */}
      {(phase === "confirm" || phase === "countdown" || phase === "executing") && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-label="긴급 정지 확인"
        >
          <div className="bg-bg-elevated rounded-[20px] shadow-card border border-border p-6 w-full max-w-sm mx-4">
            {/* 헤더 */}
            <div className="flex items-start justify-between mb-4">
              <div className="flex items-center gap-2">
                <div className="w-10 h-10 rounded-full bg-danger-bg flex items-center justify-center">
                  <AlertTriangle size={20} className="text-danger" aria-hidden />
                </div>
                <div>
                  <h2 className="text-body font-bold text-text-primary">{ko.safety.confirmStop}</h2>
                  <p className="text-caption text-text-secondary mt-0.5">{ko.safety.confirmStopDesc}</p>
                </div>
              </div>
              {phase !== "executing" && (
                <button
                  onClick={handleCancel}
                  aria-label={ko.common.cancel}
                  className="w-8 h-8 flex items-center justify-center rounded-full text-text-secondary hover:bg-bg-surface"
                >
                  <X size={16} aria-hidden />
                </button>
              )}
            </div>

            {/* 비밀번호 입력 */}
            <div className="mb-4">
              <label className="block text-caption text-text-secondary mb-1.5" htmlFor="es-password">
                {ko.safety.enterPassword}
              </label>
              <input
                id="es-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && phase === "confirm" && handleConfirmClick()}
                disabled={phase !== "confirm"}
                autoFocus
                className="
                  w-full rounded-[10px] border border-border bg-bg-surface
                  px-3 py-2.5 text-body text-text-primary
                  focus:outline-none focus:ring-2 focus:ring-danger
                  disabled:opacity-60
                "
                placeholder="••••••••"
              />
              {error && (
                <p className="text-small text-danger mt-1.5">{error}</p>
              )}
            </div>

            {/* 카운트다운 표시 */}
            {phase === "countdown" && (
              <div className="flex items-center justify-center py-3 mb-4 bg-danger-bg rounded-[10px]">
                <span className="text-2xl font-bold text-danger tabular-nums">{countdown}</span>
                <span className="text-body text-danger ml-2">{ko.safety.countdownPrefix}</span>
              </div>
            )}

            {/* 실행 중 */}
            {phase === "executing" && (
              <div className="flex items-center justify-center py-4 mb-4">
                <div className="w-6 h-6 border-2 border-danger border-t-transparent rounded-full animate-spin" aria-label="실행 중" />
                <span className="ml-2 text-body text-danger font-medium">긴급 정지 실행 중...</span>
              </div>
            )}

            {/* 버튼 */}
            {phase === "confirm" && (
              <div className="flex gap-3">
                <button onClick={handleCancel} className="flex-1 btn-secondary text-body py-3">
                  {ko.common.cancel}
                </button>
                <button
                  onClick={handleConfirmClick}
                  className="flex-1 bg-danger text-white font-semibold text-body rounded-[12px] py-3 hover:bg-[#CC3344] transition-colors"
                >
                  {ko.safety.execute}
                </button>
              </div>
            )}
            {phase === "countdown" && (
              <button onClick={handleCancel} className="w-full btn-secondary text-body py-3">
                {ko.safety.cancel} (취소)
              </button>
            )}
          </div>
        </div>
      )}

      {/* 완료 상태 */}
      {phase === "done" && (
        <div className="flex items-center gap-2 text-danger font-medium text-body mt-2">
          <ShieldOff size={16} aria-hidden />
          긴급 정지 완료. 모든 거래가 중단됐습니다.
        </div>
      )}
    </>
  );
}
