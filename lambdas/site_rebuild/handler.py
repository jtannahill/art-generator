"""Site Rebuild Lambda — scans DynamoDB for all weather and palette items,
renders static HTML pages via Jinja2, uploads to S3, and invalidates CloudFront."""

import json
import os
from collections import defaultdict
from datetime import datetime, timezone

import boto3
from jinja2 import Environment, FileSystemLoader

BUCKET_NAME = os.environ.get("BUCKET_NAME", "")
TABLE_NAME = os.environ.get("TABLE_NAME", "")
DISTRIBUTION_ID = os.environ.get("DISTRIBUTION_ID", "")

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")


def handler(event, context):
    """Scans DynamoDB, renders all pages, uploads to S3, invalidates CloudFront."""
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(TABLE_NAME)
    items = scan_all(table)

    # Separate weather and palette items
    weather_items = [i for i in items if i.get("PK", "").startswith("WEATHER#")]
    palette_items = [i for i in items if i.get("PK", "").startswith("PALETTE#")]

    # Parse colors for palette items
    for item in palette_items:
        item["colors_parsed"] = _parse_colors(item.get("colors", "[]"))

    # Group data
    weather_by_date = group_by_date(weather_items, prefix="WEATHER#")
    palettes_by_location = group_by_location(palette_items)
    palettes_by_date = group_palette_by_date(palette_items)

    # Set up Jinja2
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)

    pages = {}

    # Render index
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_weather = weather_by_date.get(today, [])
    latest_palettes = _latest_palettes(palettes_by_location)
    pages["site/index.html"] = env.get_template("index.html").render(
        today_weather=today_weather,
        latest_palettes=latest_palettes,
        today=today,
    )

    # Render weather archive
    pages["site/weather/index.html"] = env.get_template("weather_archive.html").render(
        weather_by_date=weather_by_date,
    )

    # Render weather day pages
    for date, artworks in weather_by_date.items():
        pages[f"site/weather/{date}/index.html"] = env.get_template(
            "weather_day.html"
        ).render(date=date, artworks=artworks)

        # Render individual weather artwork pages
        for artwork in artworks:
            slug = artwork.get("SK", artwork.get("slug", ""))
            pages[f"site/weather/{date}/{slug}/index.html"] = env.get_template(
                "weather_single.html"
            ).render(artwork=artwork, date=date, slug=slug)

    # Render palette archive
    pages["site/palettes/index.html"] = env.get_template(
        "palette_archive.html"
    ).render(palettes_by_location=palettes_by_location)

    # Render palette location pages
    for location_slug, palettes in palettes_by_location.items():
        pages[f"site/palettes/{location_slug}/index.html"] = env.get_template(
            "palette_location.html"
        ).render(location=location_slug, palettes=palettes)

    # Render palette day pages
    for date, palettes in palettes_by_date.items():
        pages[f"site/palettes/{date}/index.html"] = env.get_template(
            "palette_day.html"
        ).render(date=date, palettes=palettes)

    # Upload all pages to S3
    s3 = boto3.client("s3")
    for key, html in pages.items():
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=key,
            Body=html.encode("utf-8"),
            ContentType="text/html",
            CacheControl="public, max-age=300",
        )

    # Copy artwork assets into site/ prefix so CloudFront can serve them
    _copy_assets_to_site(s3, weather_by_date, palettes_by_date)

    # Invalidate CloudFront
    if DISTRIBUTION_ID:
        cf = boto3.client("cloudfront")
        cf.create_invalidation(
            DistributionId=DISTRIBUTION_ID,
            InvalidationBatch={
                "Paths": {
                    "Quantity": 3,
                    "Items": ["/index.html", "/weather/*", "/palettes/*"],
                },
                "CallerReference": f"rebuild-{today}",
            },
        )

    return {
        "pages_rendered": len(pages),
        "weather_dates": len(weather_by_date),
        "palette_locations": len(palettes_by_location),
    }


