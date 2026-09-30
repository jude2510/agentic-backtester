/**
 * Health check: can the site serve a trustworthy backtest right now?
 *
 * Checks the two things that fail silently rather than loudly — market data
 * going stale, and the quota table becoming unreachable (which makes every
 * request fail closed) — and reports remaining capacity. It makes no model
 * calls, so polling it costs nothing.
 *
 * Returns 200 when healthy and 503 when degraded, for uptime monitors.
 */

import { NextResponse } from 'next/server';
import { readCoverage } from '@/lib/coverage';
import { readBacktestUsage } from '@/lib/quota';

// Evaluated on every request. Before this, the route was prerendered at build
// time and reported "healthy" regardless of the system's actual state.
export const dynamic = 'force-dynamic';

// The same threshold the pipeline and the agent use: a long weekend is 3-4 days.
const MAX_STALENESS_DAYS = 5;

export async function GET() {
  const checks: Record<string, unknown> = {};
  let healthy = true;

  try {
    const coverage = await readCoverage();
    const ends = Object.values(coverage.symbols).map(s => s.end).sort();
    const latest = ends[ends.length - 1];
    const staleDays = Math.floor((Date.now() - Date.parse(latest)) / 86_400_000);
    const ok = staleDays <= MAX_STALENESS_DAYS;
    checks.market_data = {
      ok,
      latest_bar: latest,
      days_old: staleDays,
      symbols: Object.keys(coverage.symbols).length,
      published_at: coverage.generated_at,
    };
    healthy = healthy && ok;
  } catch (error: any) {
    console.error('[health] coverage check failed:', error?.name, error?.message);
    checks.market_data = { ok: false, error: 'coverage unavailable' };
    healthy = false;
  }

  try {
    const usage = await readBacktestUsage();
    // Running out of capacity is normal operation, not ill health; report it.
    checks.capacity = {
      ok: true,
      backtests_today: usage.today.used,
      daily_limit: usage.today.limit,
      backtests_this_month: usage.month.used,
      monthly_limit: usage.month.limit,
    };
  } catch (error: any) {
    console.error('[health] quota check failed:', error?.name, error?.message);
    checks.capacity = { ok: false, error: 'quota table unreachable' };
    healthy = false;
  }

  return NextResponse.json(
    { status: healthy ? 'healthy' : 'degraded', checked_at: new Date().toISOString(), checks },
    { status: healthy ? 200 : 503, headers: { 'Cache-Control': 'no-store' } }
  );
}
