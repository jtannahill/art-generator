"""Satellite Palette Lambda — extracts color palettes from satellite imagery."""

import io
import json
import os
from datetime import datetime

import boto3
from PIL import Image

BUCKET_NAME = os.environ.get("BUCKET_NAME", "")
TABLE_NAME = os.environ.get("TABLE_NAME", "")
BEDROCK_MODEL_ID = "us.anthropic.claude-sonnet-4-6-20250514"


def rgb_to_hex(rgb: tuple) -> str:
    """Convert (R, G, B) tuple to '#RRGGBB' hex string."""
    return "#{:02X}{:02X}{:02X}".format(rgb[0], rgb[1], rgb[2])


def extract_palette(img: Image.Image, n_colors: int = 6) -> list:
    """Extract dominant colors via median cut quantization.

    Returns list of (R, G, B) tuples sorted by prominence (most prominent first).
    """
    # Resize for speed
    small = img.copy()
    small.thumbnail((200, 200))
    small = small.convert("RGB")

    # Quantize using median cut
    quantized = small.quantize(colors=n_colors, method=Image.Quantize.MEDIANCUT)

    # Get palette and pixel counts
    palette_data = quantized.getpalette()
    histogram = quantized.histogram()

    # Determine actual number of colors in the palette
    actual_colors = min(n_colors, len(palette_data) // 3)

    # Build color-count pairs
    color_counts = []
    for i in range(actual_colors):
        count = histogram[i] if i < len(histogram) else 0
        r = palette_data[i * 3]
        g = palette_data[i * 3 + 1]
        b = palette_data[i * 3 + 2]
        if count > 0:
            color_counts.append(((r, g, b), count))

    # Sort by count descending (most prominent first)
    color_counts.sort(key=lambda x: x[1], reverse=True)

    return [color for color, _ in color_counts]


def generate_mood(location_name: str, hex_codes: list, tags: list) -> str:
    """Call Bedrock Claude Sonnet for a one-line mood/vibe description."""
    bedrock = boto3.client("bedrock-runtime")

    prompt = (
        f"You are an art director. Given a satellite image color palette from "
        f"{location_name} (tags: {', '.join(tags)}), describe the mood in one "
        f"short evocative sentence (under 15 words). Colors: {', '.join(hex_codes)}. "
        f"Reply with ONLY the mood sentence, no quotes."
    )

    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 60,
            "messages": [{"role": "user", "content": prompt}],
        }
    )

    response = bedrock.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=body,
        contentType="application/json",
    )

    result = json.loads(response["body"].read())
    return result["content"][0]["text"].strip()


def generate_swatch_png(colors: list, width: int = 800, height: int = 200) -> bytes:
    """Create a PNG with colored rectangle bands for each palette color.

    Each color gets an equal-width vertical band.
    """
    img = Image.new("RGB", (width, height))

    if not colors:
        return _image_to_bytes(img)

    band_width = width // len(colors)

    for i, color in enumerate(colors):
        x_start = i * band_width
        x_end = x_start + band_width if i < len(colors) - 1 else width
        for x in range(x_start, x_end):
            for y in range(height):
                img.putpixel((x, y), color)

    return _image_to_bytes(img)


def _image_to_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def get_season(month: int, lat: float) -> str:
    """Return season string based on month and hemisphere."""
    northern_seasons = {
        12: "winter", 1: "winter", 2: "winter",
        3: "spring", 4: "spring", 5: "spring",
        6: "summer", 7: "summer", 8: "summer",
        9: "autumn", 10: "autumn", 11: "autumn",
    }

    season = northern_seasons.get(month, "unknown")

    # Flip for southern hemisphere
    if lat < 0:
        flip = {
            "winter": "summer",
            "spring": "autumn",
            "summer": "winter",
            "autumn": "spring",
        }
        season = flip.get(season, season)

    return season


def handler(event, context):
    """Process a location's satellite image: extract palette, mood, swatch."""
    location = event
    s3_key = location["s3_key"]
    slug = location["slug"]
    date_str = location["date"]
    name = location["name"]
    tags = location.get("tags", [])
    lat = location.get("lat", 0)

    s3 = boto3.client("s3")

    # Download source image from S3
    response = s3.get_object(Bucket=BUCKET_NAME, Key=s3_key)
    image_bytes = response["Body"].read()
    img = Image.open(io.BytesIO(image_bytes))

    # Extract palette
    colors = extract_palette(img, n_colors=6)
    hex_codes = [rgb_to_hex(c) for c in colors]

    # Get mood from Bedrock
    try:
        mood = generate_mood(name, hex_codes, tags)
    except Exception as e:
        print(f"Bedrock mood generation failed: {e}")
        mood = "A landscape of shifting colors and textures"

    # Generate swatch PNG
    swatch_bytes = generate_swatch_png(colors)

    # Determine season
    month = int(date_str.split("-")[1])
    season = get_season(month, lat)

    # Build output prefix
    prefix = f"palettes/{date_str}/{slug}"

    # Upload swatch PNG
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=f"{prefix}/swatch.png",
        Body=swatch_bytes,
        ContentType="image/png",
    )

    # Upload source thumbnail
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=f"{prefix}/source-thumb.jpg",
        Body=image_bytes,
        ContentType="image/jpeg",
    )

    # Build metadata
    metadata = {
        "slug": slug,
        "name": name,
        "date": date_str,
        "season": season,
        "lat": lat,
        "lng": location.get("lng", 0),
        "tags": tags,
        "colors": hex_codes,
        "mood": mood,
        "cloud_cover": location.get("cloud_cover"),
        "swatch_key": f"{prefix}/swatch.png",
        "source_key": f"{prefix}/source-thumb.jpg",
    }

    # Upload metadata JSON
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=f"{prefix}/metadata.json",
        Body=json.dumps(metadata, indent=2),
        ContentType="application/json",
    )

    # Write to DynamoDB
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(TABLE_NAME)
    table.put_item(
        Item={
            "PK": f"PALETTE#{slug}",
            "SK": date_str,
            "colors": json.dumps(hex_codes),
            "mood": mood,
            "season": season,
            "tags": tags,
            "swatch_key": f"{prefix}/swatch.png",
            "source_key": f"{prefix}/source-thumb.jpg",
            "cloud_cover": str(location.get("cloud_cover", "")),
            "lat": str(lat),
            "lng": str(location.get("lng", 0)),
        }
    )

    print(f"Processed palette for {name}: {hex_codes}, mood: {mood}")

    return metadata
