import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";
import { MissionControlStrip } from "@/components/MissionControlStrip";

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
          {/* MissionControlStrip — mt-14 pushes it below the mobile hamburger (h-14) */}
          <div className="mt-14 md:mt-0 shrink-0">
            <MissionControlStrip />
          </div>

          {/* Page content */}
          <main className="flex-1 overflow-auto p-6">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
