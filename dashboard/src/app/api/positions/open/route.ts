import { NextResponse } from 'next/server';
import { getRedis } from '@/lib/redis';

export const dynamic = 'force-dynamic';

interface PositionRow {
  key: string;
  strategy_id: string;
  exchange_id: string;
  symbol: string;
  side: string;
  qty: string;
  entry_price: string;
  unrealized_pnl: string;
  hold_seconds: number;
}

export async function GET() {
  try {
    const r = getRedis();
    const keys = await r.keys('leviathan:position:*');
    if (keys.length === 0) {
      return NextResponse.json([]);
    }

    const pipeline = r.pipeline();
    for (const k of keys) {
      pipeline.hgetall(k);
    }
    const results = await pipeline.exec();

    const positions: PositionRow[] = [];
    if (results) {
      for (let i = 0; i < keys.length; i++) {
        const [err, data] = results[i] as [Error | null, Record<string, string> | null];
        if (err || !data) continue;
        // key format: leviathan:position:{strategy_id}:{exchange_id}:{symbol}
        const parts = keys[i].split(':');
        const strategy_id = parts[2] ?? '';
        const exchange_id = parts[3] ?? '';
        const symbol = parts.slice(4).join(':');

        const entry_ts = data.entry_timestamp ? parseInt(data.entry_timestamp, 10) : 0;
        const hold_seconds = entry_ts > 0 ? Math.floor((Date.now() / 1000) - entry_ts) : 0;

        positions.push({
          key: keys[i],
          strategy_id,
          exchange_id,
          symbol,
          side: data.side ?? '',
          qty: data.qty ?? '',
          entry_price: data.entry_price ?? '',
          unrealized_pnl: data.unrealized_pnl ?? '0',
          hold_seconds,
        });
      }
    }

    return NextResponse.json(positions);
  } catch {
    return NextResponse.json([]);
  }
}
