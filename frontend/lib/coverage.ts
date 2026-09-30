/**
 * Market-data coverage, as published by the ingest pipeline.
 *
 * After every run the pipeline writes each symbol's stored date range to an
 * SSM parameter. The form uses it to offer only backtest windows the data can
 * back, and the health check uses it for freshness — so neither keeps its own
 * copy of what the table holds.
 *
 * Server-side only: reads SSM with the route's IAM role.
 */

import { SSMClient, GetParameterCommand } from '@aws-sdk/client-ssm';
import type { Coverage } from '@/types/strategy';

const REGION = process.env.AWS_REGION || 'us-east-1';
const PARAM = process.env.COVERAGE_PARAM_NAME || '/agentic-backtest/market-data-coverage';

// The value changes once a day, so a few minutes of per-instance caching keeps
// SSM calls negligible without ever serving meaningfully stale coverage.
const CACHE_MS = 5 * 60 * 1000;

const ssm = new SSMClient({ region: REGION });

export interface PublishedCoverage {
  generated_at: string;
  symbols: Coverage;
}

let cached: { value: PublishedCoverage; at: number } | null = null;

export async function readCoverage(): Promise<PublishedCoverage> {
  if (cached && Date.now() - cached.at < CACHE_MS) return cached.value;

  const { Parameter } = await ssm.send(new GetParameterCommand({ Name: PARAM }));
  if (!Parameter?.Value) throw new Error(`coverage parameter ${PARAM} is empty`);

  const value = JSON.parse(Parameter.Value) as PublishedCoverage;
  cached = { value, at: Date.now() };
  return value;
}
