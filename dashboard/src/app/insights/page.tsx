"use client";

import { EmptyState } from "@/components/ui/EmptyState";
import { BarChart2 } from "lucide-react";
import ko from "@/i18n/ko.json";

// TODO Step 7: 분석 탭 전체 구현 (에쿼티 커브 + KPI 4개 + 거래 내역)
export default function InsightsPage() {
  return (
    <div className="p-6 max-w-5xl mx-auto">
      <h1 className="text-heading font-bold text-text-primary mb-6">{ko.nav.insights}</h1>
      <EmptyState
        icon={BarChart2}
        title="분석 탭 준비 중"
        description="Step 7 마이그레이션에서 에쿼티 커브·KPI·거래 내역이 구현됩니다."
      />
    </div>
  );
}
