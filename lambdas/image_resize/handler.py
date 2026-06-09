"""
art-image-resize — generate WebP variants AND pre-warm watermarked downloads.

Triggered by S3 PUT events on `weather/*/preview-2048.png`. Produces
preview-480.webp / preview-960.webp / preview-1200.webp /
preview-1920.webp at quality 82, written to the same prefix. The 1200
variant exists for og:image / twitter:image (social scrapers want
~1200px and the old 2048 PNG was ~1.7 MB). Idempotent — skips a
variant if it already exists.

After WebP generation, fans out two async invocations of
art-watermark-download (z=4k, z=8k) so the watermarked PNGs are cached
in `site/downloads/...` before any visitor clicks Download. Removes the
~90s cold-start wait users used to see on first download.

Also runs as a backfill via direct invoke:
- {run_id, slug}     — single piece
- {backfill: true}   — every preview-2048.png in the bucket
"""
import io
import os
import json
import urllib.parse

import boto3
from botocore.exceptions import ClientError
from PIL import Image

BUCKET = os.environ.get("BUCKET_NAME", "art-generator-216890068001")
WIDTHS = [480, 960, 1200, 1920]
QUALITY = 82
WATERMARK_FUNCTION = os.environ.get("WATERMARK_FUNCTION", "art-watermark-download")
WATERMARK_SIZES = ["4k", "8k"]

s3 = boto3.client("s3")
lambda_client = boto3.client("lambda")


def _exists(key: str) -> bool:
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def _key_for(run_id: str, slug: str) -> str:
    return f"weather/{run_id}/{slug}/preview-2048.png"


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
            scaled = img
        else:
            ratio = w / img.width
            scaled = img.resize((w, int(img.height * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        scaled.save(buf, format="WEBP", quality=QUALITY, method=4)
        s3.put_object(
            Bucket=BUCKET,
            Key=out_key,
            Body=buf.getvalue(),
            ContentType="image/webp",
            CacheControl="public, max-age=31536000, immutable",
        )
        written += 1
    return {"src": src_key, "written": written, "skipped": skipped}


def _prewarm_watermarks(run_id: str, slug: str) -> dict:
    """Async-invoke the watermark Lambda for both 4k + 8k so the
    watermarked PNG lands in site/downloads/ before users ask. Skip
    invocation if the cached file already exists."""
    fired = []
    skipped = []
    for size in WATERMARK_SIZES:
        cache_key = f"site/downloads/{run_id}/{slug}/preview-{size}.png"
        if _exists(cache_key):
            skipped.append(size)
            continue
        # The watermark Lambda's handler reads `r`, `s`, `z` from
        # queryStringParameters — emit a Function-URL-shaped event.
        payload = {
            "queryStringParameters": {"r": run_id, "s": slug, "z": size},
        }
        try:
            lambda_client.invoke(
                FunctionName=WATERMARK_FUNCTION,
                InvocationType="Event",  # async, fire-and-forget
                Payload=json.dumps(payload).encode(),
            )
            fired.append(size)
        except ClientError as e:
            print(f"prewarm {run_id}/{slug} z={size}: {e}")
    return {"fired": fired, "skipped": skipped}


def _process_piece(run_id: str, slug: str, force: bool = False) -> dict:
    """One piece end-to-end: WebP + prewarm watermarks."""
    src = _key_for(run_id, slug)
    resize = _resize_one(src, force=force)
    prewarm = _prewarm_watermarks(run_id, slug)
    return {"resize": resize, "prewarm": prewarm}


def _split_key(key: str) -> tuple[str, str] | None:
    """weather/{run_id}/{slug}/preview-2048.png -> (run_id, slug)."""
    parts = key.split("/")
    if len(parts) >= 4 and parts[0] == "weather" and parts[-1] == "preview-2048.png":
        return parts[1], parts[2]
    return None


def handler(event, context):
    # S3 trigger event
    if "Records" in event:
        results = []
        for r in event["Records"]:
            key = urllib.parse.unquote_plus(r["s3"]["object"]["key"])
            if not key.endswith("/preview-2048.png"):
                continue
            if "/weather/" not in key and not key.startswith("weather/"):
                continue
            split = _split_key(key)
            if not split:
                continue
            run_id, slug = split
            try:
                results.append(_process_piece(run_id, slug))
            except Exception as e:
                print(f"process {key}: {e}")
                results.append({"src": key, "error": str(e)})
        return {"results": results}

    # Direct invoke for one piece: {run_id, slug, force?}
    run_id = event.get("run_id")
    slug = event.get("slug")
    if run_id and slug:
        return _process_piece(run_id, slug, force=bool(event.get("force")))

    # Bulk backfill: {backfill: true, force?, prewarm_only?}
    if event.get("backfill"):
        force = bool(event.get("force"))
        prewarm_only = bool(event.get("prewarm_only"))
        results = {
            "processed": 0,
            "webp_written": 0,
            "webp_skipped": 0,
            "prewarm_fired": 0,
            "prewarm_skipped": 0,
            "errors": 0,
        }
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=BUCKET, Prefix="weather/"):
            for obj in page.get("Contents", []) or []:
                key = obj["Key"]
                if not key.endswith("/preview-2048.png"):
                    continue
                split = _split_key(key)
                if not split:
                    continue
                run_id, slug = split
                try:
                    if not prewarm_only:
                        r = _resize_one(key, force=force)
                        results["webp_written"] += r["written"]
                        results["webp_skipped"] += r["skipped"]
                    p = _prewarm_watermarks(run_id, slug)
                    results["prewarm_fired"] += len(p["fired"])
                    results["prewarm_skipped"] += len(p["skipped"])
                    results["processed"] += 1
                except Exception as e:
                    print(f"backfill {key}: {e}")
                    results["errors"] += 1
        return results

    return {"error": "no recognized event shape"}
