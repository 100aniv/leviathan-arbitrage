"use client";

import React, { useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import {
  Home, Settings2, BarChart2, ShieldCheck, Settings, Bell, LogOut, Database, Activity,
  DollarSign, Briefcase, GitBranch, HeartPulse, Scale, FileText, AlertOctagon, CheckSquare,
} from "lucide-react";
import ko from "@/i18n/ko.json";
import { useApi } from "@/hooks/useApi";
import { getStatus } from "@/lib/api";
import type { StatusResponse } from "@/types";

const TABS = [
  { id: "home",     label: ko.nav.home,     href: "/",         icon: Home },
  { id: "manage",   label: ko.nav.manage,   href: "/manage",   icon: Settings2 },
  { id: "insights", label: ko.nav.insights, href: "/insights", icon: BarChart2 },
  { id: "safety",   label: ko.nav.safety,   href: "/safety",   icon: ShieldCheck },
] as const;

/** Path-B v2 W3 (Day 22) — 8 operator dashboard pages per DESIGN.md §5. */
const PATH_B_PAGES = [
  { label: "PnL",             href: "/pnl",             icon: DollarSign },
  { label: "Positions",       href: "/positions",       icon: Briefcase },
  { label: "Strategy Health", href: "/strategy-health", icon: HeartPulse },
  { label: "Divergence",      href: "/divergence",      icon: Scale },
  { label: "Daily Report",    href: "/daily-report",    icon: FileText },
  { label: "Rejections",      href: "/rejections",      icon: AlertOctagon },
  { label: "Reconciliation",  href: "/reconciliation",  icon: CheckSquare },
  { label: "Trace",           href: "/trace/latest",    icon: GitBranch },
] as const;

const QUICK_MENU = [
  { label: ko.nav.system,    href: "/system",    icon: Activity },
  { label: ko.nav.backtest,  href: "/backtest",  icon: Database },
  { label: ko.nav.alerts,    href: "/alerts",    icon: Bell },
  { label: ko.nav.settings,  href: "/settings",  icon: Settings },
  { label: ko.nav.logout,    href: "/login",     icon: LogOut },
];

interface TabLayoutProps {
  children: React.ReactNode;
}

export function TabLayout({ children }: TabLayoutProps) {
  const pathname = usePathname();
  const [quickOpen, setQuickOpen] = useState(false);

  function isActive(href: string) {
    if (href === "/") return pathname === "/";
    return pathname.startsWith(href);
  }

  // Path-B v2 W3 — render 8 operator pages inside the dedicated sidebar layout.
  const isPathBRoute = PATH_B_PAGES.some((p) =>
    p.href === "/trace/latest" ? pathname.startsWith("/trace") : pathname.startsWith(p.href),
  );

  if (isPathBRoute) {
    return <PathBShell pathname={pathname}>{children}</PathBShell>;
  }

  return (
    <div className="min-h-screen bg-bg-base flex flex-col">
      {/* ── 헤더 (sticky) ── */}
      <header className="sticky top-0 z-50 bg-bg-base border-b border-border h-14 flex items-center px-4 md:px-6 gap-4">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          {/* 브랜드 */}
          <Link href="/" className="flex items-center gap-2 min-w-0">
            <Image
              src="/logo.png"
              alt="로고"
              width={28}
              height={28}
              className="rounded-md object-cover shrink-0"
            />
            <span className="font-display font-bold text-text-primary tracking-tight text-lg">
              LEVIATHAN
            </span>
            <span className="hidden sm:block text-xs text-text-tertiary font-medium shrink-0">
              트레이딩
            </span>
          </Link>
          {/* 연결 상태 뱃지 */}
          <ConnectionBadge />
        </div>

        {/* 데스크탑 탭 네비게이션 */}
        <nav className="hidden md:flex items-center gap-1" aria-label="주 메뉴">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const active = isActive(tab.href);
            return (
              <Link
                key={tab.id}
                href={tab.href}
                aria-current={active ? "page" : undefined}
                className={`
                  flex items-center gap-1.5 px-3 py-2 rounded-[10px] text-caption font-medium
                  transition-colors duration-150 min-h-[44px]
                  ${active
                    ? "bg-brand-subtle text-brand"
                    : "text-text-secondary hover:bg-bg-surface hover:text-text-primary"
                  }
                `}
              >
                <Icon size={16} aria-hidden />
                {tab.label}
              </Link>
            );
          })}
        </nav>

        {/* 빠른 메뉴 버튼 */}
        <div className="relative">
          <button
            onClick={() => setQuickOpen((v) => !v)}
            aria-label={ko.header.quickMenu}
            aria-expanded={quickOpen}
            className="flex items-center justify-center w-11 h-11 rounded-[10px] text-text-secondary hover:bg-bg-surface hover:text-text-primary transition-colors"
          >
            <Settings size={18} aria-hidden />
          </button>
          {quickOpen && (
            <>
              {/* 외부 클릭 닫기 */}
              <div
                className="fixed inset-0 z-40"
                onClick={() => setQuickOpen(false)}
                aria-hidden
              />
              <div
                className="absolute right-0 top-12 z-50 bg-bg-elevated border border-border rounded-[16px] shadow-card py-2 min-w-[180px]"
                role="menu"
                aria-label={ko.header.quickMenu}
              >
                {QUICK_MENU.map((item) => {
                  const Icon = item.icon;
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      role="menuitem"
                      className="flex items-center gap-3 px-4 py-2.5 text-caption text-text-primary hover:bg-bg-surface transition-colors"
                      onClick={() => setQuickOpen(false)}
                    >
                      <Icon size={16} aria-hidden className="text-text-secondary" />
                      {item.label}
                    </Link>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </header>

      {/* ── 페이지 콘텐츠 ── */}
      <main className="flex-1 overflow-auto" id="main-content">
        {children}
      </main>

      {/* ── 모바일 하단 탭바 (토스 패턴) ── */}
      <nav
        className="md:hidden fixed bottom-0 inset-x-0 z-50 bg-bg-base border-t border-border"
        aria-label="하단 탭 메뉴"
      >
        <div className="flex items-stretch h-16 safe-area-inset-bottom">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const active = isActive(tab.href);
            return (
              <Link
                key={tab.id}
                href={tab.href}
                aria-current={active ? "page" : undefined}
                className={`
                  flex-1 flex flex-col items-center justify-center gap-0.5
                  text-[10px] font-medium transition-colors duration-150
                  min-h-[44px]
                  ${active ? "text-brand" : "text-text-tertiary"}
                `}
              >
                <Icon
                  size={22}
                  aria-hidden
                  className={active ? "text-brand" : "text-text-tertiary"}
                />
                <span>{tab.label}</span>
              </Link>
            );
          })}
        </div>
      </nav>
    </div>
  );
}

