"""Weather Render Lambda — generates artwork via Flux 1.1 Pro (Replicate)
with parallel Claude SVG for vector download. Saves to S3 and DynamoDB."""

import json
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import boto3

try:
    import cairosvg
except OSError:
    cairosvg = None

BUCKET_NAME = os.environ.get("BUCKET_NAME", "art-generator-216890068001")
TABLE_NAME = os.environ.get("TABLE_NAME", "art-generator")
REPLICATE_TOKEN = os.environ.get("REPLICATE_API_TOKEN", "")
MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"


def handler(event, context):
    """Receives a single region dict, generates Flux raster + Claude SVG in parallel."""
    region = event
    date = region.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    run_id = region.get("run_id", date)
    slug = region["slug"]
    artist = region.get("artist", "sam_francis")

    # Build prompts
    flux_prompt = build_flux_prompt(region)
    svg_prompt, canvas_format = build_svg_prompt(region)

    # Run Flux and Claude SVG generation in parallel
    flux_png = None
    svg_text = None
    rationale = None

    with ThreadPoolExecutor(max_workers=2) as executor:
        flux_future = executor.submit(generate_flux, flux_prompt, region)
        svg_future = executor.submit(generate_svg, svg_prompt)

        # Flux result — primary artwork
        try:
            flux_png = flux_future.result(timeout=240)
            print(f"Flux generation succeeded: {len(flux_png)} bytes")
        except Exception as e:
            print(f"Flux generation failed: {e}")

        # Claude SVG result — vector download
        try:
            svg_text, rationale = svg_future.result(timeout=120)
            print(f"SVG generation succeeded: {len(svg_text)} chars")
        except Exception as e:
            print(f"SVG generation failed: {e}")

    # Must have at least one output
    if not flux_png and not svg_text:
        raise RuntimeError("Both Flux and SVG generation failed")

    # If Flux failed, fall back to rendering PNG from SVG
    png_2048 = None
    png_4k = None
    png_8k = None
    if flux_png:
        png_2048 = flux_png  # Flux output is the primary preview (~1024px)
        # Two-pass upscale: Real-ESRGAN 4x (1024→4096) then Clarity 2x (4096→8192)
        try:
            print("Pass 1: Real-ESRGAN 4x (1024→4096)...")
            png_4k = upscale_image(flux_png, scale=4)
            print(f"Pass 1 complete: {len(png_4k)/1024/1024:.1f} MB")
            try:
                print("Pass 2: Clarity Upscaler 2x (4096→8192)...")
                png_8k = upscale_clarity(png_4k, scale=2)
                print(f"Pass 2 complete: {len(png_8k)/1024/1024:.1f} MB (8K print-ready)")
            except Exception as e:
                print(f"8K upscale failed (non-fatal, 4K still saved): {e}")
        except Exception as e:
            print(f"Upscale failed, using Flux original as fallback: {e}")
            png_4k = flux_png
    elif svg_text and cairosvg:
        try:
            png_2048 = render_png(svg_text, 2048)
            png_4k = render_png(svg_text, 4096)
            png_8k = render_png(svg_text, 8192)
        except Exception as e:
            print(f"PNG rendering from SVG fallback failed: {e}")

    # If no rationale from SVG generation, generate one separately
    if not rationale and flux_png:
        try:
            rationale = generate_rationale(region)
        except Exception as e:
            print(f"Rationale generation failed: {e}")

    # Build S3 paths
    prefix = f"weather/{run_id}/{slug}"

    # Save to S3
    s3 = boto3.client("s3")
    if svg_text:
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
    if png_8k:
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=f"{prefix}/preview-8k.png",
            Body=png_8k,
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
        "canvas_format": canvas_format,
        "renderer": "flux-1.1-pro" if flux_png else "claude-svg",
        "has_svg": bool(svg_text),
        "has_8k": bool(png_8k),
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
        "run_id": run_id,
        "slug": slug,
        "date": date,
        "s3_prefix": prefix,
        "score": region["score"],
    }


# ---------------------------------------------------------------------------
# Flux 1.1 Pro (Replicate)
# ---------------------------------------------------------------------------

