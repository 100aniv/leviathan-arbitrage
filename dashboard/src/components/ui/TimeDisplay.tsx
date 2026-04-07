"use client";

import React, { useEffect, useState } from "react";
import ko from "@/i18n/ko.json";

interface TimeDisplayProps {
  timestamp: string | number | Date | null | undefined;
  relative?: boolean;     // true: "3분 전", false: "2026-04-07 12:34 KST"
  showTooltip?: boolean;  // 상대 시간에 절대 시간 툴팁
  className?: string;
}

function toKSTString(date: Date): string {
  return date.toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }) + " KST";
}

function toRelative(date: Date, now: Date): string {
  const diffMs = now.getTime() - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHr  = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHr / 24);

  if (diffSec < 60)  return ko.time.justNow;
  if (diffMin < 60)  return `${diffMin}${ko.time.minutesAgo}`;
  if (diffHr  < 24)  return `${diffHr}${ko.time.hoursAgo}`;
  return `${diffDay}${ko.time.daysAgo}`;
}

export function TimeDisplay({
  timestamp,
  relative = true,
  showTooltip = true,
  className = "",
}: TimeDisplayProps) {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    if (!relative) return;
    const id = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(id);
  }, [relative]);

  if (!timestamp) return <span className={`text-text-tertiary ${className}`}>—</span>;

  const date = timestamp instanceof Date ? timestamp : new Date(timestamp);
  if (isNaN(date.getTime())) return <span className={`text-text-tertiary ${className}`}>—</span>;

  const absoluteStr = toKSTString(date);

  if (!relative) {
    return (
      <time dateTime={date.toISOString()} className={`text-text-secondary ${className}`}>
        {absoluteStr}
      </time>
    );
  }

  const relativeStr = toRelative(date, now);

  if (!showTooltip) {
    return (
      <time dateTime={date.toISOString()} className={`text-text-secondary ${className}`}>
        {relativeStr}
      </time>
    );
  }

  return (
    <time
      dateTime={date.toISOString()}
      title={absoluteStr}
      className={`text-text-secondary cursor-default ${className}`}
    >
      {relativeStr}
    </time>
  );
}
