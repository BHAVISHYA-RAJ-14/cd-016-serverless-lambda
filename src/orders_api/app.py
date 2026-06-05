"""
orders_api/app.py
─────────────────
Orders API Lambda Handler
Handles: POST /orders, GET /orders/{orderId}, GET /orders, PUT /orders/{orderId}

Design decisions:
  - Lazy imports inside handler body → faster cold starts
  - Lambda Powertools decorators on handler → structured logs + traces
  - EventBridge publish on order create → decoupled event-driven flow
  - Step Functions execution on order create → orchestrated fulfilment
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from http import HTTPStatus
from typing import Any

# ── Powertools (imported at module level — small overhead, worth it for DI) ──
from powertools_config import logger, tracer, metrics, MetricUnit
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.validation import validate
from aws_lambda_powertools.utilities.validation.exceptions import SchemaValidationError

# ── Lazy AWS SDK imports — only initialised on first warm call ────────────────
_dynamodb  = None
_events    = None
_sfn       = None

def _get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        import boto3  # noqa: PLC0415
        _dynamodb = boto3.resource("dynamodb", region_name="ap-south-1")
    return _dynamodb

def _get_events_client():
    global _events
    if _events is None:
        import boto3  # noqa: PLC0415
        _events = boto3.client("events", region_name="ap-south-1")
    return _events

def _get_sfn_client():
    global _sfn
    if _sfn is None:
        import boto3  # noqa: PLC0415
        _sfn = boto3.client("stepfunctions", region_name="ap-south-1")
    return _sfn

# ── Environment variables ─────────────────────────────────────────────────────
ORDERS_TABLE       = os.environ["ORDERS_TABLE"]
EVENT_BUS_NAME     = os.environ["EVENT_BUS_NAME"]
ORDER_WORKFLOW_ARN = os.environ.get("ORDER_WORKFLOW_ARN", "")
ENVIRONMENT        = os.environ.get("ENVIRONMENT", "dev")

# ── JSON Schema: POST /orders request validation ──────────────────────────────
CREATE_ORDER_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema",
    "type": "object",
    "required": ["userId", "items"],
    "properties": {
        "userId": {
            "type": "string",
            "minLength": 1,
            "maxLength": 100
        },
        "items": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["productId", "quantity", "price"],
                "properties": {
                    "productId": {"type": "string"},
                    "quantity":  {"type": "integer", "minimum": 1},
                    "price":     {"type": "number",  "minimum": 0}
                }
            }
        },
        "shippingAddress": {"type": "string"}
    },
    "additionalProperties": False
}

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _response(status: int, body: Any) -> dict:
    """Build a standard API Gateway HTTP response."""
    import json
    return {
        "statusCode": status,
        "headers": {
            "Content-Type":              "application/json",
            "X-Service-Name":            "cd016-orders-api",
            "X-Environment":             ENVIRONMENT,
            "Strict-Transport-Security": "max-age=31536000",
        },
        "body": json.dumps(body, default=str),
    }


def _decimal_to_float(obj: Any) -> Any:
    """DynamoDB returns Decimals; convert to float for JSON serialisation."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _decimal_to_float(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimal_to_float(i) for i in obj]
    return obj


def _calculate_total(items: list[dict]) -> float:
    return sum(item["quantity"] * item["price"] for item in items)


# ─────────────────────────────────────────────────────────────────────────────
# ROUTE HANDLERS
# ─────────────────────────────────────────────────────────────────────────────

@tracer.capture_method
def _create_order(body: dict, correlation_id: str) -> dict:
    """POST /orders — validate, persist, publish event, start workflow."""
    import json

    # Schema validation (raises SchemaValidationError on failure)
    validate(event=body, schema=CREATE_ORDER_SCHEMA)

    order_id  = str(uuid.uuid4())
    now       = datetime.now(timezone.utc).isoformat()
    total     = _calculate_total(body["items"])

    order = {
        "orderId":         order_id,
        "userId":          body["userId"],
        "items":           body["items"],
        "shippingAddress": body.get("shippingAddress", ""),
        "status":          "PENDING",
        "total":           Decimal(str(round(total, 2))),
        "correlationId":   correlation_id,
        "createdAt":       now,
        "updatedAt":       now,
    }

    # Persist to DynamoDB
    table = _get_dynamodb().Table(ORDERS_TABLE)
    table.put_item(Item=order)
    logger.info("Order persisted to DynamoDB", extra={"orderId": order_id})

    # Publish OrderPlaced event to EventBridge
    event_detail = {
        "orderId":  order_id,
        "userId":   body["userId"],
        "items":    body["items"],
        "total":    float(total),
        "correlationId": correlation_id,
    }
    _get_events_client().put_events(
        Entries=[{
            "Source":       "cd016.orders",
            "DetailType":   "OrderPlaced",
            "Detail":       json.dumps(event_detail),
            "EventBusName": EVENT_BUS_NAME,
        }]
    )
    logger.info("OrderPlaced event published", extra={"orderId": order_id})

    # Start Step Functions order fulfilment workflow
    if ORDER_WORKFLOW_ARN:
        _get_sfn_client().start_execution(
            stateMachineArn=ORDER_WORKFLOW_ARN,
            name=f"order-{order_id}",
            input=json.dumps(event_detail),
        )
        logger.info("Step Functions workflow started", extra={"orderId": order_id})

    # EMF custom metric
    metrics.add_metric(name="OrderCreated", unit=MetricUnit.Count, value=1)
    metrics.add_metadata(key="orderId", value=order_id)

    return _response(HTTPStatus.CREATED, {
        "message": "Order created successfully",
        "orderId": order_id,
        "status":  "PENDING",
        "total":   float(total),
    })


