"""Satellite Ingest Lambda — fetches Sentinel-2 imagery for active locations."""

import json
import os
from datetime import datetime, timedelta

import boto3
import requests

BUCKET_NAME = os.environ.get("BUCKET_NAME", "")
COPERNICUS_CLIENT_ID = os.environ.get("COPERNICUS_CLIENT_ID", "")
COPERNICUS_CLIENT_SECRET = os.environ.get("COPERNICUS_CLIENT_SECRET", "")

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
CATALOG_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "locations.json")


def filter_active_locations(locations: list, month: int) -> list:
    """Return locations where month is in active_months."""
    return [loc for loc in locations if month in loc.get("active_months", [])]


def get_copernicus_token() -> str:
    """OAuth2 client_credentials flow to Copernicus Data Space."""
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": COPERNICUS_CLIENT_ID,
            "client_secret": COPERNICUS_CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch_sentinel2_image(token: str, location: dict, date: str):
    """Search Copernicus catalog for recent Sentinel-2 imagery.

    Looks for images within the last 30 days with < 10% cloud cover.
    Downloads the quicklook thumbnail.

    Returns:
        (jpeg_bytes, cloud_cover_pct) or (None, None)
    """
    end_date = datetime.strptime(date, "%Y-%m-%d")
    start_date = end_date - timedelta(days=30)

    lat = location["lat"]
    lng = location["lng"]

    # Build a small bounding box (~0.5 degrees around the point)
    bbox_size = 0.25
    bbox = f"POLYGON(({lng - bbox_size} {lat - bbox_size},{lng + bbox_size} {lat - bbox_size},{lng + bbox_size} {lat + bbox_size},{lng - bbox_size} {lat + bbox_size},{lng - bbox_size} {lat - bbox_size}))"

    filter_query = (
        f"Collection/Name eq 'SENTINEL-2' "
        f"and OData.CSC.Intersects(area=geography'SRID=4326;{bbox}') "
        f"and ContentDate/Start gt {start_date.strftime('%Y-%m-%dT00:00:00.000Z')} "
        f"and ContentDate/Start lt {end_date.strftime('%Y-%m-%dT23:59:59.999Z')} "
        f"and Attributes/OData.CSC.DoubleAttribute/any(att:att/Name eq 'cloudCover' and att/Value lt 10.0)"
    )

    params = {
        "$filter": filter_query,
        "$orderby": "ContentDate/Start desc",
        "$top": 1,
    }

    headers = {"Authorization": f"Bearer {token}"}

    resp = requests.get(CATALOG_URL, params=params, headers=headers, timeout=60)
    resp.raise_for_status()
    results = resp.json().get("value", [])

    if not results:
        return None, None

    product = results[0]
    product_id = product["Id"]

    # Extract cloud cover from attributes
    cloud_cover = None
    for attr in product.get("Attributes", []):
        if attr.get("Name") == "cloudCover":
            cloud_cover = attr["Value"]
            break

    # Download quicklook
    quicklook_url = (
        f"https://zipper.dataspace.copernicus.eu/odata/v1/Products({product_id})/Quicklook"
    )
    img_resp = requests.get(quicklook_url, headers=headers, timeout=120)
    img_resp.raise_for_status()

    return img_resp.content, cloud_cover


def handler(event, context):
    """Fetch Sentinel-2 imagery for active locations and save to S3."""
    today = datetime.utcnow()
    date_str = today.strftime("%Y-%m-%d")
    month = today.month

    # Load locations config
    config_path = os.environ.get("CONFIG_PATH", CONFIG_PATH)
    with open(config_path, "r") as f:
        locations = json.load(f)

    active = filter_active_locations(locations, month)
    print(f"Found {len(active)} active locations for month {month}")

    # Skip if Copernicus credentials not configured
    if not COPERNICUS_CLIENT_ID or not COPERNICUS_CLIENT_SECRET:
        print("Copernicus credentials not configured, skipping satellite ingest")
        return {"date": date_str, "locations": []}

    token = get_copernicus_token()
    s3 = boto3.client("s3")

    results = []
    for loc in active:
        slug = loc["slug"]
        print(f"Fetching imagery for {loc['name']} ({slug})...")

        try:
            jpeg_bytes, cloud_cover = fetch_sentinel2_image(token, loc, date_str)
        except Exception as e:
            print(f"Error fetching {slug}: {e}")
            continue

        if jpeg_bytes is None:
            print(f"No suitable imagery found for {slug}")
            continue

        s3_key = f"satellite/{date_str}/{slug}/source.jpg"
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_key,
            Body=jpeg_bytes,
            ContentType="image/jpeg",
        )

        results.append(
            {
                "slug": slug,
                "name": loc["name"],
                "lat": loc["lat"],
                "lng": loc["lng"],
                "tags": loc["tags"],
                "date": date_str,
                "cloud_cover": cloud_cover,
                "s3_key": s3_key,
            }
        )
        print(f"Saved {slug}: cloud_cover={cloud_cover}%, s3_key={s3_key}")

    return {"date": date_str, "locations": results}
