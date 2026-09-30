import { NextResponse } from 'next/server';
import { readCoverage } from '@/lib/coverage';

// Read on every request (through the module's short cache), never prerendered
// at build time — otherwise the site would report build-day coverage forever.
export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    const coverage = await readCoverage();
    return NextResponse.json(coverage, {
      headers: { 'Cache-Control': 'public, max-age=300' },
    });
  } catch (error: any) {
    // SSM sends some errors (e.g. ParameterNotFound) with an empty message,
    // so the name is what says what went wrong.
    console.error('[API] coverage unavailable:', error?.name, error?.message);
    return NextResponse.json(
      { error: 'Market-data coverage is unavailable right now.' },
      { status: 503 }
    );
  }
}
