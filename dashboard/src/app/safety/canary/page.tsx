"use client";

import useSWR from "swr";
import { Radio } from "lucide-react";
import { StatusBadge, KPICard, SkeletonCard, FriendlyError, EmptyState } from "@/components/ui";
import type { StatusVariant } from "@/components/ui";

interface LossThresholds {
  warn_5pct_usd?: number;
  deactivate_7pct_usd?: number;
  ks_10pct_usd?: number;
}

interface CanaryState {
  step?: string;
  status?: string;
  started_at?: string;
  ends_at?: string;
  strategy?: string;
  capital_initial_usd?: number;
  loss_thresholds?: LossThresholds;
  [key: string]: unknown;
}

function toStatusVariant(s: string | undefined): StatusVariant {
  if (!s) return "inactive";
  const lower = s.toLowerCase();
  if (lower === "running" || lower === "active" || lower === "pass" || lower === "normal") return "normal";
  if (lower === "warn" || lower === "caution" || lower === "warning") return "caution";
  if (lower === "fail" || lower === "error" || lower === "danger" || lower === "stop") return "danger";
  if (lower === "paused") return "paused";
  if (lower === "stopped") return "stopped";
  return "inactive";
}

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export default function CanaryPage() {
  const { data, isLoading, error } = useSWR<CanaryState>(
    "/api/canary/status",
    fetcher,
    { refreshInterval: 30000 }
  );

  if (isLoading) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <h1 className="text-heading font-bold text-text-primary mb-6">카나리 진행</h1>
        <div className="grid grid-cols-2 gap-4">
          <SkeletonCard />
          <SkeletonCard />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <h1 className="text-heading font-bold text-text-primary mb-6">카나리 진행</h1>
        <FriendlyError error="카나리 상태 파일을 찾을 수 없습니다." />
      </div>
    );
  }

  if (!data || Object.keys(data).length === 0) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <h1 className="text-heading font-bold text-text-primary mb-6">카나리 진행</h1>
        <EmptyState icon={Radio} title="카나리 데이터 없음" description="Phase 2 상태 파일이 아직 생성되지 않았습니다." />
      </div>
    );
  }

  const statusVariant = toStatusVariant(data.status);
  const thresholds = data.loss_thresholds ?? {};

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <Radio size={24} className="text-brand" />
        <h1 className="text-heading font-bold text-text-primary">카나리 진행</h1>
        <StatusBadge status={statusVariant} label={data.status} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Current State Card */}
        <div className="card">
          <h2 className="text-title font-bold text-text-primary mb-4">현재 상태</h2>
          <dl className="space-y-3">
            <div className="flex justify-between">
              <dt className="text-caption text-text-secondary">Step</dt>
              <dd className="text-body font-medium text-text-primary">{data.step ?? "—"}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-caption text-text-secondary">Status</dt>
              <dd>
                <StatusBadge status={statusVariant} label={data.status} size="sm" />
              </dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-caption text-text-secondary">시작</dt>
              <dd className="text-body text-text-primary">
                {data.started_at
                  ? new Date(data.started_at).toLocaleString("ko-KR")
                  : "—"}
              </dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-caption text-text-secondary">종료 예정</dt>
              <dd className="text-body text-text-primary">
                {data.ends_at
                  ? new Date(data.ends_at).toLocaleString("ko-KR")
                  : "—"}
              </dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-caption text-text-secondary">전략</dt>
              <dd className="text-body font-medium text-text-primary">{data.strategy ?? "—"}</dd>
            </div>
          </dl>
        </div>

        {/* Loss Thresholds Card */}
        <div className="card">
          <h2 className="text-title font-bold text-text-primary mb-4">자본/손실 임계값</h2>
          <div className="space-y-3">
            <KPICard
              label="초기 자본"
              value={
                data.capital_initial_usd !== undefined
                  ? `$${data.capital_initial_usd.toLocaleString()}`
                  : "—"
              }
              size="sm"
            />
            <KPICard
              label="경고 (5%)"
              value={
                thresholds.warn_5pct_usd !== undefined
                  ? <span className="text-orange-500">${thresholds.warn_5pct_usd.toLocaleString()}</span>
                  : "—"
              }
              size="sm"
            />
            <KPICard
              label="비활성화 (7%)"
              value={
                thresholds.deactivate_7pct_usd !== undefined
                  ? <span className="text-yellow-600">${thresholds.deactivate_7pct_usd.toLocaleString()}</span>
                  : "—"
              }
              size="sm"
            />
            <KPICard
              label="KillSwitch (10%)"
              value={
                thresholds.ks_10pct_usd !== undefined
                  ? <span className="text-red-600">${thresholds.ks_10pct_usd.toLocaleString()}</span>
                  : "—"
              }
              size="sm"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