@tracer.capture_method
def _get_order(order_id: str) -> dict:
    """GET /orders/{orderId}"""
    table = _get_dynamodb().Table(ORDERS_TABLE)
    result = table.get_item(Key={"orderId": order_id})
    item   = result.get("Item")

    if not item:
        return _response(HTTPStatus.NOT_FOUND, {"message": f"Order {order_id} not found"})

    return _response(HTTPStatus.OK, _decimal_to_float(item))


@tracer.capture_method
def _list_orders(query_params: dict) -> dict:
    """GET /orders — scan with optional userId filter."""
    from boto3.dynamodb.conditions import Key

    table   = _get_dynamodb().Table(ORDERS_TABLE)
    user_id = (query_params or {}).get("userId")

    if user_id:
        result = table.query(
            IndexName="userId-index",
            KeyConditionExpression=Key("userId").eq(user_id),
        )
    else:
        result = table.scan(Limit=50)

    orders = _decimal_to_float(result.get("Items", []))
    return _response(HTTPStatus.OK, {"orders": orders, "count": len(orders)})


@tracer.capture_method
def _update_order(order_id: str, body: dict) -> dict:
    """PUT /orders/{orderId} — partial update (status, shippingAddress)."""
    allowed = {"status", "shippingAddress"}
    updates  = {k: v for k, v in body.items() if k in allowed}

    if not updates:
        return _response(HTTPStatus.BAD_REQUEST, {"message": "No valid fields to update"})

    table      = _get_dynamodb().Table(ORDERS_TABLE)
    now        = datetime.now(timezone.utc).isoformat()
    updates["updatedAt"] = now

    expr       = "SET " + ", ".join(f"#k{i} = :v{i}" for i, k in enumerate(updates))
    names      = {f"#k{i}": k for i, k in enumerate(updates)}
    values     = {f":v{i}": v for i, v in enumerate(updates.values())}

    table.update_item(
        Key={"orderId": order_id},
        UpdateExpression=expr,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )

    metrics.add_metric(name="OrderUpdated", unit=MetricUnit.Count, value=1)
    return _response(HTTPStatus.OK, {"message": "Order updated", "orderId": order_id})


# ─────────────────────────────────────────────────────────────────────────────
# MAIN HANDLER
# ─────────────────────────────────────────────────────────────────────────────

@logger.inject_lambda_context(correlation_id_path="requestContext.requestId", log_event=True)
@tracer.capture_lambda_handler
@metrics.log_metrics(capture_cold_start_metric=True)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    """
    Main entry point — routes HTTP method + path to correct handler.
    Decorators (order matters):
      1. inject_lambda_context  → adds correlation ID + cold_start to all logs
      2. capture_lambda_handler → wraps entire handler as X-Ray subsegment
      3. log_metrics            → flushes EMF metrics at end of invocation
    """
    import json

    method      = event.get("requestContext", {}).get("http", {}).get("method", "")
    path        = event.get("requestContext", {}).get("http", {}).get("path", "")
    raw_body    = event.get("body", "{}")
    query_params= event.get("queryStringParameters") or {}
    path_params = event.get("pathParameters") or {}
    correlation = event.get("requestContext", {}).get("requestId", str(uuid.uuid4()))

    # Parse body safely
    try:
        body = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        return _response(HTTPStatus.BAD_REQUEST, {"message": "Invalid JSON body"})

    logger.info("Routing request", extra={
        "method": method,
        "path":   path,
        "environment": ENVIRONMENT,
    })

    try:
        # ── Route: Health check ──────────────────
        if "/health" in path:
            return _response(HTTPStatus.OK, {
                "status": "healthy",
                "service": "cd016-orders-api",
                "environment": ENVIRONMENT,
            })

        # ── Route: POST /orders ──────────────────
        if method == "POST" and "/orders" in path:
            return _create_order(body, correlation)

        # ── Route: GET /orders/{orderId} ─────────
        if method == "GET" and path_params.get("orderId"):
            return _get_order(path_params["orderId"])

        # ── Route: GET /orders ───────────────────
        if method == "GET":
            return _list_orders(query_params)

        # ── Route: PUT /orders/{orderId} ─────────
        if method == "PUT" and path_params.get("orderId"):
            return _update_order(path_params["orderId"], body)

        return _response(HTTPStatus.METHOD_NOT_ALLOWED, {"message": f"Method {method} not allowed"})

    except SchemaValidationError as exc:
        logger.warning("Request validation failed", extra={"error": str(exc)})
        return _response(HTTPStatus.UNPROCESSABLE_ENTITY, {
            "message": "Request validation failed",
            "details": str(exc),
        })

    except Exception as exc:
        logger.exception("Unhandled exception", extra={"error": str(exc)})
        metrics.add_metric(name="OrderApiError", unit=MetricUnit.Count, value=1)
        return _response(HTTPStatus.INTERNAL_SERVER_ERROR, {
            "message": "Internal server error",
            "requestId": correlation,
        })