def generate_flux(prompt, region):
    """Submit to Replicate Flux 1.1 Pro and poll for result. Returns PNG bytes."""
    import random
    random.seed(hash(f"{region['slug']}{region.get('date', '')}{region.get('artist', '')}"))
    formats = [
        (1024, 1024),   # square
        (1344, 768),    # wide landscape
        (768, 1344),    # tall portrait
        (1152, 896),    # landscape
        (896, 1152),    # portrait
    ]
    width, height = random.choice(formats)

    payload = json.dumps({
        "input": {
            "prompt": prompt,
            "width": width,
            "height": height,
            "prompt_upsampling": True,
            "output_format": "png",
        }
    }).encode()

    req = urllib.request.Request(
        "https://api.replicate.com/v1/models/black-forest-labs/flux-1.1-pro/predictions",
        data=payload,
        headers={
            "Authorization": f"Bearer {REPLICATE_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    # Submit with retry on 429
    result = None
    for attempt in range(3):
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            result = json.loads(resp.read())
            break
        except urllib.error.HTTPError as e:
            body = e.read()
            if e.code == 429:
                wait = (attempt + 1) * 15
                print(f"Flux rate limited (429), retrying in {wait}s (attempt {attempt+1}/3)")
                time.sleep(wait)
                # Rebuild request (urlopen consumes it)
                req = urllib.request.Request(
                    "https://api.replicate.com/v1/models/black-forest-labs/flux-1.1-pro/predictions",
                    data=payload,
                    headers={"Authorization": f"Bearer {REPLICATE_TOKEN}", "Content-Type": "application/json"},
                    method="POST",
                )
                continue
            # Replicate returns 402 but still creates the prediction
            result = json.loads(body)
            if not result.get("id"):
                raise
            break
    if not result or not result.get("id"):
        raise RuntimeError("Flux submission failed after 3 retries")
    pred_url = result["urls"]["get"]
    print(f"Flux submitted: {result['id']}")

    # Poll for completion (up to 4 minutes)
    for _ in range(48):
        time.sleep(5)
        status_req = urllib.request.Request(
            pred_url,
            headers={"Authorization": f"Bearer {REPLICATE_TOKEN}"},
        )
        status = json.loads(urllib.request.urlopen(status_req, timeout=30).read())
        if status["status"] == "succeeded":
            img_url = status["output"]
            if isinstance(img_url, list):
                img_url = img_url[0]
            img_data = urllib.request.urlopen(img_url, timeout=60).read()
            return img_data
        elif status["status"] == "failed":
            raise RuntimeError(f"Flux failed: {status.get('error', 'unknown')}")

    raise RuntimeError("Flux timed out after 4 minutes")


def upscale_image(png_bytes, scale=4):
    """Upscale PNG via Replicate Real-ESRGAN. Returns upscaled PNG bytes."""
    import base64
    # Convert to data URI for Replicate
    b64 = base64.b64encode(png_bytes).decode()
    data_uri = f"data:image/png;base64,{b64}"

    payload = json.dumps({
        "input": {
            "image": data_uri,
            "scale": scale,
            "face_enhance": False,
        }
    }).encode()

    req = urllib.request.Request(
        "https://api.replicate.com/v1/models/nightmareai/real-esrgan/predictions",
        data=payload,
        headers={
            "Authorization": f"Bearer {REPLICATE_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    # Submit with retry on 429
    result = None
    for attempt in range(3):
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            result = json.loads(resp.read())
            break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = (attempt + 1) * 15
                print(f"Upscale rate limited (429), retrying in {wait}s")
                time.sleep(wait)
                req = urllib.request.Request(
                    "https://api.replicate.com/v1/models/nightmareai/real-esrgan/predictions",
                    data=payload,
                    headers={"Authorization": f"Bearer {REPLICATE_TOKEN}", "Content-Type": "application/json"},
                    method="POST",
                )
                continue
            raise
    if not result or not result.get("id"):
        raise RuntimeError("Upscale submission failed")

    pred_url = result["urls"]["get"]
    print(f"Upscale submitted: {result['id']} (scale={scale}x)")

    # Poll for completion (up to 5 minutes)
    for _ in range(60):
        time.sleep(5)
        status_req = urllib.request.Request(
            pred_url,
            headers={"Authorization": f"Bearer {REPLICATE_TOKEN}"},
        )
        status = json.loads(urllib.request.urlopen(status_req, timeout=30).read())
        if status["status"] == "succeeded":
            img_url = status["output"]
            if isinstance(img_url, list):
                img_url = img_url[0]
            return urllib.request.urlopen(img_url, timeout=60).read()
        elif status["status"] == "failed":
            raise RuntimeError(f"Upscale failed: {status.get('error', 'unknown')}")

    raise RuntimeError("Upscale timed out after 5 minutes")


def upscale_clarity(png_bytes, scale=2):
    """Upscale PNG via Replicate Clarity Upscaler (diffusion-based, handles large inputs).
    Best for 4096→8192 pass. Returns upscaled PNG bytes."""
    import base64
    b64 = base64.b64encode(png_bytes).decode()
    data_uri = f"data:image/png;base64,{b64}"

    payload = json.dumps({
        "version": "dfad41707589d68ecdccd1dfa600d55a208f9310748e44bfe35b4a6291453d5e",
        "input": {
            "image": data_uri,
            "scale_factor": scale,
            "resemblance": 0.85,
            "creativity": 0.2,
            "prompt": "abstract expressionist painting, museum quality fine art, high detail",
            "negative_prompt": "text, words, letters, blurry, low quality, artifacts, watermark",
        }
    }).encode()

    req = urllib.request.Request(
        "https://api.replicate.com/v1/predictions",
        data=payload,
        headers={
            "Authorization": f"Bearer {REPLICATE_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    # Submit with retry on 429
    result = None
    for attempt in range(3):
        try:
            resp = urllib.request.urlopen(req, timeout=60)
            result = json.loads(resp.read())
            break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = (attempt + 1) * 15
                print(f"Clarity rate limited (429), retrying in {wait}s")
                time.sleep(wait)
                req = urllib.request.Request(
                    "https://api.replicate.com/v1/predictions",
                    data=payload,
                    headers={"Authorization": f"Bearer {REPLICATE_TOKEN}", "Content-Type": "application/json"},
                    method="POST",
                )
                continue
            raise
    if not result or not result.get("id"):
        raise RuntimeError("Clarity submission failed")

    pred_url = result["urls"]["get"]
    print(f"Clarity submitted: {result['id']} (scale={scale}x)")

    # Poll for completion (up to 10 minutes — Clarity is slower than Real-ESRGAN)
    for _ in range(120):
        time.sleep(5)
        status_req = urllib.request.Request(
            pred_url,
            headers={"Authorization": f"Bearer {REPLICATE_TOKEN}"},
        )
        status = json.loads(urllib.request.urlopen(status_req, timeout=30).read())
        if status["status"] == "succeeded":
            img_url = status["output"]
            if isinstance(img_url, list):
                img_url = img_url[0]
            return urllib.request.urlopen(img_url, timeout=120).read()
        elif status["status"] == "failed":
            raise RuntimeError(f"Clarity failed: {status.get('error', 'unknown')}")

    raise RuntimeError("Clarity timed out after 10 minutes")


def build_flux_prompt(region):
    """Build a text-to-image prompt for Flux 1.1 Pro with artist-specific weather mapping."""
    artist_key = region.get("artist", "sam_francis")
    profile = ARTIST_PROFILES.get(artist_key, ARTIST_PROFILES["sam_francis"])

    humidity_line = ""
    if region.get("humidity") is not None:
        humidity_line = f", humidity {region['humidity']}%"

    precip_line = ""
    if region.get("precipitation") is not None and region["precipitation"] > 0:
        precip_line = f", precipitation {region['precipitation']} mm"

    return (
        f"Abstract artwork in the style of {profile['description']}. "
        f"Inspired by atmospheric conditions over {region['slug'].replace('-', ' ')}: "
        f"wind {region['wind_speed']} m/s, pressure {region['pressure']} hPa, "
        f"temperature {region['temp']}°C with {region['temp_anomaly']}°C anomaly"
        f"{humidity_line}{precip_line}. "
        f"ARTIST-SPECIFIC WEATHER INTERPRETATION: {profile['weather_mapping']} "
        f"PALETTE: {profile['palette_guidance']} "
        f"Museum-quality abstract piece. {profile['negative']} "
        f"No text, no words, no letters, no signatures."
    )


# ---------------------------------------------------------------------------
# Claude SVG (Bedrock) — vector download option
# ---------------------------------------------------------------------------

def generate_svg(prompt):
    """Generate SVG via Bedrock Claude. Returns (svg_text, rationale)."""
    svg_text = None
    rationale = None
    last_error = None

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
            return svg_text, rationale
        last_error = error

    raise RuntimeError(f"Failed to generate valid SVG after 3 attempts: {last_error}")


def generate_rationale(region):
    """Generate just the artistic rationale text via Bedrock (no SVG needed)."""
    artist_key = region.get("artist", "sam_francis")
    artist_desc = ARTIST_PROMPTS.get(artist_key, ARTIST_PROMPTS["sam_francis"])

    prompt = (
        f"Write 2-3 sentences explaining an artistic interpretation of weather data "
        f"for an abstract artwork in the style of {artist_desc}. "
        f"Begin by naming the real-world geography at coordinates "
        f"({region['lat']}, {region['lng']}). "
        f"Atmospheric conditions: pressure {region['pressure']} hPa, "
        f"wind {region['wind_speed']} m/s, temp {region['temp']}°C "
        f"(anomaly {region['temp_anomaly']}°C). "
        f"Write in plain prose — no markdown, no bold, no headers."
    )
    client = boto3.client("bedrock-runtime", region_name="us-east-1")
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 300,
        "messages": [{"role": "user", "content": prompt}],
    })
    response = client.invoke_model(
        modelId=MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=body,
    )
    result = json.loads(response["body"].read())
    text = result["content"][0]["text"].strip()
    # Clean any markdown artifacts
    text = re.sub(r"\*\*[^*]*\*\*:?\s*", "", text)
    text = re.sub(r"\n+", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

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

    clean = before
    clean = re.sub(r"\*\*[^*]*\*\*:?\s*", "", clean)
    clean = re.sub(r"```\w*\s*", "", clean)
    clean = re.sub(r"`", "", clean)
    clean = re.sub(r"#{1,3}\s+", "", clean)
    clean = re.sub(r"\*", "", clean)
    clean = re.sub(r"\n+", " ", clean)
    clean = re.sub(r"\s{2,}", " ", clean)
    clean = clean.strip().rstrip(".") + "."
    clean = clean.replace("..", ".")
    return clean if clean else None


ARTIST_PROFILES = {
    "sam_francis": {
        "description": "Sam Francis — bold saturated color fields, energetic splashes and splatters, luminous negative space, lyrical abstraction. Think of his late works where color pools at the edges leaving breathing room in the center, or his earlier explosive compositions where color bursts outward.",
        "weather_mapping": "Wind drives the outward explosion of color — strong wind = splatters reaching further, more chaotic energy. Pressure maps to density of color pools — low pressure = heavy, saturated accumulation at edges. Temperature drives saturation — heat = vivid primaries (cadmium red, cobalt blue), cold = muted earth tones with icy accents. Calm conditions = expansive negative white space dominating the center.",
        "negative": "NOT geometric grids, NOT precise lines, NOT symmetrical, NOT figurative, NOT photorealistic. No controlled brushwork — this should feel spontaneous and gestural.",
        "palette_guidance": "Pure saturated hues: cadmium yellow, ultramarine blue, cadmium red, viridian green. Never muddy. White space is luminous, not blank.",
    },
    "gerhard_richter": {
        "description": "Gerhard Richter — his abstract paintings with sweeping squeegee strokes that layer and reveal color beneath. Bold horizontal and vertical drags of paint creating depth through concealment and revelation. Rich, complex color layering.",
        "weather_mapping": "Wind direction drives the squeegee drag direction — SW wind = diagonal drags from lower-left. Wind speed controls stroke length — strong wind = long sweeping drags across the full canvas. Pressure gradient maps to how many layers are revealed — steep gradient = more layers visible, more complexity. Temperature anomaly controls the underlying color warmth being revealed.",
        "negative": "NOT dotted, NOT circular, NOT geometric shapes, NOT clean edges, NOT minimal. No discrete elements — everything should feel like continuous dragged paint. NOT illustrative.",
        "palette_guidance": "Rich layered colors: deep reds, greys, greens, yellows showing through. Each layer a different temperature. Cool over warm or warm over cool.",
    },
    "hilma_af_klint": {
        "description": "Hilma af Klint — mystical geometric abstraction with biomorphic forms. Soft pastels alongside deep saturated colors. Spirals, circles within circles, botanical symmetry. Sacred geometry meets organic growth.",
        "weather_mapping": "Pressure maps to the scale of sacred geometry — high pressure = large encompassing circles and spirals. Wind creates the spiral direction and tightness — strong wind = tight spirals, calm = open concentric rings. Temperature drives the pastel-to-saturated ratio — warm = deep saturated jewel tones, cold = soft ethereal pastels. Humidity controls the organic/biomorphic quality — high humidity = more flowing botanical forms.",
        "negative": "NOT chaotic, NOT aggressive, NOT angular sharp edges, NOT monochrome, NOT photorealistic. No violence in the composition — this should feel spiritual, contemplative, and ordered by unseen forces.",
        "palette_guidance": "Pastels (rose, powder blue, sage) alongside jewel tones (deep ultramarine, gold, emerald). Always include gold or ochre as a sacred element.",
    },
    "wassily_kandinsky": {
        "description": "Wassily Kandinsky — dynamic geometric compositions with circles, triangles, and lines in musical harmony. Bold primary colors against muted backgrounds. Shapes that suggest movement and rhythm, like visual music.",
        "weather_mapping": "Wind speed maps to the tempo of the visual music — strong wind = staccato, many small dynamic shapes. Calm = largo, fewer large floating forms. Pressure drives the visual weight — low pressure = heavy shapes anchored to the bottom, high pressure = buoyant shapes rising. Temperature maps to pitch — heat = high-frequency sharp triangles and thin lines, cold = deep bass circles and thick forms. Wind direction sets the dominant diagonal of movement.",
        "negative": "NOT organic, NOT blurry, NOT soft-edged, NOT photorealistic, NOT figurative. No naturalistic forms. Shapes must be clearly geometric — circles, triangles, lines, rectangles. NOT a landscape.",
        "palette_guidance": "Bold primaries (red, blue, yellow) and black against muted beige/grey backgrounds. Occasional violet or green accents. Black lines as musical notation.",
    },
    "helen_frankenthaler": {
        "description": "Helen Frankenthaler — stain painting technique where color soaks and bleeds into the canvas. Transparent washes of color that pool and overlap. Ethereal, atmospheric fields of luminous color with soft edges.",
        "weather_mapping": "Precipitation directly maps to how much color pools and bleeds — rain = deep saturated pools with spreading edges. Humidity controls transparency — high humidity = more translucent layering, dry = more opaque patches. Wind creates the spread direction of color stains. Temperature drives warmth — heat = warm ochres, pinks, oranges soaking outward; cold = cool blues and greens with sharp pooling edges. Pressure gradient = how the color fields tilt and flow across the canvas.",
        "negative": "NOT geometric, NOT hard-edged, NOT precise, NOT dotted, NOT linear, NOT photorealistic. No brushstrokes visible — color should look poured, soaked, absorbed into raw canvas. NOT controlled or mechanical.",
        "palette_guidance": "Luminous washes: salmon pink, teal, golden ochre, lavender, cerulean. Colors should feel like watercolor — translucent and glowing. Raw canvas (warm cream) showing through.",
    },
    "piet_mondrian": {
        "description": "Piet Mondrian — neoplasticism with primary colors (red, blue, yellow) in rectangular fields divided by bold black lines. Asymmetric balance, white space as an active element. Pure geometric abstraction.",
        "weather_mapping": "Pressure maps to grid density — high pressure gradient = more black lines, smaller cells. Calm pressure = fewer lines, larger open white fields. Temperature determines which primary color dominates — heat = red dominates, cold = blue dominates, moderate = yellow. Wind speed controls asymmetry — strong wind = heavily asymmetric composition weighted to one side. Humidity maps to the thickness of black lines — high humidity = thicker, bolder lines.",
        "negative": "NOT organic, NOT curved, NOT gradient, NOT textured, NOT figurative, NOT photorealistic. NO circles, NO diagonals, NO blending. Only horizontal and vertical lines. Only primary colors plus black and white. This is the strictest visual system — deviate and it's not Mondrian.",
        "palette_guidance": "ONLY: pure red, pure blue, pure yellow, black, white. No mixing, no gradients, no secondary colors. White is the dominant color by area.",
    },
    "yayoi_kusama": {
        "description": "Yayoi Kusama — obsessive repetition of dots and circles. Infinity nets, polka dots in vivid colors against contrasting backgrounds. Patterns that suggest infinity and cosmic expansion.",
        "weather_mapping": "Wind speed drives dot density — strong wind = dense, tightly packed dots suggesting cosmic acceleration. Calm = sparse dots floating in space. Pressure maps to dot size variation — steep gradient = wide range from tiny to large. Temperature controls the color contrast — heat = vivid warm dots (red, orange, yellow) on dark backgrounds; cold = cool dots (blue, white, silver) on dark fields. Humidity controls the net density in infinity net patterns.",
        "negative": "NOT linear, NOT angular, NOT rectangular, NOT figurative, NOT photorealistic. No straight lines, no grids, no text. Everything must be circular — dots, circles, spheres, nets of round forms.",
        "palette_guidance": "High contrast: vivid red on white, yellow on black, pink on red, white on black. Monochromatic schemes with one accent color. The background-dot relationship is always bold.",
    },
    "mark_rothko": {
        "description": "Mark Rothko — luminous color field paintings with soft-edged rectangular forms floating on the canvas. Two or three horizontal bands of deeply saturated color that seem to glow from within. Contemplative, immersive, emotional.",
        "weather_mapping": "Temperature is the PRIMARY driver — it sets the emotional tone and color. Heat = deep warm reds, oranges, maroons (late Rothko). Cold = deep blues, blacks, dark greens (Rothko's contemplative period). Pressure maps to how many color bands — low pressure = two heavy bands filling the canvas, high pressure = three lighter bands with more breathing room. Wind creates the soft-edge vibration — strong wind = more blurred boundaries between bands, calm = slightly more defined edges. Humidity controls luminosity — high humidity = colors glow more, seem to emit light.",
        "negative": "NOT geometric, NOT precise, NOT linear, NOT figurative, NOT photorealistic, NOT patterned. No hard edges anywhere. No dots, no lines, no circles. No more than 3-4 color areas. NOT busy or complex — Rothko is about reduction and contemplation.",
        "palette_guidance": "Deep saturated fields: cadmium red/orange/yellow (warm) or ultramarine/black/dark green (cool). Colors must feel like they glow from within. Edges breathe and vibrate — never sharp.",
    },
    "bridget_riley": {
        "description": "Bridget Riley — op art with precise geometric patterns that create optical illusions of movement and vibration. Undulating lines, chevrons, and curves in carefully calibrated color relationships.",
        "weather_mapping": "Wind speed maps DIRECTLY to undulation frequency — strong wind = rapid, tight wave patterns that vibrate intensely. Calm = slow, wide undulations. Pressure gradient controls line density — steep = tightly packed lines, flat = more spacing. Temperature drives the color scheme — warm = her 1960s stripe paintings (warm color progressions), cold = her early black-and-white optical patterns. Wind direction sets the primary wave direction — horizontal, diagonal, or vertical undulations.",
        "negative": "NOT organic, NOT blurry, NOT soft, NOT random, NOT figurative, NOT photorealistic. No spontaneous marks. Every line must be precise and calculated. NOT chaotic — Riley's work is mathematically controlled even when it appears to vibrate.",
        "palette_guidance": "Early work: pure black and white. Color work: carefully sequenced progressions — coral→pink→lavender→blue or green→turquoise→blue. Never more than 4-5 colors in a single piece, always in systematic progression.",
    },
    "kazimir_malevich": {
        "description": "Kazimir Malevich — suprematist compositions with basic geometric forms (squares, circles, crosses, rectangles) floating in white space. Bold, flat colors. Dynamic diagonal arrangements suggesting weightlessness and cosmic space.",
        "weather_mapping": "Wind creates the diagonal dynamics — strong wind = shapes tilted at steep angles, suggesting flight and movement. Calm = shapes float horizontally, more static. Pressure maps to the density of forms — low pressure = fewer, heavier shapes dominating the canvas. High pressure = more scattered, lighter forms. Temperature drives color boldness — heat = vivid red, yellow forms; cold = black, deep blue forms. Precipitation adds secondary small shapes — rain = scattered small rectangles like falling geometry.",
        "negative": "NOT organic, NOT curved (except circles), NOT textured, NOT gradient, NOT figurative, NOT photorealistic. No blending, no soft edges, no naturalistic forms. Shapes are FLAT — no shading, no 3D illusion. White background is always dominant.",
        "palette_guidance": "FLAT colors only: black, red, blue, yellow, green. No gradients, no texture. White background dominates (60%+ of canvas). Each shape is one solid color.",
    },
    "lesley_tannahill": {
        "description": "Lesley Tannahill — California conceptual painter working in large-scale acrylic on canvas (72x72 inches). Her practice explores cognition, memory, and the geometric structure of thought through layered, reworked compositions that evolve over years. Dense palimpsests of paint where earlier marks show through later layers — deconstructing and reconstructing visual information. Fragments of recognizable form emerge from and dissolve into abstract fields. The tension between chaotic input and structured output, between knowing and not-knowing, drives compositions that feel simultaneously precise and intuitive.",
        "weather_mapping": "Pressure gradient maps to layer density — steep gradient = more visible layers, more archaeological depth. Temperature anomaly drives the tension between chaos and order — large anomaly = more chaotic marks breaking through structured layers; small anomaly = more resolved, contemplative layering. Wind creates the gestural energy in the under-layers — strong wind = aggressive scrubbed marks beneath. Humidity controls how much previous layers show through — high humidity = more translucent, more palimpsest visible. Precipitation adds drip marks and pooling that suggest time passing.",
        "negative": "NOT decorative, NOT symmetrical, NOT minimal, NOT clean, NOT photorealistic. This should feel WORKED — like it took years of painting over and scraping back. NOT digital-looking. NOT geometric in a Mondrian way — geometry here is buried, fragmentary, emerging from chaos.",
        "palette_guidance": "Muted California light: warm greys, dusty pinks, sage green, ochre, raw sienna. Occasional bright accent breaking through (cadmium orange, cerulean blue). Colors should feel like they've been painted over and scraped back — weathered, layered, lived-in.",
    },
}

# Legacy compat — ARTIST_PROMPTS still used in some paths
ARTIST_PROMPTS = {k: v["description"] for k, v in ARTIST_PROFILES.items()}


def build_svg_prompt(region):
    """Builds prompt for Claude SVG generation (vector download option)."""
    humidity_line = ""
    if region.get("humidity") is not None:
        humidity_line = f"\n- Relative humidity: {region['humidity']}%"

    precip_line = ""
    if region.get("precipitation") is not None:
        precip_line = f"\n- Precipitation: {region['precipitation']} kg/m^2"

    artist_key = region.get("artist", "sam_francis")
    profile = ARTIST_PROFILES.get(artist_key, ARTIST_PROFILES["sam_francis"])
    artist_desc = profile["description"]

    import random
    random.seed(hash(f"{region['slug']}{region.get('date', '')}{artist_key}"))
    formats = [
        ("2048", "2048"),
        ("2560", "1440"),
        ("1440", "2560"),
        ("2048", "1024"),
        ("1024", "2048"),
        ("1920", "1920"),
        ("2400", "1600"),
    ]
    width, height = random.choice(formats)
    canvas_format = f"{width}x{height}"

    return f"""You are a generative artist creating abstract SVG artwork inspired by real-time
atmospheric data. Create a single, self-contained SVG artwork that visually interprets
the following weather conditions.

Canvas: viewBox="0 0 {width} {height}" — use the full canvas. This is a {'landscape' if int(width) > int(height) else 'portrait' if int(height) > int(width) else 'square'} composition.

Location: {region['slug']} ({region['lat']}, {region['lng']})
Atmospheric conditions:
- Sea-level pressure: {region['pressure']} Pa (gradient: {region['pressure_gradient']} Pa/cell)
- Wind: {region['wind_speed']} m/s from {region['wind_direction']} degrees
- Temperature: {region['temp']} K (anomaly from zonal mean: {region['temp_anomaly']} K){humidity_line}{precip_line}
- Visual interest score: {region['score']}

Artistic direction:
- Draw inspiration from {artist_desc}
- ARTIST-SPECIFIC WEATHER INTERPRETATION: {profile['weather_mapping']}
- PALETTE: {profile['palette_guidance']}
- CONSTRAINTS: {profile['negative']}
- Each piece should feel unique — vary your approach while staying true to the artist's aesthetic.
- Create a substantial, richly detailed work — use 30-60+ shape elements, layered gradients,
  and complex compositions. This should feel like a museum-quality digital piece.

Technical requirements:
1. First, write 2-3 sentences explaining your artistic interpretation. Begin by naming the
   real-world geography of this location (e.g., "Over the North Atlantic south of Iceland..."
   or "Above the Saharan coast near Mauritania..."). Then describe how the atmospheric
   conditions shaped your visual choices. Write in plain prose — no markdown, no bold, no
   headers, no code fences.
2. Then output the complete SVG on its own line starting with <svg
3. Use gradients, filters, organic shapes, and layered transparency — no text elements
4. The SVG must be valid XML and self-contained (no external references)
5. Use viewBox="0 0 {width} {height}" on the root <svg> element""", canvas_format


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
    )
