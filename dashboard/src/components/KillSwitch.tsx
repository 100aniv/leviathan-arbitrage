'use client';

import { useState } from 'react';
import { killEngine } from '@/lib/api';
import { ConfirmDialog } from './ui/ConfirmDialog';

interface KillSwitchProps {
  isActive?: boolean;
  onKill?: () => void;
}

export function KillSwitch({ isActive: initialActive = false, onKill }: KillSwitchProps) {
  const [isActive, setIsActive] = useState(initialActive);
  const [showConfirm, setShowConfirm] = useState(false);
  const [isPending, setIsPending] = useState(false);

  // First click — open confirmation modal
  const handleClick = () => {
    if (isActive || isPending) return;
    setShowConfirm(true);
  };

  // Second click — confirmed: POST /kill
  const handleConfirm = async () => {
    setShowConfirm(false);
    setIsPending(true);
    try {
      await killEngine('manual kill switch');
      setIsActive(true);
      onKill?.();
    } catch (err) {
      console.error('[KillSwitch] POST /kill failed:', err);
    } finally {
      setIsPending(false);
    }
  };

  return (
    <>
      {/* Full-width SYSTEM HALTED banner */}
      {isActive && (
        <div
          role="alert"
          className="fixed top-0 inset-x-0 z-50 flex items-center justify-center gap-3 border-b border-loss bg-loss/10 px-4 py-2.5 font-mono text-xs uppercase tracking-[0.3em] text-loss"
        >
          <span className="h-2 w-2 rounded-full bg-loss animate-pulse" />
          SYSTEM HALTED — ALL TRADING SUSPENDED
          <span className="h-2 w-2 rounded-full bg-loss animate-pulse" />
        </div>
      )}

      {/* Kill switch button */}
      <button
        onClick={handleClick}
        disabled={isActive || isPending}
        aria-label={isActive ? 'Kill switch active — system halted' : 'Activate emergency stop'}
        className={[
          'relative flex items-center gap-2.5 px-5 py-2 border font-mono text-xs uppercase tracking-widest',
          'transition-all duration-100 select-none',
          isActive
            ? 'border-loss bg-loss/20 text-loss cursor-not-allowed'
            : isPending
            ? 'border-loss/60 bg-loss/10 text-loss/70 animate-pulse cursor-wait'
            : 'border-loss/30 bg-loss/5 text-loss/80 hover:bg-loss/15 hover:border-loss/70 hover:text-loss active:scale-95 cursor-pointer',
        ].join(' ')}
      >
        <span
          className={[
            'h-2.5 w-2.5 rounded-full flex-shrink-0',
            isActive ? 'bg-loss' : isPending ? 'bg-loss/60 animate-ping' : 'bg-loss/60',
          ].join(' ')}
        />
        {isActive ? 'HALTED' : isPending ? 'HALTING...' : 'KILL SWITCH'}
      </button>

      <ConfirmDialog
        isOpen={showConfirm}
        title="⚠ 긴급 정지"
        message="모든 거래가 즉시 중단됩니다. 오픈 포지션은 유지되며, 재개하려면 수동 재시작이 필요합니다."
        confirmLabel="거래 전체 중단"
        cancelLabel="취소"
        danger="critical"
        onConfirm={handleConfirm}
        onCancel={() => setShowConfirm(false)}
      />
    </>
  );
}
