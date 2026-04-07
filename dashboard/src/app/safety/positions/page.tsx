"use client";

import useSWR from "swr";
import { MapPin } from "lucide-react";
import { EmptyState, SkeletonCard, FriendlyError } from "@/components/ui";

interface Position {
  key: string;
  strategy_id: string;
  exchange_id: string;
  symbol: string;
  side: string;
  qty: string;
  entry_price: string;
  unrealized_pnl: string;
  hold_seconds: number;
}

const fetcher = (url: string) => fetch(url).then((r) => r.json());

function formatHoldTime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

export default function PositionsPage() {
  const { data, isLoading, error } = useSWR<Position[]>(
    "/api/positions/open",
    fetcher,
    { refreshInterval: 10000 }
  );

  if (isLoading) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <h1 className="text-heading font-bold text-text-primary mb-6">활성 포지션</h1>
        <SkeletonCard />
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <h1 className="text-heading font-bold text-text-primary mb-6">활성 포지션</h1>
        <FriendlyError error="포지션 데이터를 불러올 수 없습니다." />
      </div>
    );
  }

  const positions = data ?? [];

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <MapPin size={24} className="text-brand" />
        <h1 className="text-heading font-bold text-text-primary">활성 포지션</h1>
        <span className="text-caption text-text-secondary">
          {positions.length}건
        </span>
      </div>

      {positions.length === 0 ? (
        <EmptyState icon={MapPin} title="포지션 없음" description="현재 오픈 포지션이 없습니다." />
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-body">
            <thead>
              <tr className="border-b border-border text-caption text-text-secondary">
                <th className="text-left py-2 pr-4">전략</th>
                <th className="text-left py-2 pr-4">거래소</th>
                <th className="text-left py-2 pr-4">심볼</th>
                <th className="text-left py-2 pr-4">방향</th>
                <th className="text-right py-2 pr-4">수량</th>
                <th className="text-right py-2 pr-4">진입가</th>
                <th className="text-right py-2 pr-4">미실현PnL</th>
                <th className="text-right py-2">보유시간</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((pos) => {
                const pnl = parseFloat(pos.unrealized_pnl);
                const pnlColor = pnl > 0 ? "text-success" : pnl < 0 ? "text-danger" : "text-text-secondary";
                return (
                  <tr key={pos.key} className="border-b border-border last:border-0 hover:bg-bg-surface transition-colors">
                    <td className="py-2 pr-4 text-text-primary font-medium">{pos.strategy_id}</td>
                    <td className="py-2 pr-4 text-text-secondary">{pos.exchange_id}</td>
                    <td className="py-2 pr-4 text-text-primary font-mono">{pos.symbol}</td>
                    <td className="py-2 pr-4">
                      <span className={pos.side === "long" ? "text-success" : "text-danger"}>
                        {pos.side.toUpperCase()}
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-right tabular-nums">{pos.qty}</td>
                    <td className="py-2 pr-4 text-right tabular-nums">{pos.entry_price}</td>
                    <td className={`py-2 pr-4 text-right tabular-nums font-medium ${pnlColor}`}>
                      {pnl >= 0 ? "+" : ""}{pos.unrealized_pnl}
                    </td>
                    <td className="py-2 text-right text-text-secondary">{formatHoldTime(pos.hold_seconds)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
