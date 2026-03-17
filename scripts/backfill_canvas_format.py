#!/usr/bin/env python3
"""One-time backfill: parse SVG viewBox from S3, write canvas_format to DynamoDB,
and re-render PNGs with correct aspect ratio.

Requires: boto3, cairosvg (pip install cairosvg)
Usage: python scripts/backfill_canvas_format.py [--dry-run]
"""

import json
import re
import sys
import boto3

BUCKET_NAME = "art-generator-216890068001"
TABLE_NAME = "art-generator"


def parse_viewbox(svg_text: str) -> str | None:
    match = re.search(r'viewBox\s*=\s*"([^"]+)"', svg_text)
    if not match:
        return None
    parts = match.group(1).split()
    if len(parts) == 4:
        w, h = parts[2], parts[3]
        return f"{int(float(w))}x{int(float(h))}"
    return None


def render_png(svg_text: str, width: int) -> bytes:
    import cairosvg
    return cairosvg.svg2png(bytestring=svg_text.encode("utf-8"), output_width=width)


def main():
    dry_run = "--dry-run" in sys.argv
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(TABLE_NAME)
    s3 = boto3.client("s3")

    items = []
    params = {"FilterExpression": "begins_with(PK, :prefix)", "ExpressionAttributeValues": {":prefix": "WEATHER#"}}
    while True:
        resp = table.scan(**params)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        params["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    print(f"Found {len(items)} WEATHER# items")
    updated = skipped = errors = 0

    for item in items:
        pk, sk = item["PK"], item["SK"]
        run_id = pk.replace("WEATHER#", "")
        slug = sk

        if item.get("canvas_format"):
            skipped += 1
            continue

        s3_key = f"weather/{run_id}/{slug}/artwork.svg"
        try:
            obj = s3.get_object(Bucket=BUCKET_NAME, Key=s3_key)
            svg_text = obj["Body"].read().decode("utf-8")
        except Exception as e:
            print(f"  ERROR reading {s3_key}: {e}")
            errors += 1
            continue

        canvas_format = parse_viewbox(svg_text)
        if not canvas_format:
            print(f"  ERROR: no viewBox in {s3_key}")
            errors += 1
            continue

        print(f"  {run_id}/{slug}: {canvas_format}", end="")

        if dry_run:
            print(" (dry run)")
            updated += 1
            continue

        table.update_item(
            Key={"PK": pk, "SK": sk},
            UpdateExpression="SET canvas_format = :cf",
            ExpressionAttributeValues={":cf": canvas_format},
        )

        try:
            png_2048 = render_png(svg_text, 2048)
            png_4k = render_png(svg_text, 4096)
            prefix = f"weather/{run_id}/{slug}"
            s3.put_object(Bucket=BUCKET_NAME, Key=f"{prefix}/preview-2048.png", Body=png_2048, ContentType="image/png")
            s3.put_object(Bucket=BUCKET_NAME, Key=f"{prefix}/preview-4k.png", Body=png_4k, ContentType="image/png")
            print(" + PNGs re-rendered")
        except Exception as e:
            print(f" (PNGs failed: {e})")

        updated += 1

    print(f"\nDone: {updated} updated, {skipped} skipped, {errors} errors")


if __name__ == "__main__":
    main()
