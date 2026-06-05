"""
inventory/app.py
────────────────
Inventory Reservation Lambda
Triggered by: EventBridge rule → source=cd016.orders, detail-type=OrderPlaced

Responsibilities:
  - Reserve inventory for each item in the order
  - Update order status in DynamoDB
  - Publish InventoryReserved or InventoryFailed event
  - On failure → message goes to SQS DLQ
"""

from __future__ import annotations

import json
import os
import random
from datetime import datetime, timezone
from typing import Any

# ── Powertools ────────────────────────────────────────────────────────────────
from aws_lambda_powertools import Logger, Tracer, Metrics
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities.typing import LambdaContext

logger  = Logger(service="cd016-inventory")
tracer  = Tracer(service="cd016-inventory")
metrics = Metrics(namespace="CD016/Serverless", service="cd016-inventory")

# ── Lazy imports ──────────────────────────────────────────────────────────────
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
def _reserve_inventory(items: list[dict]) -> dict:
    """
    Simulate inventory reservation.
    In production: check inventory service / DynamoDB inventory table.
    Returns: {"success": bool, "reserved": list, "failed": list}
    """
    reserved = []
    failed   = []

    for item in items:
        # Simulate 95% success rate — realistic for dev/demo
        if random.random() > 0.05:
            reserved.append({**item, "status": "RESERVED"})
        else:
            failed.append({**item, "status": "OUT_OF_STOCK"})

    return {
        "success":  len(failed) == 0,
        "reserved": reserved,
        "failed":   failed,
    }


@tracer.capture_method
def _update_order_status(order_id: str, status: str, reservation: dict) -> None:
    """Update DynamoDB order record with inventory status."""
    table = _get_dynamodb().Table(ORDERS_TABLE)
    table.update_item(
        Key={"orderId": order_id},
        UpdateExpression="SET #s = :s, inventoryResult = :r, updatedAt = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": status,
            ":r": json.dumps(reservation),
            ":t": datetime.now(timezone.utc).isoformat(),
        },
    )


@tracer.capture_method
def _publish_event(order_id: str, user_id: str, success: bool, reservation: dict, correlation_id: str) -> None:
    """Publish InventoryReserved or InventoryFailed to EventBridge."""
    detail_type = "InventoryReserved" if success else "InventoryFailed"
    _get_events().put_events(
        Entries=[{
            "Source":       "cd016.inventory",
            "DetailType":   detail_type,
            "Detail":       json.dumps({
                "orderId":       order_id,
                "userId":        user_id,
                "reservation":   reservation,
                "correlationId": correlation_id,
            }),
            "EventBusName": EVENT_BUS_NAME,
        }]
    )
    logger.info(f"{detail_type} event published", extra={"orderId": order_id})


@logger.inject_lambda_context(log_event=True)
@tracer.capture_lambda_handler
@metrics.log_metrics(capture_cold_start_metric=True)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    """
    EventBridge invokes this with event.detail = OrderPlaced payload.
    EventBridge wraps the event; actual data is in event["detail"].
    """
    detail         = event.get("detail", {})
    order_id       = detail.get("orderId", "")
    user_id        = detail.get("userId", "")
    items          = detail.get("items", [])
    correlation_id = detail.get("correlationId", "")

    logger.info("Processing inventory reservation", extra={
        "orderId":    order_id,
        "itemCount":  len(items),
    })

    tracer.put_annotation(key="orderId",   value=order_id)
    tracer.put_annotation(key="eventType", value="OrderPlaced")

    reservation = _reserve_inventory(items)

    new_status = "INVENTORY_RESERVED" if reservation["success"] else "INVENTORY_FAILED"
    _update_order_status(order_id, new_status, reservation)
    _publish_event(order_id, user_id, reservation["success"], reservation, correlation_id)

    metrics.add_metric(
        name="InventoryReservationAttempts",
        unit=MetricUnit.Count,
        value=1
    )
    if not reservation["success"]:
        metrics.add_metric(name="InventoryFailed", unit=MetricUnit.Count, value=1)

    return {
        "orderId":     order_id,
        "status":      new_status,
        "reservation": reservation,
    }
