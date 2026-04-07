'use client';

import { useEffect, useRef } from 'react';

interface ConfirmDialogProps {
  isOpen: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: 'warning' | 'critical';
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  isOpen,
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  danger = 'warning',
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel();
      if (e.key === 'Enter') onConfirm();
    };
    document.addEventListener('keydown', handleKey);
    const t = setTimeout(() => confirmRef.current?.focus(), 50);
    return () => {
      document.removeEventListener('keydown', handleKey);
      clearTimeout(t);
    };
  }, [isOpen, onConfirm, onCancel]);

  if (!isOpen) return null;

  const isCritical = danger === 'critical';

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-title"
      className="fixed inset-0 z-50 flex items-center justify-center"
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/80 backdrop-blur-sm"
        onClick={onCancel}
      />

      {/* Panel */}
      <div className={`relative z-10 w-full max-w-md border font-mono bg-terminal-surface ${
        isCritical ? 'border-loss/60' : 'border-warn/60'
      }`}>
        {/* Accent bar */}
        <div className={`h-px w-full ${isCritical ? 'bg-loss' : 'bg-warn'}`} />

        <div className="p-6">
          <p
            id="confirm-title"
            className={`mb-3 text-xs uppercase tracking-[0.25em] ${
              isCritical ? 'text-loss' : 'text-warn'
            }`}
          >
            {title}
          </p>

          <p className="mb-6 text-sm text-terminal-text leading-relaxed">
            {message}
          </p>

          <div className="flex gap-3 justify-end">
            <button
              onClick={onCancel}
              className="px-4 py-2 text-xs uppercase tracking-widest border border-terminal-border text-terminal-subtle hover:text-terminal-text hover:border-terminal-muted transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2"
            >
              {cancelLabel}
            </button>
            <button
              ref={confirmRef}
              onClick={onConfirm}
              className={`px-4 py-2 text-xs uppercase tracking-widest border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 ${
                isCritical
                  ? 'border-loss/70 bg-loss/10 text-loss hover:bg-loss/20 hover:border-loss focus-visible:ring-loss'
                  : 'border-warn/70 bg-warn/10 text-warn hover:bg-warn/20 hover:border-warn focus-visible:ring-warn'
              }`}
            >
              {confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
