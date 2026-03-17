"""Study Admin Lambda — Function URL handler for managing weather studies.
Supports list, create, approve, complete, and delete actions."""

import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal

import boto3

TABLE_NAME = os.environ.get("TABLE_NAME", "art-generator")
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal types from DynamoDB."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            if obj % 1 == 0:
                return int(obj)
            return float(obj)
        return super().default(obj)


def generate_study_id(name, start_date):
    """Slugifies name and appends start date.

    E.g. generate_study_id("Arctic Storm", "2026-03-10") -> "arctic-storm-2026-03-10"
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"{slug}-{start_date}"


def parse_create_params(params):
    """Extracts study creation parameters from query string params.

    Returns dict with name, start_date, end_date, coordinates (list of {lat, lng}), artist.
    Coordinates can be comma-separated for multiple points.
    """
    name = params.get("name", "")
    start_date = params.get("start_date", "")
    end_date = params.get("end_date", start_date)
    artist = params.get("artist", "")

    # Parse coordinates — lat/lng can be comma-separated for multiple
    lat_str = params.get("lat", "")
    lng_str = params.get("lng", "")

    coordinates = []
    if lat_str and lng_str:
        lats = [x.strip() for x in lat_str.split(",")]
        lngs = [x.strip() for x in lng_str.split(",")]
        for lat_val, lng_val in zip(lats, lngs):
            try:
                coordinates.append({
                    "lat": float(lat_val),
                    "lng": float(lng_val),
                })
            except ValueError:
                continue

    return {
        "name": name,
        "start_date": start_date,
        "end_date": end_date,
        "coordinates": coordinates,
        "artist": artist,
    }


def _respond(status_code, body):
    """Build a Function URL response."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, cls=DecimalEncoder),
    }


def handler(event, context):
    """Function URL handler with auth check and action routing."""
    # Auth check
    headers = event.get("headers", {})
    auth = headers.get("authorization", "")
    if not ADMIN_API_KEY or auth != ADMIN_API_KEY:
        return _respond(401, {"error": "Unauthorized"})

    # Parse action from query params
    qs = event.get("queryStringParameters", {}) or {}
    action = qs.get("action", "")

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(TABLE_NAME)

    if action == "list":
        return _handle_list(table, qs)
    elif action == "create":
        return _handle_create(table, qs)
    elif action == "approve":
        return _handle_approve(table, qs)
    elif action == "complete":
        return _handle_complete(table, qs)
    elif action == "delete":
        return _handle_delete(table, qs)
    else:
        return _respond(400, {"error": f"Unknown action: {action}"})


def _handle_list(table, params):
    """List all studies (STUDY# items with SK=META)."""
    response = table.scan(
        FilterExpression="begins_with(PK, :pk) AND SK = :sk",
        ExpressionAttributeValues={":pk": "STUDY#", ":sk": "META"},
    )
    items = response.get("Items", [])
    return _respond(200, {"studies": items})


def _handle_create(table, params):
    """Create a new study."""
    parsed = parse_create_params(params)
    if not parsed["name"] or not parsed["start_date"]:
        return _respond(400, {"error": "name and start_date are required"})

    study_id = generate_study_id(parsed["name"], parsed["start_date"])

    item = {
        "PK": f"STUDY#{study_id}",
        "SK": "META",
        "name": parsed["name"],
        "status": "draft",
        "start_date": parsed["start_date"],
        "end_date": parsed["end_date"],
        "artist": parsed["artist"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    if parsed["coordinates"]:
        item["coordinates"] = [
            {"lat": Decimal(str(c["lat"])), "lng": Decimal(str(c["lng"]))}
            for c in parsed["coordinates"]
        ]
        # Store primary lat/lng from first coordinate
        item["lat"] = Decimal(str(parsed["coordinates"][0]["lat"]))
        item["lng"] = Decimal(str(parsed["coordinates"][0]["lng"]))

    table.put_item(Item=item)
    return _respond(201, {"study_id": study_id, "item": item})


def _handle_approve(table, params):
    """Approve a study — set status to active."""
    study_id = params.get("study_id", "")
    if not study_id:
        return _respond(400, {"error": "study_id is required"})

    table.update_item(
        Key={"PK": f"STUDY#{study_id}", "SK": "META"},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "active"},
    )
    return _respond(200, {"study_id": study_id, "status": "active"})


def _handle_complete(table, params):
    """Complete a study — set status to completed."""
    study_id = params.get("study_id", "")
    if not study_id:
        return _respond(400, {"error": "study_id is required"})

    table.update_item(
        Key={"PK": f"STUDY#{study_id}", "SK": "META"},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "completed"},
    )
    return _respond(200, {"study_id": study_id, "status": "completed"})


def _handle_delete(table, params):
    """Delete a study — removes META + all DAY# entries."""
    study_id = params.get("study_id", "")
    if not study_id:
        return _respond(400, {"error": "study_id is required"})

    pk = f"STUDY#{study_id}"

    # Query all items with this PK
    response = table.query(
        KeyConditionExpression="PK = :pk",
        ExpressionAttributeValues={":pk": pk},
    )
    items = response.get("Items", [])

    # Delete each item
    deleted = 0
    for item in items:
        table.delete_item(Key={"PK": pk, "SK": item["SK"]})
        deleted += 1

    return _respond(200, {"study_id": study_id, "items_deleted": deleted})
