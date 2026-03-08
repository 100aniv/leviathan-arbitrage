"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, Zap, ShieldAlert, Server, Activity, History, Bell, Settings, BarChart3, TrendingUp, Globe, Menu, X } from "lucide-react";
import clsx from "clsx";

const NAV_ITEMS = [
  { href: "/",           label: "Overview",   icon: LayoutDashboard },
  { href: "/strategies", label: "Strategies", icon: Zap },
  { href: "/risk",       label: "Risk",       icon: ShieldAlert },
  { href: "/system",     label: "System",     icon: Server },
  { href: "/trades",     label: "Trades",     icon: History },
  { href: "/alerts",     label: "Alerts",     icon: Bell },
  { href: "/analytics",  label: "Analytics",  icon: BarChart3 },
  { href: "/funding",    label: "Funding",    icon: TrendingUp },
  { href: "/exchanges",  label: "Exchanges",  icon: Globe },
  { href: "/settings",   label: "Settings",   icon: Settings },
];

function NavLinks({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
  return (
    <nav className="flex flex-col gap-1 p-2 flex-1" aria-label="Main navigation">
      {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
        const active = pathname === href;
        return (
          <Link
            key={href}
            href={href}
            onClick={onNavigate}
            className={clsx(
              "flex items-center gap-3 px-3 py-2 rounded-md text-sm font-mono transition-colors",
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
    </nav>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  return (
    <>
      {/* Desktop sidebar — md and above */}
      <aside className="hidden md:flex flex-col w-56 min-h-screen bg-terminal-surface border-r border-terminal-border shrink-0">
        <div className="flex items-center gap-2 px-4 py-4 border-b border-terminal-border">
          <Activity className="w-5 h-5 text-profit" />
          <div>
            <p className="text-sm font-mono font-semibold text-terminal-text leading-none">LEVIATHAN</p>
            <p className="text-[10px] font-mono text-terminal-subtle mt-0.5">ARBITRAGE ENGINE</p>
          </div>
        </div>
        <NavLinks pathname={pathname} />
        <div className="px-4 py-3 border-t border-terminal-border">
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
            <div className="flex items-center justify-between px-4 py-4 border-b border-terminal-border">
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
            <div className="px-4 py-3 border-t border-terminal-border">
              <p className="text-[10px] font-mono text-terminal-subtle">v2.0 · WAR ROOM</p>
            </div>
          </aside>
        </>
      )}
    </>
  );
}
