"""Editions action — lazy EDITION creation and lookup."""

import os
import re
from decimal import Decimal

import boto3

try:
    from tiers import get_tiers_for_format
except ImportError:
    from .tiers import get_tiers_for_format

BUCKET_NAME = os.environ.get("BUCKET_NAME", "art-generator-216890068001")


def get_editions(table, run_id: str, slug: str) -> dict | None:
    """Get edition data for an artwork. Creates EDITION item lazily on first read.

    Returns dict with aspect_ratio, featured, sizes (with sold counts), or None if artwork not found.
    """
    edition_pk = f"EDITION#{run_id}#{slug}"

    # Check for existing EDITION item
    resp = table.get_item(Key={"PK": edition_pk, "SK": "META"})
    if "Item" in resp and resp["Item"]:
        item = resp["Item"]
        return _serialize_edition({
            "aspect_ratio": item["aspect_ratio"],
            "featured": item.get("featured", False),
            "sizes": item["sizes"],
            "canvas_format": item["canvas_format"],
        })

    # No EDITION — look up the WEATHER item to get canvas_format
    weather_resp = table.get_item(Key={"PK": f"WEATHER#{run_id}", "SK": slug})
    weather = weather_resp.get("Item")
    if not weather:
        return None

    canvas_format = weather.get("canvas_format")
    if not canvas_format:
        canvas_format = _parse_viewbox_from_s3(run_id, slug)
        if not canvas_format:
            return None

    tiers = get_tiers_for_format(canvas_format)
    edition_item = {
        "PK": edition_pk,
        "SK": "META",
        "canvas_format": canvas_format,
        "aspect_ratio": tiers["aspect_ratio"],
        "featured": False,
        "sizes": tiers["sizes"],
    }
    table.put_item(Item=edition_item)

    return _serialize_edition({
        "aspect_ratio": tiers["aspect_ratio"],
        "featured": False,
        "sizes": tiers["sizes"],
        "canvas_format": canvas_format,
    })


def _serialize_edition(edition: dict) -> dict:
    """Convert DynamoDB Decimal values to int for JSON serialization."""
    result = {**edition}
    if "sizes" in result:
        result["sizes"] = {
            k: {sk: int(sv) if isinstance(sv, Decimal) else sv for sk, sv in v.items()}
            for k, v in result["sizes"].items()
        }
    return result


def _parse_viewbox_from_s3(run_id: str, slug: str) -> str | None:
    """Fallback: read SVG from S3 and parse viewBox dimensions."""
    s3 = boto3.client("s3")
    try:
        obj = s3.get_object(Bucket=BUCKET_NAME, Key=f"weather/{run_id}/{slug}/artwork.svg")
        svg_text = obj["Body"].read().decode("utf-8")
        match = re.search(r'viewBox\s*=\s*"([^"]+)"', svg_text)
        if match:
            parts = match.group(1).split()
            if len(parts) == 4:
                return f"{int(float(parts[2]))}x{int(float(parts[3]))}"
    except Exception:
        pass
    return None
