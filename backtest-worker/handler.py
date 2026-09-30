"""
Backtest worker — runs one backtest to completion and stores the result.

Exists because a backtest takes roughly a minute and the frontend is hosted
on serverless SSR, where the execution environment is frozen as soon as the
HTTP response is sent. Work started with a fire-and-forget call in a request
handler simply does not finish there.

So the API route writes a job and invokes this asynchronously; this function
does the slow part with a Lambda timeout measured in minutes, and writes the
outcome back to DynamoDB for the polling GET to read.

A backtest streams its progress: the agent runs a fixed pipeline and sends
server-sent events, one per step status change and then one result. Each step
event is written onto the job row as it arrives, so the results page shows
real progress. This function is the only writer of job state; the agent, which
executes model-written code, has no access to the table.

Chat runs through here too. It is a single model call rather than a pipeline,
but it still takes longer than the 30-second ceiling Amplify enforces on
server-side rendering — a limit that is not configurable, so anything slower
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

# The agent gets its Gateway credentials from AgentCore Identity, which needs a
# workload access token, and the runtime only issues one when the caller names
# a user. AWS treats that value as unverified, so it is this service's own fixed
# identity and never anything taken from the request.
RUNTIME_USER_ID = "backtest-worker"

_dynamodb = boto3.resource("dynamodb", region_name=REGION)
_table = _dynamodb.Table(JOBS_TABLE)

# botocore defaults are wrong for this call in two ways.
#
# read_timeout defaults to 60s. A streamed backtest goes quiet for as long as
# its slowest step, and a chat answer or a cold agent runtime can take longer
# than that, so the client would give up while the work carried on.
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


def _dynamo(value):
    """DynamoDB rejects floats; the agent's payload is full of them."""
    return json.loads(json.dumps(value), parse_float=Decimal)


def _put(job_id: str, status: str, **fields) -> None:
    item = {
        "jobId": job_id,
        "status": status,
        "updatedAt": int(time.time()),
        "expiresAt": int(time.time()) + JOB_TTL_SECONDS,
        **fields,
    }
    _table.put_item(Item=_dynamo(item))


def _update(job_id: str, **fields) -> None:
    """Set some attributes on the job row, leaving the others (the steps
    recorded so far) in place."""
    fields["updatedAt"] = int(time.time())
    _table.update_item(
        Key={"jobId": job_id},
        UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
        ExpressionAttributeNames={f"#{k}": k for k in fields},
        ExpressionAttributeValues=_dynamo({f":{k}": v for k, v in fields.items()}),
    )


def _invoke(job_id: str, payload: dict) -> dict:
    return _agentcore.invoke_agent_runtime(
        agentRuntimeArn=AGENT_ARN,
        runtimeSessionId=job_id.replace("-", "") + "0" * 8,  # >=33 chars
        payload=json.dumps(payload).encode("utf-8"),
        qualifier="DEFAULT",
        runtimeUserId=RUNTIME_USER_ID,
    )


def _events(body):
    """The JSON events of the agent's server-sent-event stream, as they arrive.

    chunk_size=1 because the default reads 1024 bytes at a time and blocks
    until it has them, which holds small step events back until later ones
    fill the buffer. One byte at a time costs about 0.1s over a whole run.
    """
    for line in body.iter_lines(chunk_size=1):
        if line.startswith(b"data: "):
            yield json.loads(line[len(b"data: "):])


def _run_backtest(job_id: str, strategy_input: dict) -> tuple:
    """Run the pipeline, recording each step as it reports. Returns the
    result event and the final step list."""
    response = _invoke(job_id, {"strategy": strategy_input})
    steps = {}  # step name -> its latest event, in the order steps first appear
    try:
        for event in _events(response["response"]):
            kind = event.get("type")
            if kind == "step":
                steps[event["step"]] = event
                _update(job_id, steps=list(steps.values()))
            elif kind == "result":
                return event, list(steps.values())
            elif "error" in event:
                # The runtime's own error event: the pipeline raised instead of
                # ending with a result, which it is written never to do.
                raise RuntimeError(f"the agent failed ({event.get('error_type')}): {event['error']}")
    finally:
        response["response"].close()
    raise RuntimeError("the agent's stream ended without a result")


def _result_data(result: dict, strategy_input: dict) -> dict:
    """The fields the results page renders."""
    return {
        "success": result.get("status") == "complete",
        "strategyInput": strategy_input,
        "strategyCode": result.get("strategy_code"),
        "trades": result.get("trades") or [],
        "trade_summary": result.get("trade_summary") or {},
        "backtest_metrics": result.get("backtest_metrics"),
        "summary_report": result.get("summary_report"),
        "data_warnings": result.get("data_warnings") or [],
        "data_period": result.get("data_period"),
        "versions": result.get("versions") or {},
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
            # so omitting it would run a backtest instead.
            response = _invoke(job_id, {"mode": "chat", "prompt": event["prompt"]})
            payload = json.loads(response["response"].read().decode("utf-8"))
            # The runtime sometimes returns a JSON *string* rather than an object.
            if isinstance(payload, str):
                payload = json.loads(payload)

            message = payload.get("result") or {}
            content = message.get("content") or [{}]
            data = {
                "success": True,
                "message": content[0].get("text", "") if content else "",
            }
            _put(job_id, "complete", data=data)
            print(f"[worker] job {job_id} chat complete in {time.time() - started:.1f}s "
                  f"({len(data['message'])} chars)")
            return {"jobId": job_id, "status": "complete"}

        strategy_input = event["strategyInput"]
        result, steps = _run_backtest(job_id, strategy_input)
        data = _result_data(result, strategy_input)

        if result.get("status") == "complete":
            _update(job_id, status="complete", steps=steps, data=data)
            print(f"[worker] job {job_id} complete in {time.time() - started:.1f}s "
                  f"({len(data['trades'])} trades)")
            return {"jobId": job_id, "status": "complete"}

        # A run that stopped at a step is an answer, not a worker failure: it
        # is recorded for the UI with the step it stopped at, and this
        # function succeeds.
        _update(job_id, status="error", steps=steps, data=data,
                failedStep=result.get("failed_step"), error=result.get("error"))
        print(f"[worker] job {job_id} stopped at {result.get('failed_step')} after "
              f"{time.time() - started:.1f}s: {result.get('error')}")
        return {"jobId": job_id, "status": "error"}

    except Exception as e:
        # Record the failure so the UI can stop polling and say something
        # useful, rather than spinning until the user gives up. An update, not
        # a put, so the steps recorded before the failure stay visible.
        print(f"[worker] job {job_id} FAILED after {time.time() - started:.1f}s: {e}")
        import traceback
        traceback.print_exc()
        _update(job_id, status="error", error=str(e))
        raise
