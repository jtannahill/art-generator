"""Watermark public PNG downloads for art.jt.

Public download buttons hit this Lambda Function URL. We fetch the clean
original from s3://bucket/weather/{run_id}/{slug}/preview-{size}.png,
composite a small "art.jt" watermark in the bottom-right, write the
watermarked PNG into s3://bucket/site/downloads/{run_id}/{slug}/...
(with Content-Disposition: attachment), and return a 302 redirect to
the CloudFront URL so the browser downloads the file directly.

Lambda response payload is capped at 6 MB (BUFFERED) and ~20 MB
(STREAMING); 8K PNGs are 80+ MB, so we never return the bytes through
Lambda - we redirect to CloudFront instead.

Print shop fulfillment continues to read the un-watermarked source path
directly via presigned URL (lambdas/print_shop/checkout.py).
"""

import io
import json
import os

import boto3
from PIL import Image, ImageDraw, ImageFont

BUCKET_NAME = os.environ["BUCKET_NAME"]
PUBLIC_HOST = os.environ.get("PUBLIC_HOST", "art.jamestannahill.com")
FONT_PATH = os.path.join(os.path.dirname(__file__), "fonts", "Fredoka.ttf")
SITE_URL = "art.jamestannahill.com"

ALLOWED_SIZES = {"4k", "8k"}
s3 = boto3.client("s3")


def handler(event, context):
    qs = event.get("queryStringParameters") or {}
    run_id = (qs.get("r") or "").strip()
    slug = (qs.get("s") or "").strip()
    size = (qs.get("z") or "4k").strip().lower()

    if not run_id or not slug or size not in ALLOWED_SIZES:
        return _error(400, "missing or invalid parameters")
    if not _safe(run_id) or not _safe(slug):
        return _error(400, "invalid characters in parameters")

    src_key = f"weather/{run_id}/{slug}/preview-{size}.png"
    # CloudFront origin path is /site, so the public URL drops the "site/" prefix.
    cache_key = f"site/downloads/{run_id}/{slug}/preview-{size}.png"
    public_path = f"/downloads/{run_id}/{slug}/preview-{size}.png"
    filename = f"{slug}-{run_id}-{size}-art.jt.png"

    if not _exists(cache_key):
        original = _try_get(src_key)
        if original is None:
            return _error(404, f"artwork not found: {src_key}")
        watermarked = _watermark(original, run_id)
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=cache_key,
            Body=watermarked,
            ContentType="image/png",
            ContentDisposition=f'attachment; filename="{filename}"',
            CacheControl="public, max-age=86400",
        )

    return {
        "statusCode": 302,
        "headers": {
            "Location": f"https://{PUBLIC_HOST}{public_path}",
            "Cache-Control": "no-store",
        },
        "body": "",
    }


def _safe(s):
    return all(c.isalnum() or c in "-_" for c in s)


def _exists(key):
    try:
        s3.head_object(Bucket=BUCKET_NAME, Key=key)
        return True
    except s3.exceptions.ClientError:
        return False


def _try_get(key):
    try:
        return s3.get_object(Bucket=BUCKET_NAME, Key=key)["Body"].read()
    except s3.exceptions.NoSuchKey:
        return None
    except s3.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("NoSuchKey", "404"):
            return None
        raise


def _watermark(png_bytes: bytes, run_id: str) -> bytes:
    """Tile 'art.jt + site + date' across the whole image at low opacity,
    rotated -22°. Hard to crop out, doesn't destroy the artwork view."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    w, h = img.size

    title = "art.jt"
    date = run_id[:10] if len(run_id) >= 10 else ""
    sub = f"{SITE_URL}  ·  {date}" if date else SITE_URL

    title_size = max(48, int(w * 0.030))
    sub_size = max(20, int(title_size * 0.42))
    title_font = ImageFont.truetype(FONT_PATH, title_size)
    sub_font = ImageFont.truetype(FONT_PATH, sub_size)

    # Build one stamp on a transparent canvas, rotate, then tile-paste
    # it across the full image.
    stamp_canvas_w = max(title_size * 8, int(w * 0.22))
    stamp_canvas_h = max(title_size * 3, int(title_size * 2.6))
    stamp = Image.new("RGBA", (stamp_canvas_w, stamp_canvas_h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(stamp)

    title_bbox = sd.textbbox((0, 0), title, font=title_font)
    title_w = title_bbox[2] - title_bbox[0]
    title_h = title_bbox[3] - title_bbox[1]
    sub_bbox = sd.textbbox((0, 0), sub, font=sub_font)
    sub_w = sub_bbox[2] - sub_bbox[0]

    cx_title = (stamp_canvas_w - title_w) // 2
    cy_title = (stamp_canvas_h - title_h - sub_size - 8) // 2
    cx_sub = (stamp_canvas_w - sub_w) // 2
    cy_sub = cy_title + title_h + max(4, sub_size // 3)

    # Soft shadow then white text for legibility on any background.
    shadow_off = max(2, title_size // 22)
    sd.text((cx_title + shadow_off, cy_title + shadow_off), title,
            font=title_font, fill=(0, 0, 0, 110))
    sd.text((cx_title, cy_title), title, font=title_font, fill=(255, 255, 255, 105))
    sd.text((cx_sub + shadow_off // 2, cy_sub + shadow_off // 2), sub,
            font=sub_font, fill=(0, 0, 0, 90))
    sd.text((cx_sub, cy_sub), sub, font=sub_font, fill=(255, 255, 255, 90))

    rotated = stamp.rotate(-22, expand=True, resample=Image.Resampling.BICUBIC)
    tw, th = rotated.size

    # Stagger every other row to break the grid.
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    step_x = int(tw * 0.95)
    step_y = int(th * 0.85)
    for ri, y in enumerate(range(-th, h + th, step_y)):
        x_offset = (ri % 2) * (step_x // 2)
        for x in range(-tw + x_offset, w + tw, step_x):
            overlay.paste(rotated, (x, y), rotated)

    composited = Image.alpha_composite(img, overlay).convert("RGB")
    buf = io.BytesIO()
    composited.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def _error(code: int, msg: str):
    return {
        "statusCode": code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": msg}),
    }
