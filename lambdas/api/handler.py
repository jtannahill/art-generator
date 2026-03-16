"""API Lambda — serves paginated artwork data for infinite scroll."""
import json
import os
from decimal import Decimal

import boto3

TABLE_NAME = os.environ.get("TABLE_NAME", "art-generator")
BUCKET_NAME = os.environ.get("BUCKET_NAME", "art-generator-216890068001")
PAGE_SIZE = 10


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


def handler(event, context):
    """Lambda function URL handler — returns paginated artworks."""
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
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({
            "items": results,
            "next_cursor": next_cursor,
            "artist": artist,
            "count": len(results),
        }, cls=DecimalEncoder),
    }
