/**
 * Backtest quota enforcement.
 *
 * Each backtest costs roughly $0.22, ~90% of it Bedrock model calls, so the
 * only control that matters is capping *invocations*. Counters live in
 * DynamoDB rather than process memory because the app may run on more than one
 * instance, and because an in-memory counter resets on every deploy — which is
 * exactly when you least want the cap to disappear.
 *
 * Increments are conditional (`count < limit`) so the check and the increment
 * are one atomic operation. A plain read-then-write would let two concurrent
 * requests both observe "109 of 110 used" and both proceed.
 *
 * This is the app-level control. The AWS Budget alarm in HostingStack is the
 * independent backstop, because everything here fails open if the app has a bug.
 */

import {
  DynamoDBClient,
  UpdateItemCommand,
  ConditionalCheckFailedException,
} from '@aws-sdk/client-dynamodb';

const TABLE = process.env.QUOTA_TABLE_NAME || 'agentic-backtest-quota';
const REGION = process.env.AWS_REGION || 'us-east-1';

// Defaults mirror infra/infra/hosting_stack.py. Overridable so the limits can
// be tightened in an incident without a redeploy of the stack.
const MONTHLY_LIMIT = Number(process.env.QUOTA_MONTHLY ?? 110);
const DAILY_LIMIT = Number(process.env.QUOTA_DAILY ?? 15);
const PER_USER_DAILY_LIMIT = Number(process.env.QUOTA_PER_USER_DAILY ?? 3);

// Set QUOTA_ENFORCED=false for local development against your own credentials.
const ENFORCED = process.env.QUOTA_ENFORCED !== 'false';

const client = new DynamoDBClient({ region: REGION });

const DAY_SECONDS = 86400;

function utcDay(d = new Date()): string {
  return d.toISOString().slice(0, 10); // YYYY-MM-DD
}

function utcMonth(d = new Date()): string {
  return d.toISOString().slice(0, 7); // YYYY-MM
}

function epoch(): number {
  return Math.floor(Date.now() / 1000);
}

interface Counter {
  id: string;
  limit: number;
  ttl: number;
  label: string;
}

function counters(subject?: string): Counter[] {
  const now = epoch();
  const list: Counter[] = [
    {
      id: `global#month#${utcMonth()}`,
      limit: MONTHLY_LIMIT,
      ttl: now + 40 * DAY_SECONDS,
      label: 'monthly capacity',
    },
    {
      id: `global#day#${utcDay()}`,
      limit: DAILY_LIMIT,
      ttl: now + 2 * DAY_SECONDS,
      label: 'daily capacity',
    },
  ];

  // Per-user fairness only becomes meaningful once auth supplies a real
  // identity. Until then the global caps carry the protection on their own —
  // an unauthenticated identifier (IP, cookie) is trivially rotated and would
  // give a false sense of per-user limiting.
  if (subject) {
    list.push({
      id: `user#${subject}#day#${utcDay()}`,
      limit: PER_USER_DAILY_LIMIT,
      ttl: now + 2 * DAY_SECONDS,
      label: 'your daily limit',
    });
  }

  return list;
}

/** Atomically increment one counter, refusing at the limit. */
async function tryIncrement(c: Counter): Promise<boolean> {
  try {
    await client.send(
      new UpdateItemCommand({
        TableName: TABLE,
        Key: { id: { S: c.id } },
        UpdateExpression:
          'SET expires_at = if_not_exists(expires_at, :ttl) ADD #count :one',
        ConditionExpression: 'attribute_not_exists(#count) OR #count < :limit',
        ExpressionAttributeNames: { '#count': 'count' },
        ExpressionAttributeValues: {
          ':one': { N: '1' },
          ':limit': { N: String(c.limit) },
          ':ttl': { N: String(c.ttl) },
        },
      })
    );
    return true;
  } catch (err) {
    if (err instanceof ConditionalCheckFailedException) return false;
    throw err;
  }
}

/** Give back a unit consumed by a counter that later failed. */
async function release(c: Counter): Promise<void> {
  try {
    await client.send(
      new UpdateItemCommand({
        TableName: TABLE,
        Key: { id: { S: c.id } },
        UpdateExpression: 'ADD #count :minusOne',
        ConditionExpression: '#count > :zero',
        ExpressionAttributeNames: { '#count': 'count' },
        ExpressionAttributeValues: {
          ':minusOne': { N: '-1' },
          ':zero': { N: '0' },
        },
      })
    );
  } catch {
    // A failed rollback only over-counts, which errs toward refusing work
    // rather than overspending. Not worth failing the request over.
  }
}

export interface QuotaResult {
  allowed: boolean;
  reason?: string;
  limitHit?: string;
}

/**
 * Consume one backtest's worth of quota.
 *
 * Counters are incremented in order and rolled back if a later one refuses, so
 * a request blocked by the monthly cap does not also burn daily capacity.
 */
export async function consumeBacktestQuota(subject?: string): Promise<QuotaResult> {
  if (!ENFORCED) return { allowed: true };

  const wanted = counters(subject);
  const consumed: Counter[] = [];

  try {
    for (const c of wanted) {
      const ok = await tryIncrement(c);
      if (!ok) {
        await Promise.all(consumed.map(release));
        return {
          allowed: false,
          limitHit: c.label,
          reason:
            c.label === 'your daily limit'
              ? `You've used your ${c.limit} backtests for today. This is a personal demo project running on a small budget — try again tomorrow.`
              : `This demo has reached its ${c.label} (${c.limit} backtests). It runs on a capped personal budget to keep it free; capacity resets shortly.`,
        };
      }
      consumed.push(c);
    }
    return { allowed: true };
  } catch (err: any) {
    // Fail CLOSED. An unreachable quota table means we cannot know the spend,
    // and unbounded model calls on a personal account is the worse failure.
    console.error('[quota] check failed, refusing request:', err?.message);
    return {
      allowed: false,
      limitHit: 'quota system',
      reason: 'Unable to verify remaining demo capacity right now. Please try again shortly.',
    };
  }
}