/* 연결 상태 표시 컴포넌트 — /api/v1/status 폴링 */
function ConnectionBadge() {
  const { data, error } = useApi<StatusResponse>(
    "/status",
    getStatus,
    { refreshInterval: 10_000 },
  );

  // 데이터 없고 에러도 없으면 로딩 중 → 마지막 상태 유지 (neutral)
  const connected = error ? false : (data?.running ?? null);

  if (connected === null) {
    return (
      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-[6px] text-small font-medium bg-bg-surface text-text-tertiary">
        <span className="w-1.5 h-1.5 rounded-full bg-text-tertiary animate-pulse" aria-hidden />
        {ko.header.reconnecting}
      </span>
    );
  }

  return (
    <span
      className={`
        inline-flex items-center gap-1.5 px-2 py-0.5 rounded-[6px] text-small font-medium
        ${connected ? "bg-success-bg text-success-text" : "bg-danger-bg text-danger"}
      `}
      aria-label={connected ? "엔진 연결됨" : "엔진 연결 끊김"}
    >
      <span
        className={`w-1.5 h-1.5 rounded-full ${connected ? "bg-success" : "bg-danger"}`}
        aria-hidden
      />
      {connected ? ko.header.connected : ko.header.disconnected}
    </span>
  );
}

/* ──────────────────────────────────────────────────────────────────────────
 * Path-B v2 W3 Shell (DESIGN.md §8)
 *  - Fixed 240px left sidebar with 8 operator pages
 *  - 48px top bar with Mode badge + ConnectionBadge + brand
 * ────────────────────────────────────────────────────────────────────────── */
