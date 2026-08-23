"""
Backtest worker — runs one backtest to completion and stores the result.

Exists because a backtest takes roughly 85 seconds and the frontend is hosted
on serverless SSR, where the execution environment is frozen as soon as the
HTTP response is sent. Work started with a fire-and-forget call in a request
handler simply does not finish there.

So the API route writes a job and invokes this asynchronously; this function
does the slow part with a Lambda timeout measured in minutes, and writes the
outcome back to DynamoDB for the polling GET to read.

Chat runs through here too. It is a single model call rather than a four-agent
pipeline, but it still takes longer than the 30-second ceiling Amplify enforces
on server-side rendering — a limit that is not configurable, so anything slower
has to leave the request cycle.

Event shapes:
    {"jobId": "...", "strategyInput": {...}}       # backtest
    {"jobId": "...", "mode": "chat", "prompt": "..."}
"""

import json
import os
import time
from decimal import Decimal

import boto3
from botocore.config import Config

AGENT_ARN = os.environ["AGENTCORE_ARN"]
JOBS_TABLE = os.environ["JOBS_TABLE_NAME"]
REGION = os.environ.get("AWS_REGION", "us-east-1")

# Jobs are transient UI state; a day is far longer than any poll needs.
JOB_TTL_SECONDS = 24 * 60 * 60

_dynamodb = boto3.resource("dynamodb", region_name=REGION)
_table = _dynamodb.Table(JOBS_TABLE)

# botocore defaults are wrong for this call in two ways.
#
# read_timeout defaults to 60s, but a backtest takes ~85s and a cold agent
# runtime can push it past two minutes — the client gave up long before the
# work finished, while the Lambda sat waiting on it.
#
# retries default to on, which is worse than the timeout: a retried invoke runs
# a second complete backtest at full model cost for a request the user has
# already been told failed. max_attempts=1 in standard mode means one attempt,
# no retries. This operation is expensive and not idempotent — if it fails, it
# should fail once and visibly.
_agentcore = boto3.client(
    "bedrock-agentcore",
    region_name=REGION,
    config=Config(
        connect_timeout=10,
        read_timeout=600,
        retries={"max_attempts": 1, "mode": "standard"},
    ),
)


def _put(job_id: str, status: str, **fields) -> None:
    item = {
        "jobId": job_id,
        "status": status,
        "updatedAt": int(time.time()),
        "expiresAt": int(time.time()) + JOB_TTL_SECONDS,
        **fields,
    }
    # DynamoDB rejects floats; the agent's payload is full of them.
    _table.put_item(Item=json.loads(json.dumps(item), parse_float=Decimal))


def _extract(payload: dict) -> dict:
    """Flatten the agent response into the shape the results page expects.

    Mirrors the extraction the API route used to do inline. `result` is the
    message envelope `{role, content:[{text}]}`; the numeric fields sit
    alongside it because the tools set them directly rather than relying on
    the model to echo them back.
    """
    message = payload.get("result") or {}
    content = message.get("content") or [{}]
    analysis = content[0].get("text", "") if content else ""

    return {
        "success": True,
        "analysis": analysis,
        "verdict": payload.get("verdict"),
        "strategyCode": payload.get("strategy_code"),
        "trades": payload.get("trades", []),
        "trade_summary": payload.get("trade_summary", {}),
        "backtest_metrics": payload.get("backtest_metrics"),
        "summary_report": payload.get("summary_report"),
        "versions": payload.get("versions", {}),
    }


def handler(event, context):
    job_id = event["jobId"]
    mode = event.get("mode", "backtest")

    print(f"[worker] starting job {job_id} (mode={mode})")
    started = time.time()

    try:
        _put(job_id, "processing", startTime=int(started * 1000))

        if mode == "chat":
            # The agent dispatches on this field and defaults to "backtest",
            # so omitting it silently runs the whole pipeline instead.
            agent_payload = {"mode": "chat", "prompt": event["prompt"]}
        else:
            strategy_input = event["strategyInput"]
            agent_payload = {
                "prompt": f"how is the strategy performance: {json.dumps(strategy_input)}"
            }

        response = _agentcore.invoke_agent_runtime(
            agentRuntimeArn=AGENT_ARN,
            runtimeSessionId=job_id.replace("-", "") + "0" * 8,  # >=33 chars
            payload=json.dumps(agent_payload).encode("utf-8"),
            qualifier="DEFAULT",
        )

        raw = response["response"].read().decode("utf-8")
        payload = json.loads(raw)
        # The runtime sometimes returns a JSON *string* rather than an object.
        if isinstance(payload, str):
            payload = json.loads(payload)

        if mode == "chat":
            message = payload.get("result") or {}
            content = message.get("content") or [{}]
            data = {
                "success": True,
                "message": content[0].get("text", "") if content else "",
            }
            _put(job_id, "complete", data=data)
            print(f"[worker] job {job_id} chat complete in {time.time() - started:.1f}s "
                  f"({len(data['message'])} chars)")
        else:
            data = _extract(payload)
            data["strategyInput"] = event["strategyInput"]
            _put(job_id, "complete", data=data)
            print(f"[worker] job {job_id} complete in {time.time() - started:.1f}s "
                  f"({len(data.get('trades') or [])} trades)")

    except Exception as e:
        # Record the failure so the UI can stop polling and say something
        # useful, rather than spinning until the user gives up.
        print(f"[worker] job {job_id} FAILED after {time.time() - started:.1f}s: {e}")
        import traceback
        traceback.print_exc()
        _put(job_id, "error", error=str(e))
        raise

    return {"jobId": job_id, "status": "complete"}
