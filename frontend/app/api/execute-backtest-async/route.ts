/**
 * Backtest job API.
 *
 * POST enqueues a backtest and returns immediately; GET polls for the result.
 *
 * Both the job state and the slow work live outside this route on purpose. A
 * backtest takes ~85 seconds, and serverless SSR freezes its execution
 * environment as soon as a response is sent — so an un-awaited background task
 * started here would be killed mid-flight, and a per-instance in-memory job map
 * would be invisible to whichever instance served the next poll.
 *
 * The route therefore does only fast, stateless things: check quota, write a
 * job row, kick the worker, read a job row.
 */

import { NextRequest, NextResponse } from 'next/server';
import { LambdaClient, InvokeCommand } from '@aws-sdk/client-lambda';
import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import { DynamoDBDocumentClient, PutCommand, GetCommand } from '@aws-sdk/lib-dynamodb';
import { v4 as uuidv4 } from 'uuid';
import { consumeBacktestQuota } from '@/lib/quota';

const REGION = process.env.AWS_REGION || 'us-east-1';
const JOBS_TABLE = process.env.JOBS_TABLE_NAME || 'agentic-backtest-jobs';
const WORKER_FN = process.env.WORKER_FUNCTION_NAME || 'agentic-backtest-worker';

const ddb = DynamoDBDocumentClient.from(new DynamoDBClient({ region: REGION }));
const lambda = new LambdaClient({ region: REGION });

const JOB_TTL_SECONDS = 24 * 60 * 60;

export async function POST(request: NextRequest) {
  try {
    const strategyInput = await request.json();

    // Gate before anything is spent. This is the only path that triggers model
    // calls (~$0.22 each, ~90% Bedrock), so the cap belongs at the front of it.
    const quota = await consumeBacktestQuota(undefined);
    if (!quota.allowed) {
      return NextResponse.json(
        { success: false, error: quota.reason, limitHit: quota.limitHit },
        { status: 429 }
      );
    }

    const jobId = uuidv4();
    const now = Math.floor(Date.now() / 1000);

    // Write the job before invoking, so a poll arriving before the worker has
    // started finds "queued" rather than a 404.
    await ddb.send(
      new PutCommand({
        TableName: JOBS_TABLE,
        Item: {
          jobId,
          status: 'queued',
          startTime: Date.now(),
          updatedAt: now,
          expiresAt: now + JOB_TTL_SECONDS,
        },
      })
    );

    // Event invocation: returns as soon as Lambda accepts the payload, and the
    // worker keeps running after this request is gone.
    await lambda.send(
      new InvokeCommand({
        FunctionName: WORKER_FN,
        InvocationType: 'Event',
        Payload: Buffer.from(JSON.stringify({ jobId, strategyInput })),
      })
    );

    console.log(`[API] queued job ${jobId}`);

    return NextResponse.json({
      success: true,
      jobId,
      message: 'Backtest started. Poll this endpoint with ?jobId= for results.',
    });
  } catch (error: any) {
    console.error('[API] failed to queue backtest:', error);
    return NextResponse.json(
      { success: false, error: error.message },
      { status: 500 }
    );
  }
}

export async function GET(request: NextRequest) {
  const jobId = new URL(request.url).searchParams.get('jobId');

  if (!jobId) {
    return NextResponse.json({ error: 'jobId required' }, { status: 400 });
  }

  try {
    const { Item } = await ddb.send(
      new GetCommand({ TableName: JOBS_TABLE, Key: { jobId } })
    );

    if (!Item) {
      return NextResponse.json(
        { error: 'Job not found. It may have expired.', jobId },
        { status: 404 }
      );
    }

    // 'queued' is an implementation detail of the handoff; the UI only
    // distinguishes "still working" from "done".
    const status = Item.status === 'queued' ? 'processing' : Item.status;

    return NextResponse.json(
      { status, data: Item.data, error: Item.error, startTime: Item.startTime },
      { headers: { 'Cache-Control': 'no-store, no-cache, must-revalidate' } }
    );
  } catch (error: any) {
    console.error(`[API] failed to read job ${jobId}:`, error);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
