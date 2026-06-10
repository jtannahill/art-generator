"""Remove duplicate palette entries from the historical archive.

For each slug, scans `palettes/{date}/{slug}/` entries newest→oldest, hashing each
`source-thumb.jpg` (which is the same JPEG bytes that satellite_palette wrote, identical
to the original `satellite/{date}/{slug}/source.jpg`). If today's hash equals the
previous-kept entry's hash, this date is a duplicate and gets:

  1. DynamoDB row deleted (PK=PALETTE#{slug}, SK={date})
  2. S3 keys deleted under `palettes/{date}/{slug}/` AND `satellite/{date}/{slug}/`

By default this is a DRY RUN. Pass --apply to actually delete.

After applying, run the site_rebuild Lambda once so the public site reflects the
deduped archive.
"""

import argparse
import hashlib
import re
import sys
from collections import defaultdict

import boto3

BUCKET = "art-generator-216890068001"
TABLE = "art-generator"

SOURCE_RE = re.compile(r"^palettes/(\d{4}-\d{2}-\d{2})/([^/]+)/source-thumb\.jpg$")

s3 = boto3.client("s3")
dynamodb = boto3.client("dynamodb")


def list_entries_per_slug() -> dict[str, list[str]]:
    """Return {slug: [date, date, ...]} sorted newest-first."""
    paginator = s3.get_paginator("list_objects_v2")
    by_slug: dict[str, list[str]] = defaultdict(list)
    for page in paginator.paginate(Bucket=BUCKET, Prefix="palettes/"):
        for obj in page.get("Contents", []):
            m = SOURCE_RE.match(obj["Key"])
            if m:
                by_slug[m.group(2)].append(m.group(1))
    return {slug: sorted(dates, reverse=True) for slug, dates in by_slug.items()}


def hash_source(date: str, slug: str) -> str:
    body = s3.get_object(
        Bucket=BUCKET, Key=f"palettes/{date}/{slug}/source-thumb.jpg"
    )["Body"].read()
    return hashlib.md5(body).hexdigest()


def delete_entry(slug: str, date: str, apply: bool) -> None:
    palette_prefix = f"palettes/{date}/{slug}/"
    satellite_prefix = f"satellite/{date}/{slug}/"
    keys_to_delete: list[str] = []

    for prefix in (palette_prefix, satellite_prefix):
        resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
        for obj in resp.get("Contents", []):
            keys_to_delete.append(obj["Key"])

    print(
        f"  DELETE {slug} {date}: "
        f"{len(keys_to_delete)} S3 keys + DynamoDB row"
        f"{' (DRY RUN)' if not apply else ''}"
    )

    if not apply:
        return

    if keys_to_delete:
        s3.delete_objects(
            Bucket=BUCKET,
            Delete={"Objects": [{"Key": k} for k in keys_to_delete], "Quiet": True},
        )
    dynamodb.delete_item(
        TableName=TABLE,
        Key={"PK": {"S": f"PALETTE#{slug}"}, "SK": {"S": date}},
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    parser.add_argument("--slug", help="restrict to a single slug (e.g. uluru)")
    args = parser.parse_args()

    entries = list_entries_per_slug()
    if args.slug:
        entries = {args.slug: entries.get(args.slug, [])}

    total_kept = total_dropped = 0
    for slug in sorted(entries):
        dates = entries[slug]
        if not dates:
            continue
        print(f"\n=== {slug} ({len(dates)} entries) ===")
        last_kept_hash: str | None = None
        for date in dates:
            try:
                h = hash_source(date, slug)
            except Exception as e:
                print(f"  SKIP {slug} {date}: read failed: {e}")
                continue
            if h == last_kept_hash:
                delete_entry(slug, date, args.apply)
                total_dropped += 1
            else:
                print(f"  KEEP {slug} {date} (md5={h})")
                last_kept_hash = h
                total_kept += 1

    mode = "DELETED" if args.apply else "WOULD DELETE"
    print(f"\nSummary: kept {total_kept}, {mode} {total_dropped}")
    if not args.apply:
        print("Re-run with --apply to actually delete.")


if __name__ == "__main__":
    main()
