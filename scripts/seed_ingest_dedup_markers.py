"""Seed satellite/_latest/{slug}.md5 markers from the most recent existing source.jpg per slug.

Run once after deploying the dedup change in lambdas/satellite_ingest/handler.py so the
first post-deploy run can already detect "no new imagery" instead of writing one more
guaranteed duplicate.
"""

import hashlib
import re
from collections import defaultdict

import boto3

BUCKET = "art-generator-216890068001"
KEY_RE = re.compile(r"^satellite/(\d{4}-\d{2}-\d{2})/([^/]+)/source\.jpg$")

s3 = boto3.client("s3")


def latest_source_per_slug() -> dict:
    paginator = s3.get_paginator("list_objects_v2")
    latest: dict[str, tuple[str, str]] = {}
    for page in paginator.paginate(Bucket=BUCKET, Prefix="satellite/"):
        for obj in page.get("Contents", []):
            m = KEY_RE.match(obj["Key"])
            if not m:
                continue
            date, slug = m.group(1), m.group(2)
            cur = latest.get(slug)
            if cur is None or date > cur[0]:
                latest[slug] = (date, obj["Key"])
    return latest


def main() -> None:
    latest = latest_source_per_slug()
    print(f"Found latest source.jpg for {len(latest)} slugs")
    for slug in sorted(latest):
        date, key = latest[slug]
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        md5_hex = hashlib.md5(body).hexdigest()
        s3.put_object(
            Bucket=BUCKET,
            Key=f"satellite/_latest/{slug}.md5",
            Body=md5_hex.encode(),
            ContentType="text/plain",
        )
        print(f"  {slug}: seeded from {date} (md5={md5_hex})")


if __name__ == "__main__":
    main()
