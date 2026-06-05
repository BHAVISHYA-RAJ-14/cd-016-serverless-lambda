"""
tests/unit/test_orders_api.py
─────────────────────────────
Unit tests for Orders API Lambda.
Uses moto to mock AWS services — no real AWS calls, no cost.

Run: make test
"""

import json
import os
import sys
import pytest

# ── Set required env vars BEFORE importing the handler ───────────────────────
os.environ["ORDERS_TABLE"]       = "cd016-orders-test"
os.environ["EVENT_BUS_NAME"]     = "cd016-orders-bus-test"
os.environ["ORDER_WORKFLOW_ARN"] = ""
os.environ["ENVIRONMENT"]        = "test"
os.environ["POWERTOOLS_SERVICE_NAME"] = "cd016-test"
os.environ["POWERTOOLS_LOG_LEVEL"]    = "WARNING"
os.environ["AWS_DEFAULT_REGION"]      = "ap-south-1"
os.environ["AWS_ACCESS_KEY_ID"]       = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"]   = "testing"
os.environ["AWS_SECURITY_TOKEN"]      = "testing"
os.environ["AWS_SESSION_TOKEN"]       = "testing"

import boto3
from moto import mock_aws


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def aws_credentials():
    """Mock AWS credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"]    = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"]   = "testing"
    os.environ["AWS_SESSION_TOKEN"]    = "testing"


@pytest.fixture
def dynamodb_table(aws_credentials):
    """Create mock DynamoDB table."""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="ap-south-1")
        table    = dynamodb.create_table(
            TableName="cd016-orders-test",
            KeySchema=[{"AttributeName": "orderId", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "orderId", "AttributeType": "S"},
                {"AttributeName": "userId",  "AttributeType": "S"},
                {"AttributeName": "status",  "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "userId-index",
                "KeySchema": [
                    {"AttributeName": "userId",  "KeyType": "HASH"},
                    {"AttributeName": "status",  "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )
        yield table


def _make_event(method: str, path: str, body=None, path_params=None, query_params=None) -> dict:
    """Build a mock API Gateway HTTP event."""
    return {
        "requestContext": {
            "requestId": "test-req-001",
            "http": {"method": method, "path": path},
        },
        "headers":              {"Content-Type": "application/json"},
        "body":                 json.dumps(body) if body else None,
        "pathParameters":       path_params or {},
        "queryStringParameters": query_params or {},
    }


class MockContext:
    function_name     = "cd016-orders-api-test"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:ap-south-1:123:function:test"
    aws_request_id    = "test-request-id"


# ─────────────────────────────────────────────────────────────────────────────
# TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestHealthCheck:
    @mock_aws
    def test_health_check_returns_200(self):
        sys.path.insert(0, "src/orders_api")
        from app import lambda_handler

        event  = _make_event("GET", "/health")
        result = lambda_handler(event, MockContext())

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["status"] == "healthy"
        assert body["service"] == "cd016-orders-api"


class TestCreateOrder:
    @mock_aws
    def test_create_order_valid_payload(self, dynamodb_table):
        sys.path.insert(0, "src/orders_api")
        # Reset lazy-loaded clients
        import app as orders_app
        orders_app._dynamodb = None
        orders_app._events   = None
        orders_app._sfn      = None

        # Mock EventBridge
        boto3.client("events", region_name="ap-south-1")

        payload = {
            "userId": "user-test-001",
            "items": [
                {"productId": "prod-001", "quantity": 2, "price": 500.0}
            ],
            "shippingAddress": "Test Address, India"
        }
        event  = _make_event("POST", "/orders", body=payload)
        result = orders_app.lambda_handler(event, MockContext())

        assert result["statusCode"] == 201
        body = json.loads(result["body"])
        assert "orderId" in body
        assert body["status"] == "PENDING"
        assert body["total"] == 1000.0

    @mock_aws
    def test_create_order_missing_user_id(self, dynamodb_table):
        sys.path.insert(0, "src/orders_api")
        import app as orders_app

        payload = {"items": [{"productId": "prod-001", "quantity": 1, "price": 100.0}]}
        event   = _make_event("POST", "/orders", body=payload)
        result  = orders_app.lambda_handler(event, MockContext())

        assert result["statusCode"] == 422   # Unprocessable Entity

    @mock_aws
    def test_create_order_empty_items(self, dynamodb_table):
        sys.path.insert(0, "src/orders_api")
        import app as orders_app

        payload = {"userId": "user-001", "items": []}
        event   = _make_event("POST", "/orders", body=payload)
        result  = orders_app.lambda_handler(event, MockContext())

        assert result["statusCode"] == 422

    @mock_aws
    def test_create_order_invalid_json(self):
        sys.path.insert(0, "src/orders_api")
        import app as orders_app

        event           = _make_event("POST", "/orders")
        event["body"]   = "{ invalid json }"
        result          = orders_app.lambda_handler(event, MockContext())

        assert result["statusCode"] == 400


class TestGetOrder:
    @mock_aws
    def test_get_order_not_found(self, dynamodb_table):
        sys.path.insert(0, "src/orders_api")
        import app as orders_app
        orders_app._dynamodb = None

        event  = _make_event("GET", "/orders/nonexistent-id", path_params={"orderId": "nonexistent-id"})
        result = orders_app.lambda_handler(event, MockContext())

        assert result["statusCode"] == 404
        body = json.loads(result["body"])
        assert "not found" in body["message"].lower()


class TestResponseHeaders:
    @mock_aws
    def test_response_has_security_headers(self):
        sys.path.insert(0, "src/orders_api")
        import app as orders_app

        event  = _make_event("GET", "/health")
        result = orders_app.lambda_handler(event, MockContext())

        assert "Strict-Transport-Security" in result["headers"]
        assert result["headers"]["Content-Type"] == "application/json"
