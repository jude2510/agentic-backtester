FROM public.ecr.aws/lambda/python:3.12-arm64

COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install --no-cache-dir -r requirements.txt

COPY providers/ ${LAMBDA_TASK_ROOT}/providers/
COPY ingest.py iceberg_writer.py lambda_handler.py symbols.json ${LAMBDA_TASK_ROOT}/

CMD ["lambda_handler.handler"]
