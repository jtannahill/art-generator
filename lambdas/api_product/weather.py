"""Weather Drama API — rankings, forecast, live scoring."""
import json
import uuid
import boto3
from decimal import Decimal

TABLE_NAME = "art-generator"
dynamodb = boto3.resource("dynamodb")


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


def handle_weather(event):
    path = event.get("path", "")
    qs = event.get("queryStringParameters") or {}
    request_id = f"req_{uuid.uuid4().hex[:12]}"

    if path.endswith("/rankings"):
        return _rankings(qs, request_id)
    elif path.endswith("/forecast"):
        return _forecast(qs, request_id)
    elif path.endswith("/score"):
        return _score(qs, request_id)
    else:
        return _resp(404, {"error": {"code": "not_found", "message": "Use /v1/weather/rankings, /forecast, or /score"}})


def _rankings(qs, request_id):
    from datetime import datetime, timezone
    table = dynamodb.Table(TABLE_NAME)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    limit = min(int(qs.get("limit", "10")), 20)

    # Try today's exact date PK first
    result = table.query(
        KeyConditionExpression="PK = :pk",
        ExpressionAttributeValues={":pk": f"WEATHER#{today}"},
    )
    items = result.get("Items", [])

    # If no exact date match, scan for latest run
    if not items:
        scan = table.scan(
            FilterExpression="begins_with(PK, :prefix)",
            ExpressionAttributeValues={":prefix": "WEATHER#"},
            ProjectionExpression="PK, SK, score, lat, lng, pressure, wind_speed, #t, humidity, precipitation",
            ExpressionAttributeNames={"#t": "temp"},
            Limit=200,
        )
        items = scan.get("Items", [])

    # Sort by score descending, take top N
    items.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
    items = items[:limit]

    rankings = []
    for i, item in enumerate(items):
        rankings.append({
            "rank": i + 1,
            "lat": float(item.get("lat", 0)),
            "lng": float(item.get("lng", 0)),
            "score": float(item.get("score", 0)),
            "pressure_hpa": float(item.get("pressure", 0)),
            "wind_speed_kmh": round(float(item.get("wind_speed", 0)) * 3.6, 1),
            "temp_c": float(item.get("temp", 0)),
            "humidity_pct": float(item.get("humidity", 0)),
            "precipitation_mm": float(item.get("precipitation", 0)),
        })

    return _resp(200, {
        "date": today,
        "rankings": rankings,
        "model": "GFS (NOAA)",
        "scan_points": 54,
        "request_id": request_id,
    })


def _forecast(qs, request_id):
    from datetime import datetime, timezone, timedelta
    table = dynamodb.Table(TABLE_NAME)
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    limit = min(int(qs.get("limit", "10")), 20)

    result = table.get_item(Key={"PK": "FORECAST", "SK": tomorrow})
    item = result.get("Item")

    if not item:
        return _resp(200, {"date": tomorrow, "rankings": [], "message": "Forecast not yet available", "request_id": request_id})

    predictions = json.loads(item.get("predictions", "[]"))[:limit]
    return _resp(200, {
        "date": tomorrow,
        "rankings": predictions,
        "model": "GFS (NOAA) 24h forecast",
        "scan_points": 54,
        "request_id": request_id,
    })


def _score(qs, request_id):
    import urllib.request as urlreq
    lat = qs.get("lat")
    lng = qs.get("lng")
    if not lat or not lng:
        return _resp(400, {"error": {"code": "missing_params", "message": "lat and lng required", "request_id": request_id}})

    lat_f, lng_f = float(lat), float(lng)
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat_f}&longitude={lng_f}"
        f"&hourly=temperature_2m,relative_humidity_2m,surface_pressure,wind_speed_10m,wind_direction_10m,precipitation"
        f"&forecast_days=1&models=gfs_seamless"
    )
    resp = urlreq.urlopen(urlreq.Request(url, headers={"User-Agent": "art-api/1.0"}), timeout=15)
    data = json.loads(resp.read())
    hourly = data.get("hourly", {})

    from datetime import datetime, timezone
    idx = min(datetime.now(timezone.utc).hour, len(hourly.get("temperature_2m", [])) - 1)

    weather = {
        "lat": lat_f,
        "lng": lng_f,
        "pressure_hpa": hourly["surface_pressure"][idx],
        "wind_speed_kmh": hourly["wind_speed_10m"][idx],
        "temp_c": hourly["temperature_2m"][idx],
        "humidity_pct": hourly["relative_humidity_2m"][idx],
        "precipitation_mm": hourly["precipitation"][idx] or 0,
    }

    # Composite score (simplified — same weight ratios as ingest)
    weather["score"] = round(
        abs(weather["pressure_hpa"] - 1013) * 0.3 +
        weather["wind_speed_kmh"] * 0.25 +
        abs(weather["temp_c"] - 15) * 0.20 +
        weather["precipitation_mm"] * 10 * 0.15 +
        weather["humidity_pct"] * 0.01 * 0.10, 1
    )
    weather["request_id"] = request_id
    return _resp(200, weather)


def _resp(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps(body, cls=DecimalEncoder),
    }
