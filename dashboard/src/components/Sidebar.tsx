"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard, Zap, ShieldAlert, Server, Activity,
  History, Bell, Settings, BarChart3, TrendingUp, Globe,
  PieChart, Wallet, Menu, X,
} from "lucide-react";
import clsx from "clsx";

// ─── Navigation Groups ────────────────────────────────────────────────────────

const NAV_GROUPS = [
  {
    label: "모니터링",
    items: [
      { href: "/",          label: "대시보드",    icon: LayoutDashboard, tip: "전체 현황 요약" },
      { href: "/portfolio", label: "포트폴리오",  icon: Wallet,          tip: "자산 배분 및 수익률" },
    ],
  },
  {
    label: "분석",
    items: [
      { href: "/strategies",  label: "전략 관리",   icon: Zap,        tip: "7개 전략 상태 및 제어" },
      { href: "/analytics",   label: "성과 분석",   icon: BarChart3,  tip: "전략별 수익 순위 및 히트맵" },
      { href: "/alerts",      label: "알림",        icon: Bell,       tip: "시스템 경고 및 알림" },
      { href: "/trades",      label: "거래 내역",   icon: History,    tip: "체결 이력 및 필터" },
      { href: "/attribution", label: "수익 귀속",   icon: PieChart,   tip: "전략/거래소/페어별 수익 분석" },
      { href: "/funding",     label: "펀딩 레이트", icon: TrendingUp, tip: "거래소별 펀딩 레이트 현황" },
      { href: "/exchanges",   label: "거래소",      icon: Globe,      tip: "10개 거래소 연결 상태" },
    ],
  },
  {
    label: "관리",
    items: [
      { href: "/settings", label: "설정",      icon: Settings,   tip: "운영 모드, 자본, 파라미터" },
      { href: "/system",   label: "시스템",    icon: Server,     tip: "엔진 상태, 리소스, Docker" },
      { href: "/risk",     label: "리스크",    icon: ShieldAlert, tip: "MDD, 킬스위치, 서킷브레이커" },
    ],
  },
];

// ─── NavLinks Component ───────────────────────────────────────────────────────

function NavLinks({
  pathname,
  onNavigate,
}: {
  pathname: string;
  onNavigate?: () => void;
}) {
  return (
    <nav className="flex flex-col gap-0.5 p-2 flex-1 overflow-y-auto" aria-label="Main navigation">
      {NAV_GROUPS.map((group) => (
        <div key={group.label} className="mb-2">
          {/* Group header */}
          <div className="px-3 py-1.5 mb-0.5">
            <span className="text-[9px] font-mono font-semibold text-terminal-subtle/60 uppercase tracking-[0.25em]">
              {group.label}
            </span>
          </div>
          {/* Group items */}
          {group.items.map(({ href, label, icon: Icon, tip }) => {
            const active = pathname === href;
            return (
              <Link
                key={href}
                href={href}
                onClick={onNavigate}
                title={tip}
                className={clsx(
                  "flex items-center gap-3 px-3 py-1.5 rounded-md text-sm font-mono transition-colors",
                  active
                    ? "bg-accent/10 text-accent border border-accent/20"
                    : "text-terminal-subtle hover:text-terminal-text hover:bg-terminal-muted/50"
                )}
                aria-current={active ? "page" : undefined}
              >
                <Icon className="w-4 h-4 shrink-0" aria-hidden />
                {label}
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}

// ─── Sidebar Component ────────────────────────────────────────────────────────

export function Sidebar() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  return (
    <>
      {/* Desktop sidebar — md and above */}
      <aside className="hidden md:flex flex-col w-56 min-h-screen bg-terminal-surface border-r border-terminal-border shrink-0">
        <div className="flex items-center gap-2 px-4 py-4 border-b border-terminal-border shrink-0">
          <Activity className="w-5 h-5 text-profit" />
          <div>
            <p className="text-sm font-mono font-semibold text-terminal-text leading-none">LEVIATHAN</p>
            <p className="text-[10px] font-mono text-terminal-subtle mt-0.5">ARBITRAGE ENGINE</p>
          </div>
        </div>
        <NavLinks pathname={pathname} />
        <div className="px-4 py-3 border-t border-terminal-border shrink-0">
          <p className="text-[10px] font-mono text-terminal-subtle">v2.0 · WAR ROOM</p>
        </div>
      </aside>

      {/* Mobile top bar — below md */}
      <div className="md:hidden fixed top-0 left-0 right-0 z-40 flex items-center justify-between h-14 px-4 bg-terminal-surface border-b border-terminal-border">
        <div className="flex items-center gap-2">
          <Activity className="w-5 h-5 text-profit" />
          <div>
            <p className="text-sm font-mono font-semibold text-terminal-text leading-none">LEVIATHAN</p>
            <p className="text-[10px] font-mono text-terminal-subtle mt-0.5">ARBITRAGE ENGINE</p>
          </div>
        </div>
        <button
          onClick={() => setOpen((v) => !v)}
          className="p-1.5 rounded text-terminal-subtle hover:text-terminal-text hover:bg-terminal-muted/50 transition-colors"
          aria-label={open ? "Close menu" : "Open menu"}
          aria-expanded={open}
        >
          {open ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
        </button>
      </div>

      {/* Mobile overlay + drawer */}
      {open && (
        <>
          <div
            className="md:hidden fixed inset-0 z-30 bg-black/60"
            onClick={() => setOpen(false)}
            aria-hidden
          />
          <aside className="md:hidden fixed top-0 left-0 z-50 flex flex-col w-64 h-full bg-terminal-surface border-r border-terminal-border">
            <div className="flex items-center justify-between px-4 py-4 border-b border-terminal-border shrink-0">
              <div className="flex items-center gap-2">
                <Activity className="w-5 h-5 text-profit" />
                <div>
                  <p className="text-sm font-mono font-semibold text-terminal-text leading-none">LEVIATHAN</p>
                  <p className="text-[10px] font-mono text-terminal-subtle mt-0.5">ARBITRAGE ENGINE</p>
                </div>
              </div>
              <button
                onClick={() => setOpen(false)}
                className="p-1 rounded text-terminal-subtle hover:text-terminal-text transition-colors"
                aria-label="Close menu"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <NavLinks pathname={pathname} onNavigate={() => setOpen(false)} />
            <div className="px-4 py-3 border-t border-terminal-border shrink-0">
              <p className="text-[10px] font-mono text-terminal-subtle">v2.0 · WAR ROOM</p>
            </div>
          </aside>
        </>
      )}
    </>
  );
}
