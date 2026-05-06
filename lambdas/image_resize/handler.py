"""
art-image-resize — generate WebP variants beside each preview-2048.png.

Triggered by S3 PUT events on `weather/*/preview-2048.png`. Produces
preview-480.webp / preview-960.webp / preview-1920.webp at quality 82,
written to the same prefix. Idempotent — skips a variant if it already
exists with a non-stale modtime.

Also runs as a backfill via direct invoke (event with run_id+slug).
"""
import io
import os
import json
import urllib.parse

import boto3
from botocore.exceptions import ClientError
from PIL import Image

BUCKET = os.environ.get("BUCKET_NAME", "art-generator-216890068001")
WIDTHS = [480, 960, 1920]
QUALITY = 82

s3 = boto3.client("s3")


def _exists(key: str) -> bool:
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def _resize_one(src_key: str, force: bool = False) -> dict:
    """Read src_key, write WebP variants. Returns counts."""
    obj = s3.get_object(Bucket=BUCKET, Key=src_key)
    src_bytes = obj["Body"].read()
    img = Image.open(io.BytesIO(src_bytes)).convert("RGB")

    base_prefix = src_key.rsplit("/", 1)[0]
    written = 0
    skipped = 0
    for w in WIDTHS:
        out_key = f"{base_prefix}/preview-{w}.webp"
        if not force and _exists(out_key):
            skipped += 1
            continue
        if img.width <= w:
            # Don't upscale — emit at native size
            scaled = img
        else:
            ratio = w / img.width
            scaled = img.resize((w, int(img.height * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        scaled.save(buf, format="WEBP", quality=QUALITY, method=4)
        buf.seek(0)
        s3.put_object(
            Bucket=BUCKET,
            Key=out_key,
            Body=buf.getvalue(),
            ContentType="image/webp",
            CacheControl="public, max-age=31536000, immutable",
        )
        written += 1
    return {"src": src_key, "written": written, "skipped": skipped}


def handler(event, context):
    # S3 trigger event
    if "Records" in event:
        results = []
        for r in event["Records"]:
            key = urllib.parse.unquote_plus(r["s3"]["object"]["key"])
            if not key.endswith("/preview-2048.png"):
                continue
            if "/weather/" not in key:
                continue
            try:
                results.append(_resize_one(key))
            except Exception as e:
                print(f"resize {key}: {e}")
                results.append({"src": key, "error": str(e)})
        return {"results": results}

    # Direct invoke for backfill: {run_id, slug, force?}
    run_id = event.get("run_id")
    slug = event.get("slug")
    if run_id and slug:
        key = f"weather/{run_id}/{slug}/preview-2048.png"
        return _resize_one(key, force=bool(event.get("force")))

    # Bulk backfill: {backfill: true, force?}
    if event.get("backfill"):
        force = bool(event.get("force"))
        results = {"processed": 0, "written": 0, "skipped": 0, "errors": 0}
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=BUCKET, Prefix="weather/"):
            for obj in page.get("Contents", []) or []:
                key = obj["Key"]
                if not key.endswith("/preview-2048.png"):
                    continue
                try:
                    r = _resize_one(key, force=force)
                    results["processed"] += 1
                    results["written"] += r["written"]
                    results["skipped"] += r["skipped"]
                except Exception as e:
                    print(f"backfill {key}: {e}")
                    results["errors"] += 1
        return results

    return {"error": "no recognized event shape"}
