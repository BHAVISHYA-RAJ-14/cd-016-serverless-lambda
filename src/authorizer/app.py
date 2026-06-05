"""
authorizer/app.py
─────────────────
JWT Lambda Authorizer for API Gateway HTTP API
- Validates Bearer token from Authorization header
- Returns IAM policy: Allow or Deny
- Result cached by API Gateway for 5 minutes (configured in template.yaml)
  → reduces Lambda invocations significantly

Token format: Authorization: Bearer <jwt_token>
JWT payload expected:
  {
    "sub": "user-id",
    "email": "user@example.com",
    "iat": 1234567890,
    "exp": 1234567890
  }
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

# Lazy import for cold start optimisation
_jwt = None

def _get_jwt():
    global _jwt
    if _jwt is None:
        import jwt as pyjwt  # noqa: PLC0415
        _jwt = pyjwt
    return _jwt

JWT_SECRET  = os.environ["JWT_SECRET"]
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")


def _build_policy(principal_id: str, effect: str, resource: str, context: dict | None = None) -> dict:
    """Build IAM policy document returned to API Gateway."""
    policy = {
        "principalId": principal_id,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [{
                "Action":   "execute-api:Invoke",
                "Effect":   effect,
                "Resource": resource,
            }],
        },
    }
    # Context is passed to downstream Lambda in $context.authorizer.*
    if context:
        policy["context"] = context
    return policy


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Authorizer entry point.
    event keys for HTTP API payload format 2.0:
      - routeArn          : arn:aws:execute-api:...
      - identitySource    : the Authorization header value
      - headers.authorization : Bearer <token>
    """
    print(json.dumps({
        "level":     "INFO",
        "message":   "Authorizer invoked",
        "routeArn":  event.get("routeArn", ""),
        "environment": ENVIRONMENT,
    }))

    # Extract token from Authorization header
    token = (
        event.get("headers", {}).get("authorization", "")
        or event.get("identitySource", "")
    )

    if not token or not token.lower().startswith("bearer "):
        print(json.dumps({"level": "WARNING", "message": "Missing or malformed Authorization header"}))
        raise Exception("Unauthorized")    # API Gateway expects this exact exception to return 401

    raw_token = token.split(" ", 1)[1].strip()

    try:
        jwt    = _get_jwt()
        payload = jwt.decode(
            raw_token,
            JWT_SECRET,
            algorithms=["HS256"],
            options={"require": ["sub", "exp"]},
        )
    except Exception as exc:
        print(json.dumps({"level": "WARNING", "message": "JWT validation failed", "error": str(exc)}))
        raise Exception("Unauthorized")

    # Check token expiry manually (extra safety)
    if payload.get("exp", 0) < int(time.time()):
        print(json.dumps({"level": "WARNING", "message": "Token expired"}))
        raise Exception("Unauthorized")

    user_id = payload.get("sub", "unknown")
    email   = payload.get("email", "")

    print(json.dumps({
        "level":   "INFO",
        "message": "JWT validated successfully",
        "userId":  user_id,
    }))

    # Allow — pass userId and email to downstream Lambda via context
    return _build_policy(
        principal_id=user_id,
        effect="Allow",
        resource=event.get("routeArn", "*"),
        context={
            "userId":      user_id,
            "email":       email,
            "environment": ENVIRONMENT,
        },
    )