function PathBShell({
  pathname,
  children,
}: {
  pathname: string;
  children: React.ReactNode;
}) {
  function isActive(href: string) {
    if (href === "/trace/latest") return pathname.startsWith("/trace");
    return pathname.startsWith(href);
  }

  return (
    <div className="min-h-screen bg-bg-base flex">
      {/* ── 좌측 사이드바 (240px) ── */}
      <aside
        className="hidden md:flex flex-col fixed top-0 left-0 bottom-0 w-[240px] bg-bg-surface border-r border-border z-40"
        aria-label="Path-B v2 dashboard navigation"
      >
        <div className="h-12 flex items-center px-4 border-b border-border">
          <Link href="/" className="flex items-center gap-2 min-w-0">
            <Image src="/logo.png" alt="로고" width={22} height={22} className="rounded-md shrink-0" />
            <span className="font-display font-bold text-text-primary tracking-tight text-body">
              LEVIATHAN
            </span>
          </Link>
        </div>

        <div className="px-4 py-3 border-b border-border">
          <ModeBadge />
        </div>

        <nav className="flex-1 overflow-y-auto py-2" aria-label="운영 페이지 메뉴">
          {PATH_B_PAGES.map((p) => {
            const Icon = p.icon;
            const active = isActive(p.href);
            return (
              <Link
                key={p.href}
                href={p.href}
                aria-current={active ? "page" : undefined}
                className={`
                  flex items-center gap-2 px-4 py-2 text-caption font-medium
                  transition-colors duration-150 border-l-2
                  ${active
                    ? "bg-brand-subtle text-brand border-brand"
                    : "text-text-secondary border-transparent hover:bg-bg-muted hover:text-text-primary"}
                `}
              >
                <Icon size={15} aria-hidden />
                <span>{p.label}</span>
              </Link>
            );
          })}
        </nav>

        <div className="px-4 py-3 border-t border-border">
          <Link href="/" className="text-small text-text-tertiary hover:text-text-primary transition-colors">
            ← 메인 대시보드
          </Link>
        </div>
      </aside>

      {/* ── 본문 (사이드바 만큼 오프셋) ── */}
      <div className="flex-1 min-w-0 md:ml-[240px] flex flex-col">
        {/* 상단 48px 바 */}
        <header
          className="sticky top-0 z-30 h-12 bg-bg-base border-b border-border flex items-center px-4 md:px-6 gap-3"
        >
          <div className="md:hidden flex items-center gap-2">
            <Image src="/logo.png" alt="로고" width={20} height={20} className="rounded-md" />
            <span className="font-display font-bold text-text-primary text-caption">LEVIATHAN</span>
          </div>
          <div className="flex items-center gap-3 ml-auto">
            <ConnectionBadge />
          </div>
        </header>

        <main className="flex-1 overflow-auto" id="main-content">
          {children}
        </main>
      </div>

      {/* 모바일: 하단 탭은 8페이지를 표시하지 않음 — 사이드바가 가려지므로
          Path-B 페이지 내 네비게이션은 /pnl /positions 등 직접 URL 접근 */}
    </div>
  );
}

/* Mode 표시 뱃지 — engine.json의 mode (paper|live|backtest) */
function ModeBadge() {
  const { data } = useApi<StatusResponse>("/status", getStatus, {
    refreshInterval: 15_000,
  });
  const mode = (data?.execution_mode ?? "paper").toLowerCase();
  const modeClass =
    mode === "live"     ? "bg-danger-bg text-danger" :
    mode === "backtest" ? "bg-info-bg text-info" :
                          "bg-success-bg text-[#026B3F]";

  return (
    <div className="flex items-center justify-between">
      <span className="text-small uppercase tracking-widest text-text-tertiary font-medium">
        Mode
      </span>
      <span
        className={`inline-flex items-center px-2 py-0.5 rounded-[6px] text-small font-mono font-semibold uppercase ${modeClass}`}
        aria-label={`실행 모드: ${mode}`}
      >
        {mode}
      </span>
    </div>
  );
}
