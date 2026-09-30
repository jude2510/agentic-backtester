"""
Calls from the pipeline to the sub-agent runtimes (strategy generator,
results summary), with the pipeline's failure semantics: a timeout or a bad
response becomes a StepError the user can read, never a retry.
"""

import json
import os
import uuid

from botocore.exceptions import ReadTimeoutError

import config
from step_types import StepError


def invoke_sub_agent(arn_env: str, payload: dict, label: str) -> dict:
    """Invoke the runtime named by `arn_env` once and return its JSON object."""
    arn = os.getenv(arn_env)
    if not arn:
        raise StepError(f"{arn_env} is not set, so the {label} can't be reached")

    try:
        response = config._agentcore_runtime_client.invoke_agent_runtime(
            agentRuntimeArn=arn,
            runtimeSessionId=str(uuid.uuid4()),
            payload=json.dumps(payload).encode("utf-8"),
            qualifier="DEFAULT",
        )
        data = json.loads(response["response"].read().decode("utf-8"))
    except ReadTimeoutError:
        raise StepError(f"the {label} did not answer within "
                        f"{config.SUB_AGENT_TIMEOUT_SECONDS} seconds", status="timeout")
    except Exception as e:
        raise StepError(f"the {label} call failed: {e}")

    # The runtime sometimes returns the object wrapped in a JSON string.
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            pass
    if not isinstance(data, dict):
        raise StepError(f"the {label} returned a response in an unexpected shape")
    return data
