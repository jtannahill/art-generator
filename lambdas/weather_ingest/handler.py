"""Weather Ingest Lambda — downloads GFS data from NOAA NOMADS, scores regions,
returns top 10 most visually interesting weather locations."""

import io
import json
import math
import os
import struct
from datetime import datetime, timezone

import boto3
import numpy as np
import requests

BUCKET_NAME = os.environ.get("BUCKET_NAME", "art-generator-216890068001")

# GFS 0.25-degree grid dimensions
NLAT = 721   # 90 to -90 in 0.25 steps
NLON = 1440  # 0 to 359.75 in 0.25 steps

# NOMADS base URL pattern — GFS 0.25 degree
NOMADS_BASE = (
    "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
    "?dir=%2Fgfs.{date}%2F{hour}%2Fatmos"
    "&file=gfs.t{hour}z.pgrb2.0p25.f000"
)

# Region name mapping by lat/lng quadrant
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


def handler(event, context):
    """Lambda entry point. Downloads GFS data from NOAA NOMADS, scores regions,
    returns top 10 visually interesting weather locations."""
    now = datetime.now(timezone.utc)
    # GFS runs at 00, 06, 12, 18 — use most recent completed (~4h delay)
    run_hour = ((now.hour - 4) // 6) * 6
    if run_hour < 0:
        run_hour = 18
        # Would need previous day, keep simple for now
    run_hour_str = f"{run_hour:02d}"
    date_str = now.strftime("%Y%m%d")
    base_url = NOMADS_BASE.format(date=date_str, hour=run_hour_str)

    # Fetch GFS fields
    fields = {
        "pressure": ("PRMSL", "mean+sea+level"),
        "wind_u": ("UGRD", "10+m+above+ground"),
        "wind_v": ("VGRD", "10+m+above+ground"),
        "temp": ("TMP", "2+m+above+ground"),
        "humidity": ("RH", "2+m+above+ground"),
        "precip": ("APCP", "surface"),
    }

    arrays = {}
    for key, (var, level) in fields.items():
        data = fetch_gfs_field(base_url, var, level)
        arr = parse_grib2_simple(data, NLAT, NLON)
        arrays[key] = arr

    # Archive raw arrays to S3
    s3 = boto3.client("s3")
    archive_key = f"weather/{now.strftime('%Y-%m-%d')}/raw/gfs_{date_str}_{run_hour_str}z.npz"
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    buf.seek(0)
    s3.put_object(Bucket=BUCKET_NAME, Key=archive_key, Body=buf.getvalue())

    # Score regions
    regions = score_regions(
        pressure=arrays["pressure"],
        wind_u=arrays["wind_u"],
        wind_v=arrays["wind_v"],
        temp=arrays["temp"],
        humidity=arrays["humidity"],
        precip=arrays["precip"],
    )

    # Add date metadata
    for r in regions:
        r["date"] = now.strftime("%Y-%m-%d")
        r["gfs_run"] = f"{date_str}_{run_hour_str}z"

    return {"regions": regions}


def fetch_gfs_field(base_url, var, level):
    """Fetches a single GFS field via NOMADS CGI filter, returns raw bytes."""
    url = f"{base_url}&var_{var}=on&lev_{level}=on"
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    return resp.content


def parse_grib2_simple(data, nlat, nlon):
    """Extracts data from GRIB2 simple packing.
    Reads section 5 for reference value, binary scale, decimal scale, and nbits.
    Reads section 7 for packed data values."""
    buf = data
    pos = 0

    # Skip to find sections — GRIB2 starts with 'GRIB'
    grib_start = buf.find(b"GRIB")
    if grib_start < 0:
        raise ValueError("No GRIB2 header found")
    pos = grib_start + 16  # skip section 0 (indicator, 16 bytes)

    ref_value = 0.0
    binary_scale = 0
    decimal_scale = 0
    nbits = 0
    nvalues = nlat * nlon
    packed_data = None

    while pos < len(buf) - 4:
        # Each section: 4-byte length, 1-byte section number
        if buf[pos : pos + 4] == b"7777":
            break
        sec_len = struct.unpack(">I", buf[pos : pos + 4])[0]
        sec_num = buf[pos + 4]

        if sec_num == 5:
            # Section 5: Data Representation
            # Bytes 12-15: reference value (IEEE 754 float)
            # Bytes 16-17: binary scale factor (signed int16)
            # Bytes 18-19: decimal scale factor (signed int16)
            # Byte 20: number of bits per value
            nvalues = struct.unpack(">I", buf[pos + 5 : pos + 9])[0]
            ref_value = struct.unpack(">f", buf[pos + 11 : pos + 15])[0]
            binary_scale = struct.unpack(">h", buf[pos + 15 : pos + 17])[0]
            decimal_scale = struct.unpack(">h", buf[pos + 17 : pos + 19])[0]
            nbits = buf[pos + 19]

        elif sec_num == 7:
            # Section 7: Data — packed values start at byte 5
            packed_data = buf[pos + 5 : pos + sec_len]

        pos += sec_len

    if packed_data is None:
        raise ValueError("No data section found in GRIB2")

    # Unpack values
    D = 10.0 ** decimal_scale
    E = 2.0 ** binary_scale

    if nbits == 0:
        # Constant field
        values = np.full(nvalues, ref_value / D, dtype=np.float32)
    else:
        # Unpack bit-packed integers
        values = _unpack_bits(packed_data, nbits, nvalues)
        values = (ref_value + values * E) / D

    return values.reshape((nlat, nlon)).astype(np.float32)


def _unpack_bits(data, nbits, nvalues):
    """Unpack nvalues integers of nbits each from packed byte data."""
    arr = np.frombuffer(data, dtype=np.uint8)
    # Convert to bit array
    bits = np.unpackbits(arr)
    total_bits = nvalues * nbits
    bits = bits[:total_bits]
    # Reshape and convert groups of nbits to integers
    bits = bits.reshape(nvalues, nbits)
    # Build integer values from bits (MSB first)
    powers = 2 ** np.arange(nbits - 1, -1, -1, dtype=np.float64)
    values = bits.astype(np.float64) @ powers
    return values


def score_regions(pressure, wind_u, wind_v, temp, humidity=None, precip=None, grid_resolution=0.25):
    """Scores global grid for visually interesting weather.
    Uses pressure gradients, wind speed, temperature anomalies.
    Returns top 10 regions with minimum 5 degree separation."""
    nlat, nlon = pressure.shape

    # Pressure gradient magnitude (central differences)
    grad_lat = np.gradient(pressure, axis=0)
    grad_lon = np.gradient(pressure, axis=1)
    pressure_gradient = np.sqrt(grad_lat ** 2 + grad_lon ** 2)

    # Wind speed
    wind_speed = np.sqrt(wind_u ** 2 + wind_v ** 2)

    # Temperature anomaly (deviation from zonal mean)
    zonal_mean = np.nanmean(temp, axis=1, keepdims=True)
    temp_anomaly = np.abs(temp - zonal_mean)

    # Composite score
    score = (
        0.35 * normalize(pressure_gradient)
        + 0.30 * normalize(wind_speed)
        + 0.20 * normalize(temp_anomaly)
    )

    # Add humidity contribution if available
    if humidity is not None:
        humidity_score = normalize(humidity)
        score += 0.08 * humidity_score

    # Add precipitation contribution if available
    if precip is not None:
        precip_score = normalize(precip)
        score += 0.07 * precip_score

    # If neither humidity nor precip, redistribute weights already in score
    if humidity is None and precip is None:
        score = score / 0.85  # renormalize

    # Find top regions with minimum 5-degree separation
    regions = []
    # Flatten and sort by score descending
    flat_indices = np.argsort(score.ravel())[::-1]

    min_sep_pixels = int(5.0 / grid_resolution)

    for idx in flat_indices:
        if len(regions) >= 10:
            break

        i, j = divmod(int(idx), nlon)
        lat = 90.0 - i * grid_resolution
        lng = j * grid_resolution
        if lng > 180:
            lng -= 360

        # Check separation from existing picks
        too_close = False
        for r in regions:
            dlat = abs(lat - r["lat"])
            dlng = abs(lng - r["lng"])
            if dlng > 180:
                dlng = 360 - dlng
            if dlat < 5.0 and dlng < 5.0:
                too_close = True
                break

        if too_close:
            continue

        # Wind direction in degrees
        wd = (270 - math.degrees(math.atan2(float(wind_v[i, j]), float(wind_u[i, j])))) % 360

        region = {
            "lat": round(float(lat), 2),
            "lng": round(float(lng), 2),
            "slug": make_slug(lat, lng),
            "pressure": round(float(pressure[i, j]), 1),
            "pressure_gradient": round(float(pressure_gradient[i, j]), 2),
            "wind_speed": round(float(wind_speed[i, j]), 2),
            "wind_direction": round(wd, 1),
            "temp": round(float(temp[i, j]), 2),
            "temp_anomaly": round(float(temp_anomaly[i, j]), 2),
            "humidity": round(float(humidity[i, j]), 1) if humidity is not None else None,
            "precipitation": round(float(precip[i, j]), 2) if precip is not None else None,
            "score": round(float(score[i, j]), 4),
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
