# Container image rather than a zip Lambda: bedrock-agentcore is recent enough
# that the runtime's bundled boto3 cannot be relied on to include it, and
# pinning the SDK here removes that guesswork. Matches the pattern already used
# by market-data-mcp.
FROM public.ecr.aws/lambda/python:3.12-arm64

COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install --no-cache-dir -r requirements.txt

COPY handler.py ${LAMBDA_TASK_ROOT}

CMD ["handler.handler"]
