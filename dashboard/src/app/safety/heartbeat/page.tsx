"use client";

import { useState } from "react";
import useSWR from "swr";
import { Heart } from "lucide-react";
import { StatusBadge, KPICard, ConfirmDialog, SkeletonCard, FriendlyError } from "@/components/ui";

interface HeartbeatStatus {
  ttl_seconds: number;
  halt_flag: boolean;
  alive: boolean;
  last_seen: string | null;
  watchdog_on: boolean;
  error?: string;
}

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export default function HeartbeatPage() {
  const { data, isLoading, error } = useSWR<HeartbeatStatus>(
    "/api/heartbeat/status",
    fetcher,
    { refreshInterval: 5000 }
  );
  const [dialogOpen, setDialogOpen] = useState(false);
  const [halting, setHalting] = useState(false);
  const [haltResult, setHaltResult] = useState<string | null>(null);

  async function doHalt() {
    setHalting(true);
    try {
      const res = await fetch("/api/heartbeat/halt", { method: "POST" });
      const json = await res.json() as { ok: boolean; dry_run?: boolean; error?: string };
      setHaltResult(json.ok ? (json.dry_run ? "Dry-run 완료 (실제 halt 미적용)" : "긴급 Halt 완료") : `실패: ${json.error}`);
    } catch {
      setHaltResult("네트워크 오류");
    } finally {
      setHalting(false);
      setDialogOpen(false);
    }
  }

  if (isLoading) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <h1 className="text-heading font-bold text-text-primary mb-6">하트비트 상태</h1>
        <div className="grid grid-cols-3 gap-4">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <h1 className="text-heading font-bold text-text-primary mb-6">하트비트 상태</h1>
        <FriendlyError error="하트비트 데이터를 불러올 수 없습니다." />
      </div>
    );
  }

  const ttlWarning = data.ttl_seconds >= 0 && data.ttl_seconds <= 30;

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <Heart size={24} className="text-brand" />
        <h1 className="text-heading font-bold text-text-primary">하트비트 상태</h1>
        <StatusBadge status={data.alive ? "normal" : "danger"} />
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-3 gap-4 mb-8">
        <KPICard
          label="TTL"
          value={
            <span className={ttlWarning ? "text-orange-500" : undefined}>
              {data.ttl_seconds < 0 ? "N/A" : `${data.ttl_seconds}s`}
            </span>
          }
          subValue={ttlWarning ? "주의: 30초 이하" : undefined}
        />
        <KPICard
          label="Halt 플래그"
          value={
            <span className={data.halt_flag ? "text-red-600" : undefined}>
              {data.halt_flag ? "활성" : "정상"}
            </span>
          }
        />
        <KPICard
          label="상태"
          value={data.alive ? "심박 정상" : "심박 없음"}
          subValue={data.last_seen ? `마지막 확인: ${new Date(data.last_seen).toLocaleTimeString("ko-KR")}` : undefined}
        />
      </div>

      {/* Halt Button Section */}
      <div className="card mb-4">
        <h2 className="text-title font-bold text-text-primary mb-3">수동 제어</h2>
        <p className="text-body text-text-secondary mb-4">
          긴급 Halt를 실행하면 Redis에 <code className="font-mono bg-bg-surface px-1 rounded">leviathan:halt=1</code>이 설정되어
          엔진이 신규 주문을 즉시 차단합니다.
        </p>
        <button
          onClick={() => setDialogOpen(true)}
          disabled={halting}
          aria-label="긴급 Halt 실행"
          className="px-4 py-2 bg-red-600 text-white rounded-lg font-medium hover:bg-red-700 disabled:opacity-50 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400 focus-visible:ring-offset-2"
        >
          {halting ? "처리 중..." : "긴급 Halt"}
        </button>
        {haltResult && (
          <p className="mt-3 text-caption text-text-secondary">{haltResult}</p>
        )}
      </div>

      {/* Watchdog status */}
      <div className="card">
        <p className="text-caption text-text-secondary">
          Watchdog: <span className={data.watchdog_on ? "text-success font-medium" : "text-text-secondary"}>{data.watchdog_on ? "활성" : "비활성"}</span>
        </p>
      </div>

      <ConfirmDialog
        isOpen={dialogOpen}
        title="긴급 HALT 실행"
        message="Redis에 halt 플래그를 설정합니다. 엔진이 신규 주문을 즉시 차단합니다. 계속하시겠습니까?"
        confirmLabel="Halt 실행"
        cancelLabel="취소"
        danger="critical"
        onConfirm={doHalt}
        onCancel={() => setDialogOpen(false)}
      />
    </div>
  );
}
