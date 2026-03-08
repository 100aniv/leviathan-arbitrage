"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, Zap, ShieldAlert, Server, Activity, History, Bell, Settings, BarChart3, TrendingUp } from "lucide-react";
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
  { href: "/settings",   label: "Settings",   icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="flex flex-col w-56 min-h-screen bg-terminal-surface border-r border-terminal-border shrink-0">
      {/* Brand */}
      <div className="flex items-center gap-2 px-4 py-4 border-b border-terminal-border">
        <Activity className="w-5 h-5 text-profit" />
        <div>
          <p className="text-sm font-mono font-semibold text-terminal-text leading-none">LEVIATHAN</p>
          <p className="text-[10px] font-mono text-terminal-subtle mt-0.5">ARBITRAGE ENGINE</p>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex flex-col gap-1 p-2 flex-1" aria-label="Main navigation">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const active = pathname === href;
          return (
            <Link
              key={href}
              href={href}
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

      <div className="px-4 py-3 border-t border-terminal-border">
        <p className="text-[10px] font-mono text-terminal-subtle">v2.0 · WAR ROOM</p>
      </div>
    </aside>
  );
}
