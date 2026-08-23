/**
 * Chat API.
 *
 * POST enqueues a chat turn and returns a jobId; GET polls for the answer.
 *
 * Chat is a single model call rather than the four-agent backtest pipeline, so
 * it was originally answered synchronously. That works locally and fails in
 * production: Amplify's server-side rendering has a hard 30-second request
 * timeout that cannot be raised, and a chat turn regularly exceeds it. The
 * request was cut off mid-flight with an empty body, which the browser reported
 * as "Unexpected end of JSON input".
 *
 * So chat now uses the same worker as backtests. Anything that can outlive 30
 * seconds has to leave the request cycle.
 */

import { NextRequest, NextResponse } from 'next/server';
import { LambdaClient, InvokeCommand } from '@aws-sdk/client-lambda';
import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import { DynamoDBDocumentClient, PutCommand, GetCommand } from '@aws-sdk/lib-dynamodb';
import { v4 as uuidv4 } from 'uuid';
import { consumeChatQuota } from '@/lib/quota';

const REGION = process.env.AWS_REGION || 'us-east-1';
const JOBS_TABLE = process.env.JOBS_TABLE_NAME || 'agentic-backtest-jobs';
const WORKER_FN = process.env.WORKER_FUNCTION_NAME || 'agentic-backtest-worker';

const ddb = DynamoDBDocumentClient.from(new DynamoDBClient({ region: REGION }));
const lambda = new LambdaClient({ region: REGION });

const JOB_TTL_SECONDS = 24 * 60 * 60;

export async function POST(request: NextRequest) {
  try {
    const { prompt } = await request.json();

    if (!prompt?.trim()) {
      return NextResponse.json(
        { success: false, error: 'prompt is required' },
        { status: 400 }
      );
    }

    // Chat reaches a model, so it is metered like everything else that does.
    const quota = await consumeChatQuota(undefined);
    if (!quota.allowed) {
      return NextResponse.json(
        { success: false, error: quota.reason, limitHit: quota.limitHit },
        { status: 429 }
      );
    }

    const jobId = uuidv4();
    const now = Math.floor(Date.now() / 1000);

    await ddb.send(
      new PutCommand({
        TableName: JOBS_TABLE,
        Item: {
          jobId,
          status: 'queued',
          kind: 'chat',
          startTime: Date.now(),
          updatedAt: now,
          expiresAt: now + JOB_TTL_SECONDS,
        },
      })
    );

    await lambda.send(
      new InvokeCommand({
        FunctionName: WORKER_FN,
        InvocationType: 'Event',
        Payload: Buffer.from(JSON.stringify({ jobId, mode: 'chat', prompt })),
      })
    );

    console.log(`[chat] queued job ${jobId}`);

    return NextResponse.json({ success: true, jobId });
  } catch (error: any) {
    console.error('[chat] failed to queue:', error);
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

    const status = Item.status === 'queued' ? 'processing' : Item.status;

    return NextResponse.json(
      {
        status,
        success: status === 'complete',
        message: Item.data?.message,
        error: Item.error,
      },
      { headers: { 'Cache-Control': 'no-store, no-cache, must-revalidate' } }
    );
  } catch (error: any) {
    console.error(`[chat] failed to read job ${jobId}:`, error);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
