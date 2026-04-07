"use client";
import { EmptyState } from "@/components/ui";
import { Radio } from "lucide-react";
// TODO Step 5+6: /api/canary/status 연동
export default function CanaryPage() {
  return (
    <div className="p-6 max-w-5xl mx-auto">
      <h1 className="text-heading font-bold text-text-primary mb-6">카나리 진행</h1>
      <EmptyState icon={Radio} title="카나리 데이터 없음" description="Phase 2 FSM 종료조건 체크리스트 (Step 5~6에서 구현)" />
    </div>
  );
}