def scan_all(table):
    """Full DynamoDB table scan with pagination."""
    items = []
    params = {}
    while True:
        response = table.scan(**params)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        params["ExclusiveStartKey"] = last_key
    return items


def group_by_date(items, prefix="WEATHER#"):
    """Groups items by date extracted from PK (e.g. 'WEATHER#2026-03-15' -> '2026-03-15').

    Returns dict of date -> list of items, sorted by date descending.
    """
    groups = defaultdict(list)
    for item in items:
        pk = item.get("PK", "")
        if pk.startswith(prefix):
            date = pk[len(prefix):]
            groups[date].append(item)

    # Sort by date descending
    return dict(sorted(groups.items(), key=lambda x: x[0], reverse=True))


def group_by_location(items):
    """Groups palette items by location slug from PK (e.g. 'PALETTE#sahara' -> 'sahara').

    Each location's items are sorted by date (SK) descending.
    Returns dict of slug -> list of items.
    """
    groups = defaultdict(list)
    for item in items:
        pk = item.get("PK", "")
        if pk.startswith("PALETTE#"):
            slug = pk[len("PALETTE#"):]
            groups[slug].append(item)

    # Sort each location's items by SK (date) descending
    for slug in groups:
        groups[slug].sort(key=lambda x: x.get("SK", ""), reverse=True)

    return dict(sorted(groups.items()))


def group_palette_by_date(items):
    """Groups palette items by date from SK.

    Returns dict of date -> list of items, sorted by date descending.
    """
    groups = defaultdict(list)
    for item in items:
        date = item.get("SK", "")
        if date:
            groups[date].append(item)

    return dict(sorted(groups.items(), key=lambda x: x[0], reverse=True))


def _parse_colors(colors):
    """Parse colors field — may be a JSON string or already a list."""
    if isinstance(colors, list):
        return colors
    if isinstance(colors, str):
        try:
            parsed = json.loads(colors)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def _latest_palettes(palettes_by_location):
    """Get the most recent palette for each location."""
    latest = []
    for slug, palettes in palettes_by_location.items():
        if palettes:
            item = dict(palettes[0])
            item["location_slug"] = slug
            latest.append(item)
    return latest


def _copy_assets_to_site(s3, weather_by_date, palettes_by_date):
    """Copy artwork SVGs and palette assets into the site/ prefix for CloudFront."""
    for date, artworks in weather_by_date.items():
        for artwork in artworks:
            slug = artwork.get("SK", artwork.get("slug", ""))
            src_prefix = f"weather/{date}/{slug}/"
            dst_prefix = f"site/weather/{date}/{slug}/"
            # List and copy all objects in the artwork folder
            try:
                resp = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix=src_prefix)
                for obj in resp.get("Contents", []):
                    src_key = obj["Key"]
                    filename = src_key.split("/")[-1]
                    dst_key = dst_prefix + filename
                    s3.copy_object(
                        Bucket=BUCKET_NAME,
                        CopySource={"Bucket": BUCKET_NAME, "Key": src_key},
                        Key=dst_key,
                    )
            except Exception as e:
                print(f"Failed to copy assets for {slug}: {e}")

    for date, palettes in palettes_by_date.items():
        for palette in palettes:
            slug = palette.get("slug", palette.get("SK", ""))
            src_prefix = f"palettes/{date}/{slug}/"
            dst_prefix = f"site/palettes/{date}/{slug}/"
            try:
                resp = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix=src_prefix)
                for obj in resp.get("Contents", []):
                    src_key = obj["Key"]
                    filename = src_key.split("/")[-1]
                    dst_key = dst_prefix + filename
                    s3.copy_object(
                        Bucket=BUCKET_NAME,
                        CopySource={"Bucket": BUCKET_NAME, "Key": src_key},
                        Key=dst_key,
                    )
            except Exception as e:
                print(f"Failed to copy palette assets for {slug}: {e}")
