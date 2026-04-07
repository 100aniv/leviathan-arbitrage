import { NextResponse } from 'next/server';
import { getRedis } from '@/lib/redis';

export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    const r = getRedis();
    const [ttlRaw, haltRaw, watchdogRaw] = await Promise.all([
      r.ttl('leviathan:heartbeat'),
      r.get('leviathan:halt'),
      r.get('leviathan:watchdog'),
    ]);

    const ttl_seconds = ttlRaw;
    const halt_flag = haltRaw === '1';
    const alive = ttl_seconds > 0;
    const watchdog_on = watchdogRaw === '1';

    return NextResponse.json({
      ttl_seconds,
      halt_flag,
      alive,
      last_seen: alive ? new Date(Date.now() - (300 - ttl_seconds) * 1000).toISOString() : null,
      watchdog_on,
    });
  } catch {
    return NextResponse.json({
      ttl_seconds: -1,
      halt_flag: false,
      alive: false,
      last_seen: null,
      watchdog_on: false,
      error: 'redis_unavailable',
    });
  }
}
