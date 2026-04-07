"use client";
import { EmptyState } from "@/components/ui";
import { MapPin } from "lucide-react";
// TODO Step 5+6: /api/positions/open 연동 + 수동 청산 버튼
export default function PositionsPage() {
  return (
    <div className="p-6 max-w-5xl mx-auto">
      <h1 className="text-heading font-bold text-text-primary mb-6">활성 포지션</h1>
      <EmptyState icon={MapPin} title="포지션 없음" description="좀비 포지션 전수조사 + 수동 청산 (Step 5~6에서 구현)" />
    </div>
  );
}
