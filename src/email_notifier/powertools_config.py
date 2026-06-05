"""
powertools_config.py
────────────────────
Centralised Lambda Powertools setup.
Import this in every Lambda handler to get:
  - Structured JSON logging with correlation IDs
  - X-Ray tracing with subsegments per external call
  - Custom CloudWatch metrics via EMF
  - Idempotency utilities

Usage in any Lambda handler:
    from powertools_config import logger, tracer, metrics
"""

import os
from aws_lambda_powertools import Logger, Tracer, Metrics
from aws_lambda_powertools.metrics import MetricUnit  # noqa: F401 — re-exported for handlers

# ── Service name pulled from env (set in template.yaml Globals) ──────────────
SERVICE_NAME = os.environ.get("POWERTOOLS_SERVICE_NAME", "cd016-serverless")
ENVIRONMENT  = os.environ.get("ENVIRONMENT", "dev")

# ── Logger ────────────────────────────────────────────────────────────────────
# Outputs structured JSON automatically:
# {
#   "level": "INFO",
#   "message": "...",
#   "service": "cd016-serverless",
#   "correlation_id": "...",   ← injected by @logger.inject_lambda_context
#   "cold_start": true/false,
#   "function_name": "...",
#   "timestamp": "..."
# }
logger = Logger(
    service=SERVICE_NAME,
    level=os.environ.get("POWERTOOLS_LOG_LEVEL", "INFO"),
)

# ── Tracer ────────────────────────────────────────────────────────────────────
# Auto-instruments boto3 calls with X-Ray subsegments.
# Wraps handler with @tracer.capture_lambda_handler
# Wraps methods with @tracer.capture_method
tracer = Tracer(service=SERVICE_NAME)

# ── Metrics ───────────────────────────────────────────────────────────────────
# Emits custom CloudWatch metrics via Embedded Metrics Format (EMF).
# Zero cost for <10 metrics/month on free tier.
# Usage: metrics.add_metric(name="OrderCreated", unit=MetricUnit.Count, value=1)
metrics = Metrics(
    namespace="CD016/Serverless",
    service=SERVICE_NAME,
)
