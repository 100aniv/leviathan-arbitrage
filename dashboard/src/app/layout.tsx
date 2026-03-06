import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";

export const metadata: Metadata = {
  title: "LEVIATHAN · War Room Dashboard",
  description: "Global Arbitrage Engine Control Panel",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="flex min-h-screen bg-terminal-bg text-terminal-text font-sans">
        <Sidebar />
        <div className="flex flex-col flex-1 min-w-0">
          {/* Top header */}
          <header className="flex items-center justify-between px-6 py-3 border-b border-terminal-border bg-terminal-surface/50 backdrop-blur shrink-0">
            <h1 className="text-xs font-mono text-terminal-subtle uppercase tracking-widest">
              War Room Dashboard
            </h1>
            <div className="flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-profit animate-pulse" aria-hidden />
              <span className="text-xs font-mono text-terminal-subtle">ENGINE ONLINE</span>
            </div>
          </header>

          {/* Page content */}
          <main className="flex-1 overflow-auto p-6">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
