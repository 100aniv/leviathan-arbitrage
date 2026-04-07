"use client";
import { EmptyState } from "@/components/ui";
import { Heart } from "lucide-react";
// TODO Step 5+6: /api/heartbeat/status 연동
export default function HeartbeatPage() {
  return (
    <div className="p-6 max-w-5xl mx-auto">
      <h1 className="text-heading font-bold text-text-primary mb-6">하트비트 상태</h1>
      <EmptyState icon={Heart} title="하트비트 데이터 없음" description="Dead Man's Switch 상태 + 수동 halt (Step 5~6에서 구현)" />
    </div>
  );
}
