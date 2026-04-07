import type { Metadata } from "next";
import "./globals.css";
// Step 1: Pretendard — 한글 최우선 폰트 (DESIGN-kraken.md SSOT)
import "@fontsource/pretendard/400.css";
import "@fontsource/pretendard/500.css";
import "@fontsource/pretendard/600.css";
import "@fontsource/pretendard/700.css";
import { TabLayout } from "@/components/layout/TabLayout";

export const metadata: Metadata = {
  title: "LEVIATHAN · 운용 대시보드",
  description: "레비아탄 자동 차익거래 엔진 운용 현황",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // Step 1 핵심: className="dark" 제거 — DESIGN-kraken.md 라이트 테마 강제
    <html lang="ko" suppressHydrationWarning>
      <body
        className="min-h-screen bg-bg-base text-text-primary font-sans"
        suppressHydrationWarning
      >
        <TabLayout>
          {children}
        </TabLayout>
      </body>
    </html>
  );
}
