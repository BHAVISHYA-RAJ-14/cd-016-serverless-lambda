"""
email_notifier/app.py
─────────────────────
Email Notification Lambda
Triggered by EventBridge rules for:
  1. OrderPlaced          → "Your order has been received"
  2. PaymentProcessed     → "Payment confirmed"
  3. OrderFulfilled       → "Your order is on its way"

Uses SNS for actual delivery (configured in template.yaml).
In production: swap SNS for SES with HTML templates.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from aws_lambda_powertools import Logger, Tracer, Metrics
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities.typing import LambdaContext

logger  = Logger(service="cd016-email-notifier")
tracer  = Tracer(service="cd016-email-notifier")
metrics = Metrics(namespace="CD016/Serverless", service="cd016-email-notifier")

_sns = None

def _get_sns():
    global _sns
    if _sns is None:
        import boto3
        _sns = boto3.client("sns", region_name="ap-south-1")
    return _sns

ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")

# ── Email templates ───────────────────────────────────────────────────────────
TEMPLATES = {
    "OrderPlaced": {
        "subject": "Order Received — #{orderId}",
        "body":    "Hi {userId},\n\nYour order #{orderId} has been received and is being processed.\nTotal: ₹{total}\n\nThank you for your order!\n\nCD-016 Store",
    },
    "PaymentProcessed": {
        "subject": "Payment Confirmed — #{orderId}",
        "body":    "Hi {userId},\n\nPayment for order #{orderId} has been confirmed.\nTransaction ID: {transactionId}\n\nYour order is being prepared for shipping.\n\nCD-016 Store",
    },
    "OrderFulfilled": {
        "subject": "Order Shipped — #{orderId}",
        "body":    "Hi {userId},\n\nYour order #{orderId} has been fulfilled and is on its way!\n\nThank you for shopping with us.\n\nCD-016 Store",
    },
    "InventoryFailed": {
        "subject": "Order Issue — #{orderId}",
        "body":    "Hi {userId},\n\nUnfortunately, some items in order #{orderId} are out of stock.\nOur team will reach out shortly.\n\nCD-016 Store",
    },
    "PaymentFailed": {
        "subject": "Payment Failed — #{orderId}",
        "body":    "Hi {userId},\n\nPayment for order #{orderId} could not be processed.\nPlease try again or use a different payment method.\n\nCD-016 Store",
    },
}


@tracer.capture_method
def _send_notification(event_type: str, detail: dict) -> bool:
    """
    Build email from template and publish to SNS.
    SNS → email subscription delivers to user inbox.
    """
    template = TEMPLATES.get(event_type)
    if not template:
        logger.warning(f"No template for event type: {event_type}")
        return False

    order_id       = detail.get("orderId", "N/A")
    user_id        = detail.get("userId", "customer")
    total          = detail.get("total", detail.get("payment", {}).get("amount", 0))
    transaction_id = detail.get("payment", {}).get("transactionId", "N/A")

    subject = template["subject"].format(orderId=order_id[:8])
    body    = template["body"].format(
        orderId=order_id[:8],
        userId=user_id,
        total=total,
        transactionId=transaction_id,
    )

    logger.info("Sending notification", extra={
        "eventType": event_type,
        "orderId":   order_id,
        "subject":   subject,
    })

    # In dev: just log the notification (avoid real SNS emails during testing)
    if ENVIRONMENT == "dev":
        logger.info("DEV MODE — notification logged (not sent)", extra={
            "subject": subject,
            "body":    body,
        })
        return True

    # In prod: publish to SNS
    _get_sns().publish(
        TopicArn=os.environ.get("NOTIFICATION_TOPIC_ARN", ""),
        Subject=subject,
        Message=body,
        MessageAttributes={
            "userId":    {"DataType": "String", "StringValue": user_id},
            "eventType": {"DataType": "String", "StringValue": event_type},
        },
    )
    return True


@logger.inject_lambda_context(log_event=True)
@tracer.capture_lambda_handler
@metrics.log_metrics(capture_cold_start_metric=True)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    """
    EventBridge delivers events with:
      event["detail-type"]  → "OrderPlaced" / "PaymentProcessed" / "OrderFulfilled"
      event["detail"]       → order payload dict
    """
    event_type     = event.get("detail-type", "Unknown")
    detail         = event.get("detail", {})
    order_id       = detail.get("orderId", "")
    correlation_id = detail.get("correlationId", "")

    logger.info("Email notification triggered", extra={
        "eventType": event_type,
        "orderId":   order_id,
    })

    tracer.put_annotation(key="eventType", value=event_type)
    tracer.put_annotation(key="orderId",   value=order_id)

    success = _send_notification(event_type, detail)

    metrics.add_metric(name="NotificationsSent", unit=MetricUnit.Count, value=1 if success else 0)
    metrics.add_metric(name="NotificationsFailed", unit=MetricUnit.Count, value=0 if success else 1)

    return {
        "orderId":   order_id,
        "eventType": event_type,
        "notified":  success,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
