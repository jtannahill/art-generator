"""
art-site-rebuild — asset mirror only.

Replaces the original Jinja2 site renderer (now retired in favor of the
art-astro repo). This shrunken version exists solely to keep the daily
Step Function pipeline's SiteRebuildTask functional: it mirrors any new
files under weather/ and palettes/ into the site/ prefix that CloudFront's
origin path serves from.

Triggered by the art-daily-pipeline Step Function once per pipeline run.
Idempotent: only copies files that don't already exist (or where the
source has a newer modtime).
"""
import os
import boto3
from botocore.exceptions import ClientError

BUCKET = os.environ.get("BUCKET_NAME", "art-generator-216890068001")
s3 = boto3.client("s3")


def _list_keys(prefix):
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            yield obj["Key"], obj.get("LastModified"), obj.get("Size")


def _mirror(prefix):
    """Copy bucket://prefix/* → bucket://site/prefix/* for any keys that
    don't yet exist or where source is newer."""
    src_root = prefix
    dst_root = f"site/{prefix}"

    dst_index = {}
    for key, mtime, _ in _list_keys(dst_root):
        relative = key[len(dst_root):]
        dst_index[relative] = mtime

    copied = 0
    skipped = 0
    for key, mtime, size in _list_keys(src_root):
        relative = key[len(src_root):]
        existing = dst_index.get(relative)
        if existing is not None and existing >= mtime:
            skipped += 1
            continue
        try:
            s3.copy_object(
                Bucket=BUCKET,
                CopySource={"Bucket": BUCKET, "Key": key},
                Key=f"{dst_root}{relative}",
            )
            copied += 1
        except ClientError as e:
            print(f"copy failed {key}: {e}")
    return {"copied": copied, "skipped": skipped, "scanned": copied + skipped}


def handler(event, context):
    return {
        "weather": _mirror("weather/"),
        "palettes": _mirror("palettes/"),
        "note": "Astro renders HTML in the art-astro repo; this Lambda only mirrors pipeline assets.",
    }
