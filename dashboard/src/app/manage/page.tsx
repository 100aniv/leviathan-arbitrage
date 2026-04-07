"use client";

import { EmptyState } from "@/components/ui/EmptyState";
import { Settings2 } from "lucide-react";
import ko from "@/i18n/ko.json";

// TODO Step 7: 운용 탭 전체 구현 (전략 그리드 + 거래소 그리드 + 자본 설정)
export default function ManagePage() {
  return (
    <div className="p-6 max-w-5xl mx-auto">
      <h1 className="text-heading font-bold text-text-primary mb-6">{ko.nav.manage}</h1>
      <EmptyState
        icon={Settings2}
        title="운용 탭 준비 중"
        description="Step 7 마이그레이션에서 전략 그리드·거래소 그리드·자본 설정이 구현됩니다."
      />
    </div>
  );
}
