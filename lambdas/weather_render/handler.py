"""Weather Render Lambda — generates SVG artwork from atmospheric data via Bedrock,
renders PNG previews, saves to S3 and DynamoDB."""

import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import boto3

try:
    import cairosvg
except OSError:
    cairosvg = None  # Cairo C library not available (local dev/testing)

BUCKET_NAME = os.environ.get("BUCKET_NAME", "art-generator-216890068001")
TABLE_NAME = os.environ.get("TABLE_NAME", "art-generator")
MODEL_ID = "us.anthropic.claude-sonnet-4-6-20250514"


def handler(event, context):
    """Receives a single region dict, generates SVG via Bedrock,
    renders PNG, saves to S3 + DynamoDB."""
    region = event
    date = region.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    slug = region["slug"]

    prompt = build_art_prompt(region)
    svg_text = None
    rationale = None
    last_error = None

    # Try up to 3 times (initial + 2 retries)
    for attempt in range(3):
        if attempt == 0:
            response_text = invoke_bedrock(prompt)
        else:
            retry_prompt = build_retry_prompt(prompt, svg_text or "", last_error)
            response_text = invoke_bedrock(retry_prompt)

        svg_text = extract_svg(response_text)
        if svg_text is None:
            last_error = "No SVG found in response"
            continue

        rationale = extract_rationale(response_text)
        valid, error = validate_svg(svg_text)
        if valid:
            break
        last_error = error
    else:
        raise RuntimeError(f"Failed to generate valid SVG after 3 attempts: {last_error}")

    # Render PNGs
    png_2048 = render_png(svg_text, 2048)
    png_4k = render_png(svg_text, 4096)

    # Build S3 paths
    prefix = f"weather/{date}/{slug}"

    # Save to S3
    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=f"{prefix}/artwork.svg",
        Body=svg_text.encode("utf-8"),
        ContentType="image/svg+xml",
    )
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=f"{prefix}/preview-2048.png",
        Body=png_2048,
        ContentType="image/png",
    )
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=f"{prefix}/preview-4k.png",
        Body=png_4k,
        ContentType="image/png",
    )

    metadata = {
        "date": date,
        "slug": slug,
        "lat": region["lat"],
        "lng": region["lng"],
        "score": region["score"],
        "pressure": region["pressure"],
        "pressure_gradient": region["pressure_gradient"],
        "wind_speed": region["wind_speed"],
        "wind_direction": region["wind_direction"],
        "temp": region["temp"],
        "temp_anomaly": region["temp_anomaly"],
        "humidity": region.get("humidity"),
        "precipitation": region.get("precipitation"),
        "rationale": rationale,
        "s3_prefix": prefix,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=f"{prefix}/metadata.json",
        Body=json.dumps(metadata, indent=2).encode("utf-8"),
        ContentType="application/json",
    )

    # Write to DynamoDB
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(TABLE_NAME)
    item = {
        "PK": f"WEATHER#{date}",
        "SK": slug,
        **{k: _convert_dynamo(v) for k, v in metadata.items()},
    }
    table.put_item(Item=item)

    return {
        "slug": slug,
        "date": date,
        "s3_prefix": prefix,
        "score": region["score"],
    }


def _convert_dynamo(val):
    """Convert floats to Decimal-safe strings for DynamoDB."""
    if isinstance(val, float):
        from decimal import Decimal
        return Decimal(str(val))
    return val


def invoke_bedrock(prompt):
    """Calls Bedrock Claude Sonnet, returns text response."""
    client = boto3.client("bedrock-runtime", region_name="us-east-1")
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": prompt}],
    })
    response = client.invoke_model(
        modelId=MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=body,
    )
    result = json.loads(response["body"].read())
    return result["content"][0]["text"]


def validate_svg(svg_text):
    """XML parse + check root is <svg>. Returns (is_valid, error_message)."""
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as e:
        return False, f"XML parse error: {e}"

    # Check root tag — strip namespace if present
    tag = root.tag
    if "}" in tag:
        tag = tag.split("}", 1)[1]

    if tag.lower() != "svg":
        return False, f"Root element is <{tag}>, expected <svg>"

    return True, None


def extract_svg(text):
    """Regex to find <svg...>...</svg> in Bedrock response."""
    match = re.search(r"(<svg[\s\S]*?</svg>)", text, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def extract_rationale(text):
    """Gets explanation text before the SVG."""
    match = re.search(r"<svg", text, re.IGNORECASE)
    if match:
        before = text[: match.start()].strip()
        return before if before else None
    return text.strip() if text.strip() else None


def build_art_prompt(region):
    """Builds prompt from atmospheric data including humidity and precipitation."""
    humidity_line = ""
    if region.get("humidity") is not None:
        humidity_line = f"\n- Relative humidity: {region['humidity']}%"

    precip_line = ""
    if region.get("precipitation") is not None:
        precip_line = f"\n- Precipitation: {region['precipitation']} kg/m^2"

    return f"""You are a generative artist creating abstract SVG artwork inspired by real-time
atmospheric data. Create a single, self-contained SVG artwork (viewBox="0 0 2048 2048")
that visually interprets the following weather conditions.

Location: {region['slug']} ({region['lat']}, {region['lng']})
Atmospheric conditions:
- Sea-level pressure: {region['pressure']} Pa (gradient: {region['pressure_gradient']} Pa/cell)
- Wind: {region['wind_speed']} m/s from {region['wind_direction']} degrees
- Temperature: {region['temp']} K (anomaly from zonal mean: {region['temp_anomaly']} K){humidity_line}{precip_line}
- Visual interest score: {region['score']}

Guidelines:
1. First, briefly explain your artistic interpretation (2-3 sentences)
2. Then output the complete SVG
3. Use gradients, patterns, and organic shapes — no text elements
4. Color palette should reflect the atmospheric mood (e.g., warm for high temp anomaly,
   cool blues for high pressure, dynamic reds/oranges for high wind)
5. The SVG must be valid XML and self-contained (no external references)
6. Use the viewBox="0 0 2048 2048" attribute on the root <svg> element"""


def build_retry_prompt(original_prompt, bad_svg, error):
    """Retry prompt with error feedback."""
    return f"""Your previous SVG output had an error. Please fix it and try again.

Error: {error}

Previous (broken) SVG:
```
{bad_svg[:2000]}
```

Original request:
{original_prompt}

Please output a corrected, valid SVG. Make sure it is well-formed XML with <svg> as the root element."""


def render_png(svg_text, width):
    """Uses cairosvg.svg2png to render SVG to PNG bytes at the given width."""
    return cairosvg.svg2png(
        bytestring=svg_text.encode("utf-8"),
        output_width=width,
        output_height=width,
    )
