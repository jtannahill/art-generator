"""Print Shop Lambda — routes to action handlers based on ?action= query param."""

import json
import os
from urllib.parse import parse_qs

import boto3
import stripe

TABLE_NAME = os.environ.get("TABLE_NAME", "art-generator")
BUCKET_NAME = os.environ.get("BUCKET_NAME", "art-generator-216890068001")

_table = None


def _get_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _table


def handler(event, context):
    """Lambda Function URL handler — routes by action query param."""
    qs = event.get("queryStringParameters") or {}
    action = qs.get("action", "")
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")

    try:
        if action == "editions":
            return _handle_editions(qs)
        elif action == "checkout" and method == "POST":
            return _handle_checkout(event, qs)
        elif action == "stripe_webhook" and method == "POST":
            return _handle_stripe_webhook(event)
        elif action == "tps_webhook" and method == "POST":
            return _handle_tps_webhook(event)
        else:
            return _response(400, {"error": "invalid_action"})
    except Exception as e:
        print(f"Error in {action}: {e}")
        return _response(500, {"error": "internal_error"})


def _handle_editions(qs):
    from editions import get_editions
    from dynamic_pricing import compute_multiplier
    run_id = qs.get("run_id", "")
    slug = qs.get("slug", "")
    if not run_id or not slug:
        return _response(400, {"error": "missing run_id or slug"})
    result = get_editions(_get_table(), run_id, slug)
    if result is None:
        return _response(404, {"error": "artwork_not_found"})

    # Apply dynamic pricing
    try:
        pricing = compute_multiplier(TABLE_NAME, run_id, slug)
        if pricing["multiplier"] > 1.0:
            for key, size in result.get("sizes", {}).items():
                size["base_price_cents"] = size["price_cents"]
                size["price_cents"] = int(size["price_cents"] * pricing["multiplier"])
            result["pricing"] = pricing
    except Exception as e:
        print(f"Dynamic pricing failed, using base prices: {e}")

    return _response(200, result)


def _handle_checkout(event, qs):
    from checkout import create_checkout_session
    from secrets_loader import get_secrets

    body = json.loads(event.get("body", "{}"))
    run_id = body.get("run_id", qs.get("run_id", ""))
    slug = body.get("slug", qs.get("slug", ""))
    size_key = body.get("size_key", qs.get("size_key", ""))

    if not all([run_id, slug, size_key]):
        return _response(400, {"error": "missing run_id, slug, or size_key"})

    secrets = get_secrets()
    result = create_checkout_session(
        table=_get_table(), stripe_key=secrets["stripe_secret_key"],
        run_id=run_id, slug=slug, size_key=size_key,
        base_url="https://art.jamestannahill.com",
    )
    if "error" in result:
        return _response(400 if result["error"] != "not_found" else 404, result)
    return _response(200, result)


def _handle_stripe_webhook(event):
    from stripe_webhook import handle_checkout_completed
    from secrets_loader import get_secrets

    secrets = get_secrets()
    body = event.get("body", "")
    sig_header = (event.get("headers") or {}).get("stripe-signature", "")

    try:
        evt = stripe.Webhook.construct_event(body, sig_header, secrets["stripe_webhook_secret"])
    except (ValueError, stripe.error.SignatureVerificationError):
        return _response(400, {"error": "invalid_signature"})

    if evt["type"] == "checkout.session.completed":
        result = handle_checkout_completed(_get_table(), evt["data"]["object"], secrets["tps_api_key"])
        return _response(200, result)

    return _response(200, {"status": "ignored", "type": evt["type"]})


def _handle_tps_webhook(event):
    from tps_webhook import handle_tps_webhook
    from secrets_loader import get_secrets

    secrets = get_secrets()
    body = event.get("body", "")
    sig = (event.get("headers") or {}).get("x-creativehub-signature", "")

    result = handle_tps_webhook(_get_table(), body, sig, secrets.get("tps_webhook_secret", secrets["tps_api_key"]))
    status_code = 200 if "error" not in result else 400
    return _response(status_code, result)


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }
