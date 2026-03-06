'use client';

import { useState } from 'react';
import { ConfirmDialog } from './ui/ConfirmDialog';

type TradingMode = 'paper' | 'live' | 'backtest';

interface ModeSwitchProps {
  initialMode?: TradingMode;
  onChange?: (mode: TradingMode) => void;
}

const MODE_CONFIG: Record<TradingMode, {
  label: string;
  dot: string;
  activeText: string;
  activeBorder: string;
  activeBg: string;
}> = {
  paper: {
    label: 'PAPER',
    dot: 'bg-profit',
    activeText: 'text-profit',
    activeBorder: 'border-profit/50',
    activeBg: 'bg-profit/10',
  },
  live: {
    label: 'LIVE',
    dot: 'bg-loss animate-pulse',
    activeText: 'text-loss',
    activeBorder: 'border-loss/50',
    activeBg: 'bg-loss/10',
  },
  backtest: {
    label: 'BACKTEST',
    dot: 'bg-accent',
    activeText: 'text-accent',
    activeBorder: 'border-accent/50',
    activeBg: 'bg-accent/10',
  },
};

const MODES: TradingMode[] = ['paper', 'live', 'backtest'];

export function ModeSwitch({ initialMode = 'paper', onChange }: ModeSwitchProps) {
  const [mode, setMode] = useState<TradingMode>(initialMode);
  const [pendingMode, setPendingMode] = useState<TradingMode | null>(null);
  const [showConfirm, setShowConfirm] = useState(false);
  const [isSwitching, setIsSwitching] = useState(false);

  const applyMode = async (target: TradingMode) => {
    setIsSwitching(true);
    try {
      // POST mode change to engine
      await fetch(
        `${process.env.NEXT_PUBLIC_ENGINE_URL ?? 'http://localhost:8000'}/mode`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: target }),
        }
      );
      setMode(target);
      onChange?.(target);
    } catch (err) {
      console.error('[ModeSwitch] Failed to switch mode:', err);
    } finally {
      setIsSwitching(false);
    }
  };

  const handleModeClick = (target: TradingMode) => {
    if (target === mode || isSwitching) return;
    if (target === 'live') {
      setPendingMode(target);
      setShowConfirm(true);
    } else {
      applyMode(target);
    }
  };

  const handleConfirm = () => {
    setShowConfirm(false);
    if (pendingMode) {
      applyMode(pendingMode);
      setPendingMode(null);
    }
  };

  const handleCancel = () => {
    setShowConfirm(false);
    setPendingMode(null);
  };

  return (
    <>
      <div
        role="group"
        aria-label="Trading mode"
        className="flex items-stretch border border-terminal-border font-mono"
      >
        {MODES.map((m, i) => {
          const cfg = MODE_CONFIG[m];
          const isActive = mode === m;

          return (
            <button
              key={m}
              onClick={() => handleModeClick(m)}
              disabled={isSwitching}
              aria-pressed={isActive}
              aria-label={`${cfg.label} mode`}
              className={[
                'flex items-center gap-2 px-3 py-2 text-xs uppercase tracking-widest transition-all duration-100 select-none',
                i > 0 ? 'border-l border-terminal-border' : '',
                isActive
                  ? `${cfg.activeBg} ${cfg.activeText} ${cfg.activeBorder}`
                  : `text-terminal-subtle hover:text-terminal-text hover:bg-terminal-muted/40 ${
                      isSwitching ? 'cursor-not-allowed opacity-50' : 'cursor-pointer'
                    }`,
              ].join(' ')}
            >
              <span className={`h-2 w-2 rounded-full flex-shrink-0 ${cfg.dot}`} />
              {cfg.label}
            </button>
          );
        })}
      </div>

      <ConfirmDialog
        isOpen={showConfirm}
        title="Switch to LIVE Trading"
        message="Real orders will be placed using real funds. Confirm you have reviewed all strategy parameters and risk limits before enabling live trading."
        confirmLabel="ENABLE LIVE TRADING"
        cancelLabel="Stay in Paper"
        danger="warning"
        onConfirm={handleConfirm}
        onCancel={handleCancel}
      />
    </>
  );
}
