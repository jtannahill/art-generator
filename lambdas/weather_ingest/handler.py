"""Weather Ingest Lambda — fetches global weather data via Open-Meteo API,
scores locations for visual interest, returns top 10."""

import json
import math
import os
from datetime import datetime, timezone

import boto3
import numpy as np
import requests

BUCKET_NAME = os.environ.get("BUCKET_NAME", "art-generator-216890068001")

# Grid of ~50 global sample points for weather scanning
SCAN_POINTS = [
    (70, -20), (70, 20), (70, 60), (70, 140),  # Arctic
    (60, -130), (60, -70), (60, 0), (60, 40), (60, 90), (60, 140),  # Subarctic
    (45, -120), (45, -75), (45, -30), (45, 10), (45, 50), (45, 90), (45, 130),  # Mid-lat N
    (30, -110), (30, -60), (30, -15), (30, 30), (30, 70), (30, 110), (30, 150),  # Subtrop N
    (15, -90), (15, -40), (15, 0), (15, 40), (15, 80), (15, 120), (15, 160),  # Tropical N
    (0, -80), (0, -30), (0, 20), (0, 60), (0, 100), (0, 140),  # Equatorial
    (-15, -70), (-15, -20), (-15, 30), (-15, 80), (-15, 130),  # Tropical S
    (-30, -60), (-30, 0), (-30, 50), (-30, 120), (-30, 170),  # Subtrop S
    (-45, -70), (-45, 0), (-45, 80), (-45, 170),  # Mid-lat S
    (-60, -60), (-60, 0), (-60, 100),  # Subantarctic
]

# Region name mapping
REGION_NAMES = [
    ((-90, -60), (-180, 180), "Antarctica"),
    ((-60, -30), (-120, -30), "South America"),
    ((-60, -30), (10, 60), "Southern Africa"),
    ((-60, -30), (100, 180), "Australasia"),
    ((-30, 0), (-120, -30), "Tropical South America"),
    ((-30, 0), (-30, 60), "Tropical Africa"),
    ((-30, 0), (60, 180), "Maritime Continent"),
    ((0, 30), (-130, -60), "Central America / Caribbean"),
    ((0, 30), (-60, -10), "Equatorial Atlantic"),
    ((0, 30), (-10, 60), "Sahara / Sahel"),
    ((0, 30), (60, 120), "South Asia"),
    ((0, 30), (120, 180), "Western Pacific"),
    ((30, 60), (-130, -60), "North America"),
    ((30, 60), (-30, 40), "Europe"),
    ((30, 60), (40, 100), "Central Asia"),
    ((30, 60), (100, 150), "East Asia"),
    ((60, 90), (-180, 180), "Arctic"),
]

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def handler(event, context):
    """Lambda entry point. Fetches weather for global grid, scores, returns top 10."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    hour = now.hour

    # Fetch weather data for all scan points
    all_weather = []
    for lat, lng in SCAN_POINTS:
        try:
            data = fetch_weather(lat, lng, hour)
            if data:
                all_weather.append(data)
        except Exception as e:
            print(f"Failed to fetch ({lat},{lng}): {e}")
            continue

    if not all_weather:
        raise RuntimeError("Could not fetch weather data for any location")

    print(f"Fetched weather for {len(all_weather)} locations")

    # Score and rank
    regions = score_regions(all_weather)

    # Add date metadata
    for r in regions:
        r["date"] = date_str

    # Archive raw data to S3
    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=f"source/weather/{date_str}/scan_data.json",
        Body=json.dumps(all_weather).encode(),
        ContentType="application/json",
    )

    return {"regions": regions}


def fetch_weather(lat, lng, hour):
    """Fetch current weather for a point via Open-Meteo API."""
    resp = requests.get(OPEN_METEO_URL, params={
        "latitude": lat,
        "longitude": lng,
        "hourly": "temperature_2m,relative_humidity_2m,surface_pressure,wind_speed_10m,wind_direction_10m,precipitation",
        "forecast_days": 1,
        "models": "gfs_seamless",
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    hourly = data.get("hourly", {})
    # Use current hour's data
    idx = min(hour, len(hourly.get("temperature_2m", [])) - 1)
    if idx < 0:
        return None

    temp = hourly["temperature_2m"][idx]
    humidity = hourly["relative_humidity_2m"][idx]
    pressure = hourly["surface_pressure"][idx]
    wind_speed = hourly["wind_speed_10m"][idx]
    wind_dir = hourly["wind_direction_10m"][idx]
    precip = hourly["precipitation"][idx]

    if any(v is None for v in [temp, humidity, pressure, wind_speed]):
        return None

    return {
        "lat": lat,
        "lng": lng,
        "temp": temp,
        "humidity": humidity,
        "pressure": pressure,
        "wind_speed": wind_speed,  # km/h from Open-Meteo
        "wind_direction": wind_dir,
        "precipitation": precip or 0,
    }


def score_regions(weather_data, grid_resolution=0.25):
    """Score weather locations for visual interest.
    Returns top 10 with minimum 15° separation."""
    if not weather_data:
        return []

    # Extract arrays for scoring
    pressures = np.array([w["pressure"] for w in weather_data])
    winds = np.array([w["wind_speed"] for w in weather_data])
    temps = np.array([w["temp"] for w in weather_data])
    humidities = np.array([w["humidity"] for w in weather_data])
    precips = np.array([w["precipitation"] for w in weather_data])

    # Pressure anomaly (deviation from global mean)
    pressure_anomaly = np.abs(pressures - np.mean(pressures))

    # Temperature anomaly (deviation from global mean)
    temp_anomaly = np.abs(temps - np.mean(temps))

    # Composite score
    scores = (
        0.30 * normalize(pressure_anomaly) +
        0.25 * normalize(winds) +
        0.20 * normalize(temp_anomaly) +
        0.15 * normalize(precips) +
        0.10 * normalize(humidities)
    )

    # Sort by score descending
    ranked = sorted(zip(scores, weather_data), key=lambda x: -x[0])

    # Pick top 10 with minimum 15° separation
    regions = []
    for score, w in ranked:
        if len(regions) >= 10:
            break

        # Check separation
        too_close = False
        for r in regions:
            dlat = abs(w["lat"] - r["lat"])
            dlng = abs(w["lng"] - r["lng"])
            if dlng > 180:
                dlng = 360 - dlng
            if dlat < 15.0 and dlng < 15.0:
                too_close = True
                break

        if too_close:
            continue

        region = {
            "lat": w["lat"],
            "lng": w["lng"],
            "slug": make_slug(w["lat"], w["lng"]),
            "pressure": round(w["pressure"], 1),
            "pressure_gradient": round(float(pressure_anomaly[weather_data.index(w)]), 2),
            "wind_speed": round(w["wind_speed"] / 3.6, 1),  # km/h to m/s
            "wind_direction": round(w["wind_direction"], 1),
            "temp": round(w["temp"], 1),
            "temp_anomaly": round(float(temp_anomaly[weather_data.index(w)]), 1),
            "humidity": round(w["humidity"], 1),
            "precipitation": round(w["precipitation"], 1),
            "score": round(float(score) * 100, 1),
        }
        regions.append(region)

    return regions


def normalize(arr):
    """Min-max normalize array to [0, 1]."""
    arr_min = np.nanmin(arr)
    arr_max = np.nanmax(arr)
    if arr_max == arr_min:
        return np.zeros_like(arr, dtype=np.float32)
    return ((arr - arr_min) / (arr_max - arr_min)).astype(np.float32)


def make_slug(lat, lng):
    """Maps lat/lng to human-readable region name slug."""
    for (lat_min, lat_max), (lng_min, lng_max), name in REGION_NAMES:
        if lat_min <= lat < lat_max and lng_min <= lng < lng_max:
            slug = name.lower().replace(" / ", "-").replace(" ", "-")
            lat_tag = f"{abs(lat):.0f}{'n' if lat >= 0 else 's'}"
            lng_tag = f"{abs(lng):.0f}{'e' if lng >= 0 else 'w'}"
            return f"{slug}-{lat_tag}-{lng_tag}"
    lat_tag = f"{abs(lat):.0f}{'n' if lat >= 0 else 's'}"
    lng_tag = f"{abs(lng):.0f}{'e' if lng >= 0 else 'w'}"
    return f"region-{lat_tag}-{lng_tag}"
