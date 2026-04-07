'use client';

import { useState } from 'react';
import { fetchApi } from '@/lib/api';

const MODE_CONFIG: Record<string, { en: string; ko: string; color: string }> = {
  backtest: { en: 'BACKTEST', ko: '백테스트', color: 'text-terminal-subtle' },
  paper:    { en: 'PAPER',    ko: '페이퍼',   color: 'text-accent' },
  live:     { en: 'LIVE',     ko: '실거래',   color: 'text-loss' },
};

interface ModeSwitchProps {
  currentMode: string;
}

export function ModeSwitch({ currentMode }: ModeSwitchProps) {
  const [mode, setMode]               = useState(currentMode || 'paper');
  const [showConfirm, setShowConfirm] = useState(false);
  const [pendingMode, setPendingMode] = useState<string | null>(null);
  const [error, setError]             = useState<string | null>(null);
  const [loading, setLoading]         = useState(false);

  const handleModeChange = async (newMode: string) => {
    if (newMode === mode) return;
    if (newMode === 'live') {
      setPendingMode(newMode);
      setShowConfirm(true);
      return;
    }
    await switchMode(newMode);
  };

  const switchMode = async (targetMode: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchApi('/api/v1/settings/mode', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: targetMode }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        if (res.status === 403) {
          const detail = data.detail ?? data.error ?? 'LiveGate 조건 미충족';
          setError(`LiveGate 실패: ${detail}`);
        } else {
          setError(data.error ?? data.detail ?? 'Mode switch failed');
        }
        return;
      }
      setMode(targetMode);
    } catch {
      setError('Network error');
    } finally {
      setLoading(false);
      setShowConfirm(false);
      setPendingMode(null);
    }
  };

  return (
    <div className="relative">
      <div className="flex items-center gap-2">
        {Object.entries(MODE_CONFIG).map(([key, { en, ko, color }]) => (
          <button
            key={key}
            onClick={() => handleModeChange(key)}
            disabled={loading}
            className={`px-2 py-0.5 text-[10px] font-mono tracking-wider border transition-colors disabled:opacity-50 flex flex-col items-center leading-none ${
              key === mode
                ? `${color} border-current bg-current/10`
                : 'text-terminal-subtle border-terminal-border hover:border-terminal-text/30'
            }`}
          >
            <span className="uppercase">{en}</span>
            <span className="text-[8px] opacity-70 mt-0.5">{ko}</span>
          </button>
        ))}
      </div>

      {error && (
        <div className="absolute top-full left-0 mt-1 p-2 bg-loss/10 border border-loss text-loss text-[10px] font-mono z-50 max-w-xs">
          {error}
        </div>
      )}

      {showConfirm && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="mode-confirm-title"
          className="fixed inset-0 bg-black/80 flex items-center justify-center z-50"
        >
          <div className="bg-terminal-surface border border-loss p-6 max-w-sm">
            <h3 id="mode-confirm-title" className="text-sm font-mono text-loss font-bold mb-2">⚠ 실거래 모드 전환</h3>
            <p className="text-xs font-mono text-terminal-text mb-1">
              실제 자금으로 거래를 시작합니다.
            </p>
            <p className="text-xs font-mono text-terminal-subtle mb-4">
              LiveGate 체크를 통과해야 활성화됩니다.
            </p>
            <div className="flex gap-2">
              <button
                onClick={() => switchMode(pendingMode!)}
                disabled={loading}
                aria-label="실거래 모드 전환 확인"
                className="px-3 py-1 text-xs font-mono bg-loss text-white hover:bg-loss/80 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-loss focus-visible:ring-offset-2"
              >
                {loading ? '확인 중...' : '전환 확인'}
              </button>
              <button
                onClick={() => { setShowConfirm(false); setPendingMode(null); }}
                aria-label="모드 전환 취소"
                className="px-3 py-1 text-xs font-mono border border-terminal-border text-terminal-subtle hover:text-terminal-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2"
              >
                취소
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
