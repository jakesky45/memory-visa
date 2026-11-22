FROM --platform=linux/arm64 python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir boto3
COPY src/ .
EXPOSE 8080
# Without this, stdout is block-buffered for a long-running, non-TTY process -
# confirmed live: every _log_usage() print() this container ever made sat in
# an unflushed buffer and never reached CloudWatch, while stderr tracebacks
# (line-buffered by default) did. The audit-logging feature was silently
# dead from day one until this was caught.
ENV PYTHONUNBUFFERED=1
CMD ["python3", "server.py"]
