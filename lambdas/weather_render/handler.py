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
MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"


def handler(event, context):
    """Receives a single region dict, generates SVG via Bedrock,
    renders PNG, saves to S3 + DynamoDB."""
    region = event
    date = region.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    run_id = region.get("run_id", date)
    slug = region["slug"]
    artist = region.get("artist", "sam_francis")

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

    # Render PNGs (skip if Cairo not available)
    png_2048 = None
    png_4k = None
    try:
        png_2048 = render_png(svg_text, 2048)
        png_4k = render_png(svg_text, 4096)
    except Exception as e:
        print(f"PNG rendering skipped (Cairo not available): {e}")

    # Build S3 paths — use run_id for unique storage per generation
    prefix = f"weather/{run_id}/{slug}"

    # Save to S3
    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=f"{prefix}/artwork.svg",
        Body=svg_text.encode("utf-8"),
        ContentType="image/svg+xml",
    )
    if png_2048:
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=f"{prefix}/preview-2048.png",
            Body=png_2048,
            ContentType="image/png",
        )
    if png_4k:
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=f"{prefix}/preview-4k.png",
            Body=png_4k,
            ContentType="image/png",
        )

    metadata = {
        "date": date,
        "run_id": run_id,
        "slug": slug,
        "artist": artist,
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
        "PK": f"WEATHER#{run_id}",
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
    """Gets explanation text before the SVG, cleaned of markdown artifacts."""
    match = re.search(r"<svg", text, re.IGNORECASE)
    if match:
        before = text[: match.start()].strip()
    else:
        before = text.strip() if text.strip() else None

    if not before:
        return None

    # Clean markdown artifacts
    clean = before
    clean = re.sub(r"\*\*[^*]*\*\*:?\s*", "", clean)  # Remove **bold** labels
    clean = re.sub(r"```\w*\s*", "", clean)  # Remove code fences
    clean = re.sub(r"`", "", clean)  # Remove backticks
    clean = re.sub(r"#{1,3}\s+", "", clean)  # Remove markdown headers
    clean = re.sub(r"\*", "", clean)  # Remove remaining asterisks
    clean = re.sub(r"\n+", " ", clean)  # Collapse all newlines to spaces
    clean = re.sub(r"\s{2,}", " ", clean)  # Collapse multiple spaces
    clean = clean.strip().rstrip("."  ) + "."  # Ensure ends with period
    clean = clean.replace("..", ".")  # Fix double period

    return clean if clean else None


ARTIST_PROMPTS = {
    "sam_francis": "Sam Francis — bold saturated color fields, energetic splashes and splatters, luminous negative space, lyrical abstraction. Think of his late works where color pools at the edges leaving breathing room in the center, or his earlier explosive compositions where color bursts outward.",
    "gerhard_richter": "Gerhard Richter — his abstract paintings with sweeping squeegee strokes that layer and reveal color beneath. Bold horizontal and vertical drags of paint creating depth through concealment and revelation. Rich, complex color layering.",
    "hilma_af_klint": "Hilma af Klint — mystical geometric abstraction with biomorphic forms. Soft pastels alongside deep saturated colors. Spirals, circles within circles, botanical symmetry. Sacred geometry meets organic growth.",
    "wassily_kandinsky": "Wassily Kandinsky — dynamic geometric compositions with circles, triangles, and lines in musical harmony. Bold primary colors against muted backgrounds. Shapes that suggest movement and rhythm, like visual music.",
    "helen_frankenthaler": "Helen Frankenthaler — stain painting technique where color soaks and bleeds into the canvas. Transparent washes of color that pool and overlap. Ethereal, atmospheric fields of luminous color with soft edges.",
    "piet_mondrian": "Piet Mondrian — neoplasticism with primary colors (red, blue, yellow) in rectangular fields divided by bold black lines. Asymmetric balance, white space as an active element. Pure geometric abstraction.",
    "yayoi_kusama": "Yayoi Kusama — obsessive repetition of dots and circles. Infinity nets, polka dots in vivid colors against contrasting backgrounds. Patterns that suggest infinity and cosmic expansion.",
    "mark_rothko": "Mark Rothko — luminous color field paintings with soft-edged rectangular forms floating on the canvas. Two or three horizontal bands of deeply saturated color that seem to glow from within. Contemplative, immersive, emotional.",
    "bridget_riley": "Bridget Riley — op art with precise geometric patterns that create optical illusions of movement and vibration. Undulating lines, chevrons, and curves in carefully calibrated color relationships.",
    "kazimir_malevich": "Kazimir Malevich — suprematist compositions with basic geometric forms (squares, circles, crosses, rectangles) floating in white space. Bold, flat colors. Dynamic diagonal arrangements suggesting weightlessness and cosmic space.",
    "lesley_tannahill": "Lesley Tannahill — contemporary mixed-media artist working across painting, drawing, and printmaking. Expressive mark-making with layered textures, intuitive color relationships, and gestural energy. Works that balance controlled composition with spontaneous, raw expression. Rich surface quality with visible process.",
}


def build_art_prompt(region):
    """Builds prompt from atmospheric data including humidity and precipitation."""
    humidity_line = ""
    if region.get("humidity") is not None:
        humidity_line = f"\n- Relative humidity: {region['humidity']}%"

    precip_line = ""
    if region.get("precipitation") is not None:
        precip_line = f"\n- Precipitation: {region['precipitation']} kg/m^2"

    artist_key = region.get("artist", "sam_francis")
    artist_desc = ARTIST_PROMPTS.get(artist_key, ARTIST_PROMPTS["sam_francis"])

    return f"""You are a generative artist creating abstract SVG artwork inspired by real-time
atmospheric data. Create a single, self-contained SVG artwork (viewBox="0 0 2048 2048")
that visually interprets the following weather conditions.

Location: {region['slug']} ({region['lat']}, {region['lng']})
Atmospheric conditions:
- Sea-level pressure: {region['pressure']} Pa (gradient: {region['pressure_gradient']} Pa/cell)
- Wind: {region['wind_speed']} m/s from {region['wind_direction']} degrees
- Temperature: {region['temp']} K (anomaly from zonal mean: {region['temp_anomaly']} K){humidity_line}{precip_line}
- Visual interest score: {region['score']}

Artistic direction:
- Draw inspiration from {artist_desc}
- Let the atmospheric data drive the composition: high wind = dynamic energy and movement;
  deep low pressure = density and weight; temperature extremes = vivid, saturated hues;
  calm conditions = open space and restraint.
- Each piece should feel unique — vary your approach while staying true to the artist's aesthetic.

Technical requirements:
1. First, write 2-3 sentences explaining your artistic interpretation. Begin by naming the
   real-world geography of this location (e.g., "Over the North Atlantic south of Iceland..."
   or "Above the Saharan coast near Mauritania..."). Then describe how the atmospheric
   conditions shaped your visual choices. Write in plain prose — no markdown, no bold, no
   headers, no code fences.
2. Then output the complete SVG on its own line starting with <svg
3. Use gradients, filters, organic shapes, and layered transparency — no text elements
4. The SVG must be valid XML and self-contained (no external references)
5. Use the viewBox="0 0 2048 2048" attribute on the root <svg> element
6. Use at least 20-30 shape elements for visual richness"""


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
