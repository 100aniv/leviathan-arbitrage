import { NextResponse } from 'next/server';
import { getRedis } from '@/lib/redis';

export const dynamic = 'force-dynamic';

const MOCK_LATENCY = [
  { exchange: 'binance',  p50: 45,  p95: 120, p99: 280, avg: 68,  sample_count: 0 },
  { exchange: 'bybit',    p50: 52,  p95: 145, p99: 310, avg: 78,  sample_count: 0 },
  { exchange: 'okx',      p50: 38,  p95: 98,  p99: 210, avg: 55,  sample_count: 0 },
  { exchange: 'bitget',   p50: 61,  p95: 160, p99: 340, avg: 89,  sample_count: 0 },
  { exchange: 'upbit',    p50: 75,  p95: 195, p99: 420, avg: 102, sample_count: 0 },
  { exchange: 'bithumb',  p50: 82,  p95: 210, p99: 450, avg: 115, sample_count: 0 },
];

export async function GET() {
  try {
    const r = getRedis();
    const results = await Promise.allSettled(
      MOCK_LATENCY.map(({ exchange }) => r.get(`leviathan:latency:${exchange}`))
    );

    const data = MOCK_LATENCY.map((mock, i) => {
      const result = results[i];
      if (result.status === 'fulfilled' && result.value) {
        try {
          const parsed = JSON.parse(result.value) as {
            p50?: number; p95?: number; p99?: number; avg?: number; sample_count?: number;
          };
          return {
            exchange: mock.exchange,
            p50: parsed.p50 ?? mock.p50,
            p95: parsed.p95 ?? mock.p95,
            p99: parsed.p99 ?? mock.p99,
            avg: parsed.avg ?? mock.avg,
            sample_count: parsed.sample_count ?? 0,
          };
        } catch {
          return mock;
        }
      }
      return mock;
    });

    return NextResponse.json(data);
  } catch {
    return NextResponse.json(MOCK_LATENCY);
  }
}
