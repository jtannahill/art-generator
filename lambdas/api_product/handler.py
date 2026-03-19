"""art.jt API — routes requests to endpoint handlers."""
import json
import os

TABLE_NAME = os.environ.get("TABLE_NAME", "art-generator")


def handler(event, context):
    path = event.get("path", "")
    method = event.get("httpMethod", "GET")

    try:
        if path == "/v1/critique" and method == "POST":
            from critique import handle_critique
            return handle_critique(event)
        elif path.startswith("/v1/weather"):
            from weather import handle_weather
            return handle_weather(event)
        elif path == "/v1/price" and method == "POST":
            from price import handle_price
            return handle_price(event)
        elif method == "OPTIONS":
            return _response(200, {})
        else:
            return _response(404, {"error": {"code": "not_found", "message": "Endpoint not found. See https://art.jamestannahill.com/api/"}})
    except Exception as e:
        print(f"[ERROR] {path}: {e}")
        return _response(500, {"error": {"code": "internal_error", "message": "Internal server error"}})


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type, x-api-key",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        },
        "body": json.dumps(body),
    }
