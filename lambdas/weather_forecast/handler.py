"""Weather Forecast Lambda — predicts tomorrow's most dramatic weather locations."""

import json
import os
from datetime import datetime, timezone, timedelta

import boto3
import numpy as np
import requests

TABLE_NAME = os.environ.get("TABLE_NAME", "art-generator")
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# Same 54-point grid as weather_ingest
SCAN_POINTS = [
    (70, -20), (70, 20), (70, 60), (70, 140),
    (60, -130), (60, -70), (60, 0), (60, 40), (60, 90), (60, 140),
    (45, -120), (45, -75), (45, -30), (45, 10), (45, 50), (45, 90), (45, 130),
    (30, -110), (30, -60), (30, -15), (30, 30), (30, 70), (30, 110), (30, 150),
    (15, -90), (15, -40), (15, 0), (15, 40), (15, 80), (15, 120), (15, 160),
    (0, -80), (0, -30), (0, 20), (0, 60), (0, 100), (0, 140),
    (-15, -70), (-15, -20), (-15, 30), (-15, 80), (-15, 130),
    (-30, -60), (-30, 0), (-30, 50), (-30, 120), (-30, 170),
    (-45, -70), (-45, 0), (-45, 80), (-45, 170),
    (-60, -60), (-60, 0), (-60, 100),
]

dynamodb = boto3.resource("dynamodb")


def handler(event, context):
    """Fetch 24h forecast for all 54 points, score and store predictions."""
    now = datetime.now(timezone.utc)
    tomorrow = now + timedelta(hours=24)
    target_date = tomorrow.strftime("%Y-%m-%d")
    target_hour = 6  # Score for 06:00 UTC (when the pipeline runs)

    all_weather = []
    for lat, lng in SCAN_POINTS:
        try:
            data = fetch_forecast(lat, lng, target_hour)
            if data:
                all_weather.append(data)
        except Exception as e:
            print(f"Failed to fetch forecast ({lat},{lng}): {e}")

    if not all_weather:
        return {"error": "No forecast data available"}

    print(f"[FORECAST] Fetched {len(all_weather)} points for {target_date}")

    # Score using same formula as weather_ingest
    pressures = np.array([w["pressure"] for w in all_weather])
    winds = np.array([w["wind_speed"] for w in all_weather])
    temps = np.array([w["temp"] for w in all_weather])
    humidities = np.array([w["humidity"] for w in all_weather])
    precips = np.array([w["precipitation"] for w in all_weather])

    pressure_anomaly = np.abs(pressures - np.mean(pressures))
    temp_anomaly = np.abs(temps - np.mean(temps))

    scores = (
        0.30 * normalize(pressure_anomaly) +
        0.25 * normalize(winds) +
        0.20 * normalize(temp_anomaly) +
        0.15 * normalize(precips) +
        0.10 * normalize(humidities)
    )

    ranked = sorted(zip(scores, all_weather), key=lambda x: -x[0])

    predictions = []
    for i, (score, w) in enumerate(ranked[:20]):
        predictions.append({
            "rank": i + 1,
            "lat": w["lat"],
            "lng": w["lng"],
            "score": round(float(score) * 100, 1),
            "pressure": round(w["pressure"], 1),
            "wind_speed": round(w["wind_speed"], 1),
            "temp": round(w["temp"], 1),
            "humidity": round(w["humidity"], 1),
            "precipitation": round(w["precipitation"], 1),
        })

    table = dynamodb.Table(TABLE_NAME)
    table.put_item(Item={
        "PK": "FORECAST",
        "SK": target_date,
        "predictions": json.dumps(predictions),
        "created_at": now.isoformat(),
        "target_date": target_date,
        "points_scanned": len(all_weather),
    })

    print(f"[FORECAST] {target_date}: #1 ({predictions[0]['lat']},{predictions[0]['lng']}) score={predictions[0]['score']}")
    return {"target_date": target_date, "top_5": predictions[:5]}


def fetch_forecast(lat, lng, target_hour):
    """Fetch 24h ahead forecast for a point."""
    resp = requests.get(OPEN_METEO_URL, params={
        "latitude": lat,
        "longitude": lng,
        "hourly": "temperature_2m,relative_humidity_2m,surface_pressure,wind_speed_10m,wind_direction_10m,precipitation",
        "forecast_days": 2,
        "models": "gfs_seamless",
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    hourly = data.get("hourly", {})
    idx = 24 + target_hour
    if idx >= len(hourly.get("temperature_2m", [])):
        idx = len(hourly["temperature_2m"]) - 1

    temp = hourly["temperature_2m"][idx]
    humidity = hourly["relative_humidity_2m"][idx]
    pressure = hourly["surface_pressure"][idx]
    wind_speed = hourly["wind_speed_10m"][idx]
    precip = hourly["precipitation"][idx]

    if any(v is None for v in [temp, humidity, pressure, wind_speed]):
        return None

    return {
        "lat": lat, "lng": lng,
        "temp": temp, "humidity": humidity,
        "pressure": pressure, "wind_speed": wind_speed,
        "precipitation": precip or 0,
    }


def normalize(arr):
    arr_min = np.nanmin(arr)
    arr_max = np.nanmax(arr)
    if arr_max == arr_min:
        return np.zeros_like(arr, dtype=np.float32)
    return ((arr - arr_min) / (arr_max - arr_min)).astype(np.float32)
