'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

interface ParameterSliderProps {
  parameterId: string;
  label: string;
  description?: string;
  unit?: string;
  min: number;
  max: number;
  step?: number;
  initialValue: number;
  onChange?: (id: string, value: number) => void;
}

const BASE_URL = process.env.NEXT_PUBLIC_ENGINE_URL ?? 'http://localhost:8000';
const DEBOUNCE_MS = 300;

export function ParameterSlider({
  parameterId,
  label,
  description,
  unit = '',
  min,
  max,
  step = 1,
  initialValue,
  onChange,
}: ParameterSliderProps) {
  const [value, setValue] = useState(initialValue);
  const [isSyncing, setIsSyncing] = useState(false);
  const [syncError, setSyncError] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const sendUpdate = useCallback(
    async (newValue: number) => {
      setIsSyncing(true);
      setSyncError(false);
      try {
        const token = typeof localStorage !== 'undefined' ? localStorage.getItem('leviathan_token') : null;
        const res = await fetch(`${BASE_URL}/parameters/${parameterId}`, {
          method: 'PATCH',
          headers: {
            'Content-Type': 'application/json',
            ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({ value: newValue }),
        });
        if (!res.ok) setSyncError(true);
        else onChange?.(parameterId, newValue);
      } catch {
        setSyncError(true);
      } finally {
        setIsSyncing(false);
      }
    },
    [parameterId, onChange]
  );

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newValue = parseFloat(e.target.value);
    setValue(newValue);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => sendUpdate(newValue), DEBOUNCE_MS);
  };

  // Cleanup debounce on unmount
  useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current); }, []);

  const pct = ((value - min) / (max - min)) * 100;

  return (
    <div className="font-mono">
      {/* Header */}
      <div className="flex items-baseline justify-between mb-2">
        <div className="flex items-baseline gap-2">
          <span className="text-xs text-terminal-subtle uppercase tracking-wider">{label}</span>
          {description && (
            <span className="text-[10px] text-terminal-subtle/60">{description}</span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          {isSyncing && <span className="text-[10px] text-warn animate-blink">SYNC</span>}
          {syncError && <span className="text-[10px] text-loss">ERR</span>}
          <span className="text-sm text-terminal-text tabular-nums">
            {value}
            {unit && <span className="text-xs text-terminal-subtle ml-0.5">{unit}</span>}
          </span>
        </div>
      </div>

      {/* Track + thumb */}
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={handleChange}
        aria-label={`${label}${unit ? ` (${unit})` : ''}`}
        aria-valuemin={min}
        aria-valuemax={max}
        aria-valuenow={value}
        className="w-full h-px appearance-none cursor-pointer
          [&::-webkit-slider-thumb]:appearance-none
          [&::-webkit-slider-thumb]:w-3
          [&::-webkit-slider-thumb]:h-3
          [&::-webkit-slider-thumb]:rounded-none
          [&::-webkit-slider-thumb]:bg-terminal-text
          [&::-webkit-slider-thumb]:hover:bg-white
          [&::-webkit-slider-thumb]:cursor-pointer
          [&::-webkit-slider-thumb]:transition-colors"
        style={{
          background: `linear-gradient(to right, #00ff88 0%, #00ff88 ${pct}%, #2a303a ${pct}%, #2a303a 100%)`,
        }}
      />

      {/* Min / Max */}
      <div className="flex justify-between mt-1.5">
        <span className="text-[10px] text-terminal-subtle/50 tabular-nums">{min}{unit}</span>
        <span className="text-[10px] text-terminal-subtle/50 tabular-nums">{max}{unit}</span>
      </div>
    </div>
  );
}
