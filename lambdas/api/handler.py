"""API Lambda — serves paginated artwork data for infinite scroll."""
import json
import os
from decimal import Decimal

import boto3

TABLE_NAME = os.environ.get("TABLE_NAME", "art-generator")
BUCKET_NAME = os.environ.get("BUCKET_NAME", "art-generator-216890068001")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
PAGE_SIZE = 10


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


CORS_HEADERS = {}  # CORS handled by Lambda Function URL config


def handler(event, context):
    """Lambda function URL handler — artworks API + newsletter subscribe."""
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")

    if method == "OPTIONS":
        return {"statusCode": 200, "headers": CORS_HEADERS}

    if method == "POST" and path == "/subscribe":
        return handle_subscribe(event)

    if method == "GET" and path == "/subscribers":
        return handle_list_subscribers(event)

    query = event.get("queryStringParameters") or {}
    artist = query.get("artist", "")
    cursor = query.get("cursor", "")
    page_size = min(int(query.get("limit", PAGE_SIZE)), 50)

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(TABLE_NAME)

    # Scan all WEATHER# items, filter by artist
    # For better performance at scale, add a GSI on artist
    items = []
    scan_params = {
        "FilterExpression": "begins_with(PK, :prefix)",
        "ExpressionAttributeValues": {":prefix": "WEATHER#"},
    }

    if artist:
        scan_params["FilterExpression"] += " AND artist = :artist"
        scan_params["ExpressionAttributeValues"][":artist"] = artist

    if cursor:
        scan_params["ExclusiveStartKey"] = json.loads(cursor)

    response = table.scan(**scan_params)
    items = response.get("Items", [])

    # Sort by run_id descending (newest first)
    items.sort(key=lambda x: x.get("run_id", x.get("PK", "")), reverse=True)

    # Paginate
    page = items[:page_size]
    has_more = len(items) > page_size

    # Build next cursor from DynamoDB pagination
    next_cursor = None
    if response.get("LastEvaluatedKey"):
        next_cursor = json.dumps(response["LastEvaluatedKey"], cls=DecimalEncoder)
    elif has_more:
        # If we got more items than page_size from a single scan, we need another scan
        next_cursor = "more"

    # Build response with S3 URLs
    results = []
    for item in page:
        run_id = item.get("run_id", item.get("PK", "").replace("WEATHER#", ""))
        slug = item.get("SK", item.get("slug", ""))
        results.append({
            "run_id": run_id,
            "slug": slug,
            "date": item.get("date", ""),
            "artist": item.get("artist", "sam_francis"),
            "lat": float(item.get("lat", 0)),
            "lng": float(item.get("lng", 0)),
            "temp": float(item.get("temp", 0)),
            "wind_speed": float(item.get("wind_speed", 0)),
            "pressure": float(item.get("pressure", 0)),
            "score": float(item.get("score", 0)),
            "rationale": item.get("rationale", ""),
            "svg_url": f"/weather/{run_id}/{slug}/artwork.svg",
            "page_url": f"/weather/{run_id}/{slug}/",
        })

    return {
        "statusCode": 200,
        "headers": {**CORS_HEADERS, "Content-Type": "application/json"},
        "body": json.dumps({
            "items": results,
            "next_cursor": next_cursor,
            "artist": artist,
            "count": len(results),
        }, cls=DecimalEncoder),
    }


def handle_list_subscribers(event):
    """Admin endpoint — list all subscribers. Requires ?key= param."""
    query = event.get("queryStringParameters") or {}
    if not ADMIN_KEY or query.get("key") != ADMIN_KEY:
        return {"statusCode": 401, "headers": {"Content-Type": "application/json"}, "body": json.dumps({"error": "Unauthorized"})}

    from datetime import datetime, timezone
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(TABLE_NAME)
    result = table.query(
        KeyConditionExpression="PK = :pk",
        ExpressionAttributeValues={":pk": "SUBSCRIBER"},
    )
    subscribers = []
    for item in result.get("Items", []):
        ts = int(item.get("subscribed_at", 0))
        subscribers.append({
            "email": item["SK"],
            "source": item.get("source", ""),
            "subscribed_at": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else "",
        })

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"subscribers": subscribers, "count": len(subscribers)}),
    }


def handle_subscribe(event):
    """Store newsletter subscriber in DynamoDB."""
    import re
    import time

    try:
        body = json.loads(event.get("body", "{}"))
    except (json.JSONDecodeError, TypeError):
        body = {}

    email = (body.get("email") or "").strip().lower()
    if not email or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return {
            "statusCode": 400,
            "headers": {**CORS_HEADERS, "Content-Type": "application/json"},
            "body": json.dumps({"error": "Valid email required"}),
        }

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(TABLE_NAME)
    table.put_item(
        Item={
            "PK": "SUBSCRIBER",
            "SK": email,
            "subscribed_at": int(time.time()),
            "source": body.get("source", "website"),
        },
        ConditionExpression="attribute_not_exists(SK)",
    )

    return {
        "statusCode": 200,
        "headers": {**CORS_HEADERS, "Content-Type": "application/json"},
        "body": json.dumps({"ok": True}),
    }
