"use client";

import Link from "next/link";
import { ShieldCheck, MapPin, Activity, Radio, Heart } from "lucide-react";
import { StatusBadge } from "@/components/ui";
import { EmergencyStop } from "@/components/ui/EmergencyStop";
import ko from "@/i18n/ko.json";

const SAFETY_PAGES = [
  { href: "/safety/positions",  icon: MapPin,    label: "활성 포지션",    desc: "좀비 포지션 전수조사 + 수동 청산" },
  { href: "/safety/latency",    icon: Activity,  label: "지연시간 측정",   desc: "13항목 지연시간 라이브 차트" },
  { href: "/safety/canary",     icon: Radio,     label: "카나리 진행",    desc: "Phase 2 FSM 종료조건 체크리스트" },
  { href: "/safety/heartbeat",  icon: Heart,     label: "하트비트 상태",   desc: "데드맨 스위치 + 수동 정지" },
];

// TODO Step 6: /api/safety/* 백엔드 라우터 연동
async function handleEmergencyStop(password: string) {
  const res = await fetch("/engine-api/kill", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason: "dashboard_manual", password }) });
  if (!res.ok) throw new Error("긴급정지 실패");
}

export default function SafetyPage() {
  return (
    <div className="max-w-screen-xl mx-auto px-4 md:px-6 py-4 pb-24 space-y-6">
      <div className="flex items-center gap-3 mb-2">
        <ShieldCheck size={24} className="text-brand" aria-hidden />
        <h1 className="text-heading font-bold text-text-primary">{ko.nav.safety}</h1>
        <StatusBadge status="normal" />
      </div>

      {/* 긴급 정지 */}
      <section className="card" aria-label="긴급 정지">
        <h2 className="text-body font-semibold text-text-primary mb-2">{ko.safety.emergencyStop}</h2>
        <p className="text-caption text-text-secondary mb-4">{ko.safety.emergencyStopDesc}</p>
        <EmergencyStop onConfirm={handleEmergencyStop} />
      </section>

      {/* 안전 페이지 바로가기 */}
      <section>
        <h2 className="text-body font-semibold text-text-primary mb-3">안전 세부 메뉴</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {SAFETY_PAGES.map(({ href, icon: Icon, label, desc }) => (
            <Link
              key={href}
              href={href}
              className="card flex items-start gap-3 hover:border-brand transition-colors group"
            >
              <div className="w-10 h-10 rounded-[10px] bg-brand-subtle flex items-center justify-center flex-shrink-0">
                <Icon size={18} className="text-brand" aria-hidden />
              </div>
              <div>
                <div className="text-body font-medium text-text-primary group-hover:text-brand transition-colors">{label}</div>
                <div className="text-caption text-text-secondary mt-0.5">{desc}</div>
              </div>
            </Link>
          ))}
        </div>
      </section>

      {/* 최근 안전 이벤트 (TODO: 백엔드 연동) */}
      <section className="card" aria-label="최근 안전 이벤트">
        <h2 className="text-body font-semibold text-text-primary mb-3">{ko.safety.recentEvents}</h2>
        <p className="text-caption text-text-secondary">{ko.safety.noEvents}</p>
      </section>
    </div>
  );
}
