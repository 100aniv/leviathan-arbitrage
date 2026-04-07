import { NextResponse } from 'next/server';
import path from 'path';
import fs from 'fs/promises';

export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    const filePath = path.join(process.cwd(), '..', '.omc', 'state', 'phase2', 'step-2-1-state.json');
    const raw = await fs.readFile(filePath, 'utf-8');
    const data = JSON.parse(raw) as unknown;
    return NextResponse.json(data);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: 'not_found', detail: message }, { status: 404 });
  }
}
