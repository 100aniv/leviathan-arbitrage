"use client";

import React, { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Home, Settings2, BarChart2, ShieldCheck, Settings, Bell, LogOut, Database, Activity } from "lucide-react";
import ko from "@/i18n/ko.json";

const TABS = [
  { id: "home",     label: ko.nav.home,     href: "/",         icon: Home },
  { id: "manage",   label: ko.nav.manage,   href: "/manage",   icon: Settings2 },
  { id: "insights", label: ko.nav.insights, href: "/insights", icon: BarChart2 },
  { id: "safety",   label: ko.nav.safety,   href: "/safety",   icon: ShieldCheck },
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

  return (
    <div className="min-h-screen bg-bg-base flex flex-col">
      {/* ── 헤더 (sticky) ── */}
      <header className="sticky top-0 z-50 bg-bg-base border-b border-border h-14 flex items-center px-4 md:px-6 gap-4">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          {/* 브랜드 */}
          <span className="font-display font-bold text-text-primary tracking-tight text-lg">
            LEVIATHAN
          </span>
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

/* 연결 상태 표시 컴포넌트 */
function ConnectionBadge() {
  // TODO: useEngineStatus 훅으로 실제 연결 상태 연동
  const connected = true;
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
