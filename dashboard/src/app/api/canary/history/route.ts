import { NextResponse } from 'next/server';
import path from 'path';
import fs from 'fs/promises';

export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    const dir = path.join(process.cwd(), '..', '.omc', 'state', 'phase2');
    const entries = await fs.readdir(dir);
    const jsonFiles = entries.filter((f) => f.endsWith('.json'));

    const results: unknown[] = [];
    for (const file of jsonFiles) {
      try {
        const raw = await fs.readFile(path.join(dir, file), 'utf-8');
        results.push(JSON.parse(raw) as unknown);
      } catch {
        // skip malformed files
      }
    }

    return NextResponse.json(results);
  } catch {
    return NextResponse.json([]);
  }
}
