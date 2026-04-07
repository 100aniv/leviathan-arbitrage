"use client";
import { EmptyState } from "@/components/ui";
import { Activity } from "lucide-react";
// TODO Step 5+6: /api/latency/exchange 연동
export default function LatencyPage() {
  return (
    <div className="p-6 max-w-5xl mx-auto">
      <h1 className="text-heading font-bold text-text-primary mb-6">지연시간 측정</h1>
      <EmptyState icon={Activity} title="측정 데이터 없음" description="Bug 13 지연시간 라이브 차트 (Step 5~6에서 구현)" />
    </div>
  );
}
