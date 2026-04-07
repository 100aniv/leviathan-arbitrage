import { NextResponse } from 'next/server';
import { getRedis } from '@/lib/redis';

export const dynamic = 'force-dynamic';

export async function POST() {
  const dry_run = process.env.HALT_DRY_RUN === '1';
  try {
    const r = getRedis();
    if (!dry_run) {
      await r.set('leviathan:halt', '1', 'EX', 86400);
    }
    return NextResponse.json({ ok: true, dry_run });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ ok: false, error: message }, { status: 500 });
  }
}
