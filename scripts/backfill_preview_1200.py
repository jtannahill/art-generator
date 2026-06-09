"""Backfill preview-1200.webp for every weather/ piece.

For each weather/{run_id}/{slug}/preview-2048.png:
  - skip if weather/.../preview-1200.webp already exists (idempotent)
  - download the 2048 PNG, resize to 1200px (LANCZOS), encode WebP q82
  - upload to BOTH weather/... (source of truth) and site/weather/...
    (CloudFront origin prefix) with image/webp + immutable cache-control

Run:
  uv run --no-project --with pillow --with boto3 python scripts/backfill_preview_1200.py
"""
import io
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.exceptions import ClientError
from PIL import Image

BUCKET = "art-generator-216890068001"
WIDTH = 1200
QUALITY = 82

session = boto3.session.Session(region_name="us-east-1")
s3 = session.client("s3")
lock = threading.Lock()
done = 0
total = 0


def exists(key: str) -> bool:
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def list_pngs():
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix="weather/"):
        for obj in page.get("Contents", []) or []:
            if obj["Key"].endswith("/preview-2048.png"):
                yield obj["Key"]


def process(src_key: str) -> str:
    global done
    base = src_key.rsplit("/", 1)[0]
    out_key = f"{base}/preview-1200.webp"
    site_key = f"site/{out_key}"

    if exists(out_key) and exists(site_key):
        status = "skip"
    else:
        body = s3.get_object(Bucket=BUCKET, Key=src_key)["Body"].read()
        img = Image.open(io.BytesIO(body)).convert("RGB")
        if img.width > WIDTH:
            ratio = WIDTH / img.width
            img = img.resize((WIDTH, int(img.height * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=QUALITY, method=4)
        data = buf.getvalue()
        for key in (out_key, site_key):
            s3.put_object(
                Bucket=BUCKET,
                Key=key,
                Body=data,
                ContentType="image/webp",
                CacheControl="public, max-age=31536000, immutable",
            )
        status = f"ok {len(data)}b"
    with lock:
        done += 1
        print(f"[{done}/{total}] {status} {out_key}", flush=True)
    return status


def main():
    global total
    keys = list(list_pngs())
    total = len(keys)
    print(f"found {total} preview-2048.png sources", flush=True)
    errors = 0
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(process, k): k for k in keys}
        for f in as_completed(futs):
            try:
                f.result()
            except Exception as e:
                errors += 1
                print(f"ERROR {futs[f]}: {e}", flush=True)
    print(f"DONE total={total} errors={errors}", flush=True)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
