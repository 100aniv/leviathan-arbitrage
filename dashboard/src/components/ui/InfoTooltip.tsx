"use client";

import React, { useState, useRef, useEffect } from "react";
import { Info } from "lucide-react";

interface InfoTooltipProps {
  content: string;
  size?: number;
  className?: string;
}

export function InfoTooltip({ content, size = 14, className = "" }: InfoTooltipProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // 외부 클릭 시 닫기
  useEffect(() => {
    if (!open) return;
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  return (
    <div ref={ref} className={`relative inline-flex ${className}`}>
      <button
        type="button"
        aria-label="설명 보기"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        className="inline-flex items-center justify-center w-[22px] h-[22px] rounded-full text-text-tertiary hover:text-brand hover:bg-brand-subtle transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand"
      >
        <Info size={size} aria-hidden />
      </button>

      {open && (
        <div
          role="tooltip"
          className="
            absolute bottom-full left-1/2 -translate-x-1/2 mb-2 z-50
            bg-text-primary text-bg-base text-small rounded-[10px] px-3 py-2
            w-max max-w-[240px] shadow-card pointer-events-none
            animate-fade-in
          "
        >
          {content}
          {/* 화살표 */}
          <div className="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-text-primary" />
        </div>
      )}
    </div>
  );
}
