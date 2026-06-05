"""
payment/app.py
──────────────
Payment Processing Lambda
Triggered by: EventBridge → source=cd016.orders, detail-type=OrderPlaced

Responsibilities:
  - Process payment for the order total
  - Update order status in DynamoDB
  - Publish PaymentProcessed or PaymentFailed event
"""

from __future__ import annotations

import json
import os
import random
from datetime import datetime, timezone
from decimal import Decimal

from aws_lambda_powertools import Logger, Tracer, Metrics
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities.typing import LambdaContext

logger  = Logger(service="cd016-payment")
tracer  = Tracer(service="cd016-payment")
metrics = Metrics(namespace="CD016/Serverless", service="cd016-payment")

_dynamodb = None
_events   = None

def _get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        import boto3
        _dynamodb = boto3.resource("dynamodb", region_name="ap-south-1")
    return _dynamodb

def _get_events():
    global _events
    if _events is None:
        import boto3
        _events = boto3.client("events", region_name="ap-south-1")
    return _events

ORDERS_TABLE   = os.environ["ORDERS_TABLE"]
EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME", "")


@tracer.capture_method
def _process_payment(order_id: str, total: float) -> dict:
    """
    Simulate payment gateway call.
    In production: integrate Stripe / Razorpay / PayU.
    """
    success       = random.random() > 0.03        # 97% success rate
    transaction_id = f"txn-{order_id[:8]}-{int(datetime.now().timestamp())}"

    return {
        "success":       success,
        "transactionId": transaction_id if success else None,
        "amount":        total,
        "currency":      "INR",
        "gateway":       "simulated",
        "failureReason": None if success else "INSUFFICIENT_FUNDS",
    }


@tracer.capture_method
def _update_order(order_id: str, status: str, payment: dict) -> None:
    table = _get_dynamodb().Table(ORDERS_TABLE)
    table.update_item(
        Key={"orderId": order_id},
        UpdateExpression="SET #s = :s, paymentResult = :p, updatedAt = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": status,
            ":p": json.dumps(payment),
            ":t": datetime.now(timezone.utc).isoformat(),
        },
    )


@logger.inject_lambda_context(log_event=True)
@tracer.capture_lambda_handler
@metrics.log_metrics(capture_cold_start_metric=True)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    detail         = event.get("detail", {})
    order_id       = detail.get("orderId", "")
    user_id        = detail.get("userId", "")
    total          = float(detail.get("total", 0.0))
    correlation_id = detail.get("correlationId", "")

    logger.info("Processing payment", extra={"orderId": order_id, "total": total})
    tracer.put_annotation(key="orderId", value=order_id)

    payment    = _process_payment(order_id, total)
    new_status = "PAYMENT_PROCESSED" if payment["success"] else "PAYMENT_FAILED"

    _update_order(order_id, new_status, payment)

    detail_type = "PaymentProcessed" if payment["success"] else "PaymentFailed"
    _get_events().put_events(
        Entries=[{
            "Source":       "cd016.payment",
            "DetailType":   detail_type,
            "Detail":       json.dumps({
                "orderId":       order_id,
                "userId":        user_id,
                "payment":       payment,
                "correlationId": correlation_id,
            }),
            "EventBusName": EVENT_BUS_NAME,
        }]
    )

    metrics.add_metric(name="PaymentAttempts", unit=MetricUnit.Count, value=1)
    if payment["success"]:
        metrics.add_metric(name="PaymentSuccess",    unit=MetricUnit.Count,  value=1)
        metrics.add_metric(name="RevenueProcessed",  unit=MetricUnit.Count,  value=int(total))
    else:
        metrics.add_metric(name="PaymentFailed",     unit=MetricUnit.Count,  value=1)

    logger.info(f"{detail_type} completed", extra={
        "orderId":       order_id,
        "transactionId": payment.get("transactionId"),
        "success":       payment["success"],
    })

    return {
        "orderId": order_id,
        "status":  new_status,
        "payment": payment,
    }
