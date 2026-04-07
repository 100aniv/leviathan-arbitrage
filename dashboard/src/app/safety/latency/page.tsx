"use client";

import dynamic from "next/dynamic";
import useSWR from "swr";
import { Activity } from "lucide-react";
import { SkeletonCard, FriendlyError } from "@/components/ui";

const BarChart = dynamic(() => import("recharts").then((m) => m.BarChart), { ssr: false });
const Bar = dynamic(() => import("recharts").then((m) => m.Bar), { ssr: false });
const XAxis = dynamic(() => import("recharts").then((m) => m.XAxis), { ssr: false });
const YAxis = dynamic(() => import("recharts").then((m) => m.YAxis), { ssr: false });
const CartesianGrid = dynamic(() => import("recharts").then((m) => m.CartesianGrid), { ssr: false });
const Tooltip = dynamic(() => import("recharts").then((m) => m.Tooltip), { ssr: false });
const Legend = dynamic(() => import("recharts").then((m) => m.Legend), { ssr: false });
const ResponsiveContainer = dynamic(() => import("recharts").then((m) => m.ResponsiveContainer), { ssr: false });

interface LatencyEntry {
  exchange: string;
  p50: number;
  p95: number;
  p99: number;
  avg: number;
  sample_count: number;
}

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export default function LatencyPage() {
  const { data, isLoading, error } = useSWR<LatencyEntry[]>(
    "/api/latency/exchange",
    fetcher,
    { refreshInterval: 30000 }
  );

  if (isLoading) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <h1 className="text-heading font-bold text-text-primary mb-6">지연시간 측정</h1>
        <SkeletonCard />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <h1 className="text-heading font-bold text-text-primary mb-6">지연시간 측정</h1>
        <FriendlyError error="지연시간 데이터를 불러올 수 없습니다." />
      </div>
    );
  }

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <Activity size={24} className="text-brand" />
        <h1 className="text-heading font-bold text-text-primary">지연시간 측정</h1>
      </div>

      {/* Bar Chart */}
      <div className="card mb-6">
        <h2 className="text-title font-bold text-text-primary mb-4">거래소별 지연시간 (ms)</h2>
        <ResponsiveContainer width="100%" height={320}>
          <BarChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
            <XAxis dataKey="exchange" tick={{ fontSize: 12 }} />
            <YAxis tick={{ fontSize: 12 }} unit="ms" />
            <Tooltip
              formatter={(value: unknown) => [`${Number(value) || 0}ms`]}
              contentStyle={{ fontSize: 12 }}
            />
            <Legend />
            <Bar dataKey="p50" name="P50" fill="#7132F5" radius={[3, 3, 0, 0]} />
            <Bar dataKey="p95" name="P95" fill="#F59E0B" radius={[3, 3, 0, 0]} />
            <Bar dataKey="p99" name="P99" fill="#EF4444" radius={[3, 3, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Table */}
      <div className="card overflow-x-auto">
        <table className="w-full text-body">
          <thead>
            <tr className="border-b border-border text-caption text-text-secondary">
              <th className="text-left py-2 pr-4">거래소</th>
              <th className="text-right py-2 pr-4">P50</th>
              <th className="text-right py-2 pr-4">P95</th>
              <th className="text-right py-2 pr-4">P99</th>
              <th className="text-right py-2 pr-4">평균</th>
              <th className="text-right py-2">샘플수</th>
            </tr>
          </thead>
          <tbody>
            {data.map((entry) => (
              <tr
                key={entry.exchange}
                className="border-b border-border last:border-0 hover:bg-bg-surface transition-colors"
              >
                <td className="py-2 pr-4 font-medium text-text-primary capitalize">{entry.exchange}</td>
                <td className="py-2 pr-4 text-right tabular-nums">{entry.p50}ms</td>
                <td className="py-2 pr-4 text-right tabular-nums">{entry.p95}ms</td>
                <td className="py-2 pr-4 text-right tabular-nums">{entry.p99}ms</td>
                <td className="py-2 pr-4 text-right tabular-nums">{entry.avg}ms</td>
                <td className="py-2 text-right">
                  {entry.sample_count === 0 ? (
                    <span className="inline-flex items-center px-2 py-0.5 rounded text-caption bg-bg-surface text-text-secondary border border-border">
                      미측정
                    </span>
                  ) : (
                    <span className="tabular-nums">{entry.sample_count.toLocaleString()}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
