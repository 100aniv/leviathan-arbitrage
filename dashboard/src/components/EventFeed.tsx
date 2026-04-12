'use client';

import Link from 'next/link';
import { useApi } from '@/hooks/useApi';
import { getAlerts } from '@/lib/api';
import type { Alert } from '@/types';

function SeverityBadge({ severity }: { severity: Alert['severity'] }) {
  if (severity === 'critical') return <span className="badge-loss shrink-0">critical</span>;
  if (severity === 'warning')  return <span className="badge-warn shrink-0">warning</span>;
  return <span className="badge-accent shrink-0">info</span>;
}

export function EventFeed() {
  const { data: alerts, error, mutate } = useApi<Alert[]>(
    '/alerts',
    getAlerts,
    { refreshInterval: 10000 },
  );

  const sorted = [...(alerts ?? [])]
    .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())
    .slice(0, 20);

  return (
    <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">이벤트</span>
        <Link href="/alerts" className="text-[10px] font-mono text-terminal-subtle hover:text-accent transition-colors">
          전체 보기 →
        </Link>
      </div>

      {error ? (
        <div className="flex flex-col items-center gap-2 py-6">
          <p className="text-xs font-mono text-loss">이벤트를 불러오지 못했어요</p>
          <button
            onClick={() => mutate()}
            aria-label="이벤트 다시 불러오기"
            className="text-[10px] font-mono border border-terminal-border px-3 py-1 text-terminal-subtle hover:text-terminal-text transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            다시 시도
          </button>
        </div>
      ) : sorted.length === 0 ? (
        <p className="text-xs font-mono text-terminal-subtle text-center py-6">최근 이벤트 없음</p>
      ) : (
        <div className="max-h-[300px] overflow-y-auto space-y-0.5">
          {sorted.map(alert => (
            <div
              key={alert.id}
              className="flex items-start gap-2 px-2 py-1.5 hover:bg-terminal-muted/20 transition-colors rounded"
            >
              <span className="text-[10px] font-mono text-terminal-subtle tabular-nums shrink-0 pt-0.5 w-[68px]">
                {new Date(alert.timestamp).toLocaleTimeString('en-GB', {
                  hour: '2-digit',
                  minute: '2-digit',
                  second: '2-digit',
                })}
              </span>
              <SeverityBadge severity={alert.severity} />
              <span className="text-[11px] font-mono text-terminal-text flex-1 min-w-0 break-words leading-relaxed">
                {alert.message}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
