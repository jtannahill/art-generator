# Print Shop Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add limited-edition print sales to art.jamestannahill.com via Stripe Checkout + theprintspace fulfillment.

**Architecture:** Single new Lambda (`art-print-shop`) with Function URL, four actions (editions, checkout, stripe_webhook, tps_webhook). Existing DynamoDB table gets new PK patterns (EDITION#, ORDER#, PRODUCT#, SESSION#, TPS_ORDER#). Frontend is a slide-out drawer on artwork pages. Stripe handles payment, theprintspace handles printing/shipping.

**Note:** Featured badge on gallery cards is deferred to v2 — the `featured` field exists in the data model but rendering it across gallery templates is out of scope for this plan.

**Tech Stack:** Python 3.12, CDK (TypeScript), Stripe API, theprintspace CreativeHub API, DynamoDB, S3, SES, Secrets Manager, Jinja2

**Spec:** `docs/superpowers/specs/2026-03-17-print-shop-design.md`

---

## File Map

### New files

| File | Responsibility |
|------|---------------|
| `lambdas/print_shop/handler.py` | Main Lambda handler — routes to action handlers |
| `lambdas/print_shop/editions.py` | `?action=editions` — lazy EDITION creation, tier lookup |
| `lambdas/print_shop/checkout.py` | `?action=checkout` — Stripe session creation |
| `lambdas/print_shop/stripe_webhook.py` | `?action=stripe_webhook` — payment processing, order creation, fulfillment |
| `lambdas/print_shop/tps_webhook.py` | `?action=tps_webhook` — dispatch/tracking updates |
| `lambdas/print_shop/tps_client.py` | theprintspace API client (product registration, orders) |
| `lambdas/print_shop/email.py` | SES confirmation + alert emails |
| `lambdas/print_shop/tiers.py` | Size tier config — aspect ratio → sizes/prices/limits |
| `lambdas/print_shop/secrets.py` | Secrets Manager loader with cold-start cache |
| `lambdas/print_shop/requirements.txt` | `stripe`, `requests` |
| `lambdas/site_rebuild/templates/shop_success.html` | Order success page |
| `lambdas/site_rebuild/templates/shop_cancel.html` | Checkout cancelled page |
| `scripts/backfill_canvas_format.py` | One-time backfill: parse SVG viewBox → DynamoDB + re-render PNGs |
| `tests/print_shop/test_tiers.py` | Tier config tests |
| `tests/print_shop/test_editions.py` | Edition creation/lookup tests |
| `tests/print_shop/test_checkout.py` | Checkout session creation tests |
| `tests/print_shop/test_stripe_webhook.py` | Webhook processing tests |
| `tests/print_shop/test_tps_webhook.py` | theprintspace webhook tests |
| `tests/print_shop/test_tps_client.py` | theprintspace API client tests |
| `tests/print_shop/conftest.py` | Shared fixtures (DynamoDB mock, sample artworks) |

### Modified files

| File | Changes |
|------|---------|
| `lambdas/weather_render/handler.py:230-255,308-314` | Return canvas_format from `build_art_prompt()`, store in metadata/DynamoDB, fix `render_png()` aspect ratio |
| `lambdas/site_rebuild/handler.py:78-83,378-390` | Pass `canvas_format` + `print_shop_url` to templates, add `/shop/*` to CF invalidation |
| `lambdas/site_rebuild/templates/weather_single.html:19-59,72-74,130-131,141-145` | Add drawer markup+JS, fix hardcoded dimensions in Schema.org + Technical Details, replace mailto CTA |
| `lambdas/site_rebuild/templates/base.html:263` | Add drawer CSS |
| `cdk/lib/art-generator-stack.ts:290-350` | Add print-shop Lambda, Function URL, Secrets Manager, SES permissions |

---

## Task 1: Size tier config

**Files:**
- Create: `lambdas/print_shop/tiers.py`
- Create: `tests/print_shop/test_tiers.py`
- Create: `tests/print_shop/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Write the tier config test**

```python
# tests/print_shop/test_tiers.py
from lambdas.print_shop.tiers import get_tiers_for_format, ASPECT_RATIOS, format_to_aspect_ratio


def test_square_format_returns_square_tiers():
    tiers = get_tiers_for_format("2048x2048")
    assert tiers["aspect_ratio"] == "1:1"
    assert set(tiers["sizes"].keys()) == {"S", "M", "L", "XL", "XXL"}
    assert tiers["sizes"]["S"]["dims"] == "12x12"
    assert tiers["sizes"]["S"]["price_cents"] == 9500
    assert tiers["sizes"]["S"]["limit"] == 100


def test_large_square_maps_to_same_tiers():
    t1 = get_tiers_for_format("2048x2048")
    t2 = get_tiers_for_format("1920x1920")
    assert t1 == t2


def test_panoramic_wide_format():
    tiers = get_tiers_for_format("2048x1024")
    assert tiers["aspect_ratio"] == "2:1"
    assert tiers["sizes"]["XXL"]["dims"] == "60x30"
    assert tiers["sizes"]["XXL"]["price_cents"] == 110000


def test_tall_portrait_format():
    tiers = get_tiers_for_format("1440x2560")
    assert tiers["aspect_ratio"] == "9:16"
    assert tiers["sizes"]["S"]["dims"] == "9x16"


def test_tall_panoramic_format():
    tiers = get_tiers_for_format("1024x2048")
    assert tiers["aspect_ratio"] == "1:2"
    assert tiers["sizes"]["L"]["dims"] == "20x40"


def test_landscape_16_9_format():
    tiers = get_tiers_for_format("2560x1440")
    assert tiers["aspect_ratio"] == "16:9"
    assert tiers["sizes"]["XL"]["dims"] == "48x27"


def test_golden_landscape_format():
    tiers = get_tiers_for_format("2400x1600")
    assert tiers["aspect_ratio"] == "3:2"
    assert tiers["sizes"]["M"]["dims"] == "18x12"


def test_format_to_aspect_ratio_all_formats():
    assert format_to_aspect_ratio("2048x2048") == "1:1"
    assert format_to_aspect_ratio("1920x1920") == "1:1"
    assert format_to_aspect_ratio("2560x1440") == "16:9"
    assert format_to_aspect_ratio("2400x1600") == "3:2"
    assert format_to_aspect_ratio("2048x1024") == "2:1"
    assert format_to_aspect_ratio("1440x2560") == "9:16"
    assert format_to_aspect_ratio("1024x2048") == "1:2"


def test_unknown_format_raises():
    import pytest
    with pytest.raises(ValueError):
        get_tiers_for_format("999x999")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/jamest/art-generator && python -m pytest tests/print_shop/test_tiers.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write tier config implementation**

```python
# lambdas/print_shop/tiers.py
"""Size tier configuration for each aspect ratio.

Maps canvas viewBox formats to print sizes, edition limits, and prices.
"""

FORMAT_TO_RATIO = {
    "2048x2048": "1:1",
    "1920x1920": "1:1",
    "2560x1440": "16:9",
    "2400x1600": "3:2",
    "2048x1024": "2:1",
    "1440x2560": "9:16",
    "1024x2048": "1:2",
}

ASPECT_RATIOS = {
    "1:1": {
        "S":   {"dims": "12x12", "limit": 100, "price_cents": 9500},
        "M":   {"dims": "20x20", "limit": 75,  "price_cents": 19500},
        "L":   {"dims": "30x30", "limit": 50,  "price_cents": 35000},
        "XL":  {"dims": "40x40", "limit": 25,  "price_cents": 59500},
        "XXL": {"dims": "60x60", "limit": 10,  "price_cents": 120000},
    },
    "16:9": {
        "S":   {"dims": "16x9",  "limit": 100, "price_cents": 8500},
        "M":   {"dims": "24x14", "limit": 75,  "price_cents": 17500},
        "L":   {"dims": "36x20", "limit": 50,  "price_cents": 32500},
        "XL":  {"dims": "48x27", "limit": 25,  "price_cents": 55000},
        "XXL": {"dims": "60x34", "limit": 10,  "price_cents": 99500},
    },
    "3:2": {
        "S":   {"dims": "12x8",  "limit": 100, "price_cents": 8500},
        "M":   {"dims": "18x12", "limit": 75,  "price_cents": 17500},
        "L":   {"dims": "24x16", "limit": 50,  "price_cents": 32500},
        "XL":  {"dims": "36x24", "limit": 25,  "price_cents": 55000},
        "XXL": {"dims": "50x34", "limit": 10,  "price_cents": 99500},
    },
    "2:1": {
        "S":   {"dims": "20x10", "limit": 100, "price_cents": 9500},
        "M":   {"dims": "30x15", "limit": 75,  "price_cents": 19500},
        "L":   {"dims": "40x20", "limit": 50,  "price_cents": 37500},
        "XL":  {"dims": "50x25", "limit": 25,  "price_cents": 65000},
        "XXL": {"dims": "60x30", "limit": 10,  "price_cents": 110000},
    },
    "9:16": {
        "S":   {"dims": "9x16",  "limit": 100, "price_cents": 8500},
        "M":   {"dims": "14x24", "limit": 75,  "price_cents": 17500},
        "L":   {"dims": "20x36", "limit": 50,  "price_cents": 32500},
        "XL":  {"dims": "27x48", "limit": 25,  "price_cents": 55000},
        "XXL": {"dims": "34x60", "limit": 10,  "price_cents": 99500},
    },
    "1:2": {
        "S":   {"dims": "10x20", "limit": 100, "price_cents": 9500},
        "M":   {"dims": "15x30", "limit": 75,  "price_cents": 19500},
        "L":   {"dims": "20x40", "limit": 50,  "price_cents": 37500},
        "XL":  {"dims": "25x50", "limit": 25,  "price_cents": 65000},
        "XXL": {"dims": "30x60", "limit": 10,  "price_cents": 110000},
    },
}


def format_to_aspect_ratio(canvas_format: str) -> str:
    """Convert a viewBox format string like '2048x2048' to aspect ratio like '1:1'."""
    ratio = FORMAT_TO_RATIO.get(canvas_format)
    if ratio is None:
        raise ValueError(f"Unknown canvas format: {canvas_format}")
    return ratio


def get_tiers_for_format(canvas_format: str) -> dict:
    """Return tier config for a canvas format.

    Returns: {"aspect_ratio": "1:1", "sizes": {"S": {"dims": ..., "limit": ..., "price_cents": ...}, ...}}
    """
    ratio = format_to_aspect_ratio(canvas_format)
    sizes = {k: {**v, "sold": 0} for k, v in ASPECT_RATIOS[ratio].items()}
    return {"aspect_ratio": ratio, "sizes": sizes}
```

- [ ] **Step 4: Create `__init__.py` files and run tests**

Create empty `tests/__init__.py`, `tests/print_shop/__init__.py`, `lambdas/print_shop/__init__.py`.

Run: `cd /Users/jamest/art-generator && python -m pytest tests/print_shop/test_tiers.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add lambdas/print_shop/tiers.py lambdas/print_shop/__init__.py tests/print_shop/test_tiers.py tests/print_shop/__init__.py tests/__init__.py
git commit -m "feat(print-shop): add size tier config with aspect ratio mapping"
```

---

## Task 2: Fix weather_render — canvas_format storage + PNG aspect ratio

**Files:**
- Modify: `lambdas/weather_render/handler.py:30-31,92-111,230-255,308-314`

- [ ] **Step 1: Refactor `build_art_prompt` to return dimensions**

In `lambdas/weather_render/handler.py`, change `build_art_prompt` (line 230) to return a tuple:

```python
def build_art_prompt(region):
    """Builds prompt from atmospheric data including humidity and precipitation.

    Returns: (prompt_text, canvas_format) where canvas_format is e.g. '2048x1440'.
    """
    # ... existing code through line 255 ...
    width, height = random.choice(formats)

    canvas_format = f"{width}x{height}"

    prompt = f"""You are a generative artist..."""  # existing f-string

    return prompt, canvas_format
```

- [ ] **Step 2: Update handler to use new return value and store canvas_format**

In `handler()` (line 31), change:
```python
# Old:
prompt = build_art_prompt(region)

# New:
prompt, canvas_format = build_art_prompt(region)
```

In the metadata dict (line 92-111), add `canvas_format`:
```python
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
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] **Step 3: Fix `render_png` to respect aspect ratio**

Replace `render_png` (lines 308-314):
```python
def render_png(svg_text, width):
    """Uses cairosvg.svg2png to render SVG to PNG bytes at the given width.

    Height is derived from the SVG viewBox aspect ratio automatically.
    """
    return cairosvg.svg2png(
        bytestring=svg_text.encode("utf-8"),
        output_width=width,
    )
```

- [ ] **Step 4: Verify the changes work**

Run: `cd /Users/jamest/art-generator && python -c "from lambdas.weather_render.handler import build_art_prompt; p, cf = build_art_prompt({'slug': 'test', 'date': '2026-01-01', 'artist': 'sam_francis', 'lat': 0, 'lng': 0, 'pressure': 101325, 'pressure_gradient': 100, 'wind_speed': 5, 'wind_direction': 180, 'temp': 293, 'temp_anomaly': 2, 'score': 0.8}); print(f'canvas_format={cf}'); assert 'x' in cf"`
Expected: Prints canvas_format value, no assertion error

- [ ] **Step 5: Commit**

```bash
git add lambdas/weather_render/handler.py
git commit -m "feat(weather-render): store canvas_format in metadata, fix PNG aspect ratio"
```

---

## Task 3: Backfill script — canvas_format for existing artworks

**Files:**
- Create: `scripts/backfill_canvas_format.py`

- [ ] **Step 1: Write backfill script**

```python
#!/usr/bin/env python3
"""One-time backfill: parse SVG viewBox from S3, write canvas_format to DynamoDB,
and re-render PNGs with correct aspect ratio.

Requires: boto3, cairosvg (pip install cairosvg)
Usage: python scripts/backfill_canvas_format.py [--dry-run]
"""

import json
import re
import sys

import boto3

BUCKET_NAME = "art-generator-216890068001"
TABLE_NAME = "art-generator"


def parse_viewbox(svg_text: str) -> str | None:
    """Extract viewBox from SVG and return as 'WxH' format."""
    match = re.search(r'viewBox\s*=\s*"([^"]+)"', svg_text)
    if not match:
        return None
    parts = match.group(1).split()
    if len(parts) == 4:
        w, h = parts[2], parts[3]
        return f"{int(float(w))}x{int(float(h))}"
    return None


def render_png(svg_text: str, width: int) -> bytes:
    """Render SVG to PNG at given width, preserving aspect ratio."""
    import cairosvg
    return cairosvg.svg2png(
        bytestring=svg_text.encode("utf-8"),
        output_width=width,
    )


def main():
    dry_run = "--dry-run" in sys.argv

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(TABLE_NAME)
    s3 = boto3.client("s3")

    # Scan all WEATHER# items
    items = []
    params = {"FilterExpression": "begins_with(PK, :prefix)", "ExpressionAttributeValues": {":prefix": "WEATHER#"}}
    while True:
        resp = table.scan(**params)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        params["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    print(f"Found {len(items)} WEATHER# items")
    updated = 0
    skipped = 0
    errors = 0

    for item in items:
        pk = item["PK"]
        sk = item["SK"]
        run_id = pk.replace("WEATHER#", "")
        slug = sk

        # Skip if already has canvas_format
        if item.get("canvas_format"):
            skipped += 1
            continue

        # Read SVG from S3
        s3_key = f"weather/{run_id}/{slug}/artwork.svg"
        try:
            obj = s3.get_object(Bucket=BUCKET_NAME, Key=s3_key)
            svg_text = obj["Body"].read().decode("utf-8")
        except Exception as e:
            print(f"  ERROR reading {s3_key}: {e}")
            errors += 1
            continue

        canvas_format = parse_viewbox(svg_text)
        if not canvas_format:
            print(f"  ERROR: no viewBox in {s3_key}")
            errors += 1
            continue

        print(f"  {run_id}/{slug}: {canvas_format}", end="")

        if dry_run:
            print(" (dry run)")
            updated += 1
            continue

        # Update DynamoDB
        table.update_item(
            Key={"PK": pk, "SK": sk},
            UpdateExpression="SET canvas_format = :cf",
            ExpressionAttributeValues={":cf": canvas_format},
        )

        # Re-render PNGs with correct aspect ratio
        try:
            png_2048 = render_png(svg_text, 2048)
            png_4k = render_png(svg_text, 4096)
            prefix = f"weather/{run_id}/{slug}"
            s3.put_object(Bucket=BUCKET_NAME, Key=f"{prefix}/preview-2048.png", Body=png_2048, ContentType="image/png")
            s3.put_object(Bucket=BUCKET_NAME, Key=f"{prefix}/preview-4k.png", Body=png_4k, ContentType="image/png")
            print(" + PNGs re-rendered")
        except Exception as e:
            print(f" (PNGs failed: {e})")

        updated += 1

    print(f"\nDone: {updated} updated, {skipped} skipped (already had canvas_format), {errors} errors")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Test with dry run**

Run: `cd /Users/jamest/art-generator && python scripts/backfill_canvas_format.py --dry-run`
Expected: Lists all WEATHER# items with their parsed canvas_format, no writes

- [ ] **Step 3: Run for real**

Run: `cd /Users/jamest/art-generator && python scripts/backfill_canvas_format.py`
Expected: Updates DynamoDB items and re-renders PNGs. Print count summary.

- [ ] **Step 4: Verify a sample**

Run: `cd /Users/jamest/art-generator && python -c "import boto3; t = boto3.resource('dynamodb').Table('art-generator'); r = t.scan(FilterExpression='begins_with(PK, :p)', ExpressionAttributeValues={':p': 'WEATHER#'}, Limit=5); [print(i['PK'], i['SK'], i.get('canvas_format', 'MISSING')) for i in r['Items']]"`
Expected: All items show a canvas_format value

- [ ] **Step 5: Commit**

```bash
git add scripts/backfill_canvas_format.py
git commit -m "feat: add canvas_format backfill script for existing artworks"
```

---

## Task 4: Secrets Manager loader

**Files:**
- Create: `lambdas/print_shop/secrets.py`

- [ ] **Step 1: Write secrets loader**

```python
# lambdas/print_shop/secrets.py
"""Load and cache secrets from AWS Secrets Manager on cold start."""

import json
import os

import boto3

_cache = {}
SECRET_ID = os.environ.get("SECRET_ID", "art-generator/print-shop")


def get_secrets() -> dict:
    """Return cached secrets dict with keys: stripe_secret_key, stripe_webhook_secret, tps_api_key."""
    if not _cache:
        client = boto3.client("secretsmanager")
        resp = client.get_secret_value(SecretId=SECRET_ID)
        _cache.update(json.loads(resp["SecretString"]))
    return _cache
```

- [ ] **Step 2: Commit**

```bash
git add lambdas/print_shop/secrets.py
git commit -m "feat(print-shop): add Secrets Manager loader with cold-start cache"
```

---

## Task 5: Editions action — lazy EDITION creation + lookup

**Files:**
- Create: `lambdas/print_shop/editions.py`
- Create: `tests/print_shop/conftest.py`
- Create: `tests/print_shop/test_editions.py`

- [ ] **Step 1: Write test fixtures**

```python
# tests/print_shop/conftest.py
import os
import pytest
import boto3
from unittest.mock import patch, MagicMock

os.environ.setdefault("TABLE_NAME", "art-generator-test")
os.environ.setdefault("BUCKET_NAME", "art-generator-test")
os.environ.setdefault("SECRET_ID", "test/secret")


@pytest.fixture
def mock_table():
    """Mock DynamoDB table."""
    table = MagicMock()
    table.table_name = "art-generator-test"
    return table


@pytest.fixture
def sample_weather_item():
    return {
        "PK": "WEATHER#2026-03-16-130500",
        "SK": "arctic-70n-20w",
        "run_id": "2026-03-16-130500",
        "slug": "arctic-70n-20w",
        "artist": "sam_francis",
        "canvas_format": "2048x2048",
        "lat": 70,
        "lng": -20,
        "score": 0.85,
    }


@pytest.fixture
def sample_edition_item():
    return {
        "PK": "EDITION#2026-03-16-130500#arctic-70n-20w",
        "SK": "META",
        "canvas_format": "2048x2048",
        "aspect_ratio": "1:1",
        "featured": False,
        "sizes": {
            "S":   {"dims": "12x12", "limit": 100, "sold": 0, "price_cents": 9500},
            "M":   {"dims": "20x20", "limit": 75,  "sold": 0, "price_cents": 19500},
            "L":   {"dims": "30x30", "limit": 50,  "sold": 0, "price_cents": 35000},
            "XL":  {"dims": "40x40", "limit": 25,  "sold": 0, "price_cents": 59500},
            "XXL": {"dims": "60x60", "limit": 10,  "sold": 0, "price_cents": 120000},
        },
    }
```

- [ ] **Step 2: Write editions tests**

```python
# tests/print_shop/test_editions.py
from unittest.mock import MagicMock, patch
from lambdas.print_shop.editions import get_editions


def test_returns_existing_edition(mock_table, sample_edition_item):
    mock_table.get_item.return_value = {"Item": sample_edition_item}

    result = get_editions(mock_table, "2026-03-16-130500", "arctic-70n-20w")

    assert result["aspect_ratio"] == "1:1"
    assert result["sizes"]["S"]["dims"] == "12x12"
    mock_table.get_item.assert_called_once_with(
        Key={"PK": "EDITION#2026-03-16-130500#arctic-70n-20w", "SK": "META"}
    )


def test_creates_edition_lazily_from_weather_item(mock_table, sample_weather_item):
    # First call: no EDITION item
    mock_table.get_item.side_effect = [
        {"Item": None},  # No EDITION
        {"Item": sample_weather_item},  # WEATHER item found
    ]
    # get_item returns None for EDITION, so we fall through
    mock_table.get_item.side_effect = [
        {},  # No EDITION
        {"Item": sample_weather_item},  # WEATHER item
    ]

    result = get_editions(mock_table, "2026-03-16-130500", "arctic-70n-20w")

    assert result["aspect_ratio"] == "1:1"
    assert result["sizes"]["S"]["limit"] == 100
    # Should have written the new EDITION item
    mock_table.put_item.assert_called_once()
    written = mock_table.put_item.call_args[1]["Item"]
    assert written["PK"] == "EDITION#2026-03-16-130500#arctic-70n-20w"
    assert written["SK"] == "META"


def test_returns_none_if_artwork_not_found(mock_table):
    mock_table.get_item.side_effect = [{}, {}]

    result = get_editions(mock_table, "nonexistent", "slug")
    assert result is None


@patch("lambdas.print_shop.editions._parse_viewbox_from_s3")
def test_falls_back_to_s3_viewbox(mock_parse, mock_table):
    """If WEATHER item has no canvas_format, parse from S3 SVG."""
    weather_no_format = {
        "PK": "WEATHER#run1", "SK": "slug1",
        "run_id": "run1", "slug": "slug1",
    }
    mock_table.get_item.side_effect = [
        {},  # No EDITION
        {"Item": weather_no_format},  # WEATHER without canvas_format
    ]
    mock_parse.return_value = "2560x1440"

    result = get_editions(mock_table, "run1", "slug1")

    assert result["aspect_ratio"] == "16:9"
    mock_parse.assert_called_once_with("run1", "slug1")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /Users/jamest/art-generator && python -m pytest tests/print_shop/test_editions.py -v`
Expected: FAIL — module not found

- [ ] **Step 4: Write editions implementation**

```python
# lambdas/print_shop/editions.py
"""Editions action — lazy EDITION creation and lookup."""

import os
import re

import boto3

from .tiers import get_tiers_for_format

BUCKET_NAME = os.environ.get("BUCKET_NAME", "art-generator-216890068001")


def get_editions(table, run_id: str, slug: str) -> dict | None:
    """Get edition data for an artwork. Creates EDITION item lazily on first read.

    Returns dict with aspect_ratio, featured, sizes (with sold counts), or None if artwork not found.
    """
    edition_pk = f"EDITION#{run_id}#{slug}"

    # Check for existing EDITION item
    resp = table.get_item(Key={"PK": edition_pk, "SK": "META"})
    if "Item" in resp and resp["Item"]:
        item = resp["Item"]
        return _serialize_edition({
            "aspect_ratio": item["aspect_ratio"],
            "featured": item.get("featured", False),
            "sizes": item["sizes"],
            "canvas_format": item["canvas_format"],
        })

    # No EDITION — look up the WEATHER item to get canvas_format
    weather_resp = table.get_item(Key={"PK": f"WEATHER#{run_id}", "SK": slug})
    weather = weather_resp.get("Item")
    if not weather:
        return None

    canvas_format = weather.get("canvas_format")
    if not canvas_format:
        # Fallback: parse viewBox from SVG in S3
        canvas_format = _parse_viewbox_from_s3(run_id, slug)
        if not canvas_format:
            return None

    # Create EDITION item with default tiers
    tiers = get_tiers_for_format(canvas_format)
    edition_item = {
        "PK": edition_pk,
        "SK": "META",
        "canvas_format": canvas_format,
        "aspect_ratio": tiers["aspect_ratio"],
        "featured": False,
        "sizes": tiers["sizes"],
    }
    table.put_item(Item=edition_item)

    return _serialize_edition({
        "aspect_ratio": tiers["aspect_ratio"],
        "featured": False,
        "sizes": tiers["sizes"],
        "canvas_format": canvas_format,
    })


def _serialize_edition(edition: dict) -> dict:
    """Convert DynamoDB Decimal values to int for JSON serialization."""
    from decimal import Decimal
    result = {**edition}
    if "sizes" in result:
        result["sizes"] = {
            k: {sk: int(sv) if isinstance(sv, Decimal) else sv for sk, sv in v.items()}
            for k, v in result["sizes"].items()
        }
    return result


def _parse_viewbox_from_s3(run_id: str, slug: str) -> str | None:
    """Fallback: read SVG from S3 and parse viewBox dimensions."""
    s3 = boto3.client("s3")
    try:
        obj = s3.get_object(Bucket=BUCKET_NAME, Key=f"weather/{run_id}/{slug}/artwork.svg")
        svg_text = obj["Body"].read().decode("utf-8")
        match = re.search(r'viewBox\s*=\s*"([^"]+)"', svg_text)
        if match:
            parts = match.group(1).split()
            if len(parts) == 4:
                return f"{int(float(parts[2]))}x{int(float(parts[3]))}"
    except Exception:
        pass
    return None
```

- [ ] **Step 5: Run tests**

Run: `cd /Users/jamest/art-generator && python -m pytest tests/print_shop/test_editions.py -v`
Expected: All 4 tests PASS

- [ ] **Step 6: Commit**

```bash
git add lambdas/print_shop/editions.py tests/print_shop/conftest.py tests/print_shop/test_editions.py
git commit -m "feat(print-shop): add editions action with lazy EDITION creation"
```

---

## Task 6: Checkout action — Stripe session creation

**Files:**
- Create: `lambdas/print_shop/checkout.py`
- Create: `tests/print_shop/test_checkout.py`

- [ ] **Step 1: Write checkout tests**

```python
# tests/print_shop/test_checkout.py
from unittest.mock import MagicMock, patch
from lambdas.print_shop.checkout import create_checkout_session


@patch("lambdas.print_shop.checkout.stripe")
def test_creates_stripe_session(mock_stripe, mock_table, sample_edition_item):
    mock_table.get_item.return_value = {"Item": sample_edition_item}
    mock_stripe.checkout.Session.create.return_value = MagicMock(url="https://checkout.stripe.com/sess_123")

    result = create_checkout_session(
        table=mock_table,
        stripe_key="sk_test_123",
        run_id="2026-03-16-130500",
        slug="arctic-70n-20w",
        size_key="L",
        base_url="https://art.jamestannahill.com",
    )

    assert result["url"] == "https://checkout.stripe.com/sess_123"
    mock_stripe.checkout.Session.create.assert_called_once()
    call_kwargs = mock_stripe.checkout.Session.create.call_args[1]
    assert call_kwargs["metadata"]["run_id"] == "2026-03-16-130500"
    assert call_kwargs["metadata"]["slug"] == "arctic-70n-20w"
    assert call_kwargs["metadata"]["size_key"] == "L"
    assert call_kwargs["line_items"][0]["price_data"]["unit_amount"] == 35000


@patch("lambdas.print_shop.checkout.stripe")
def test_returns_error_if_sold_out(mock_stripe, mock_table, sample_edition_item):
    sample_edition_item["sizes"]["L"]["sold"] = 50  # At limit
    mock_table.get_item.return_value = {"Item": sample_edition_item}

    result = create_checkout_session(
        table=mock_table,
        stripe_key="sk_test_123",
        run_id="2026-03-16-130500",
        slug="arctic-70n-20w",
        size_key="L",
        base_url="https://art.jamestannahill.com",
    )

    assert result["error"] == "sold_out"
    mock_stripe.checkout.Session.create.assert_not_called()


@patch("lambdas.print_shop.checkout.stripe")
def test_returns_error_if_no_edition(mock_stripe, mock_table):
    mock_table.get_item.return_value = {}

    result = create_checkout_session(
        table=mock_table,
        stripe_key="sk_test_123",
        run_id="nonexistent",
        slug="nope",
        size_key="S",
        base_url="https://art.jamestannahill.com",
    )

    assert result["error"] == "not_found"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/jamest/art-generator && python -m pytest tests/print_shop/test_checkout.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write checkout implementation**

```python
# lambdas/print_shop/checkout.py
"""Checkout action — creates Stripe Checkout Session."""

import stripe


def create_checkout_session(
    table, stripe_key: str, run_id: str, slug: str, size_key: str, base_url: str
) -> dict:
    """Create a Stripe Checkout Session for a print purchase.

    Returns: {"url": "https://checkout.stripe.com/..."} or {"error": "reason"}
    """
    # Look up EDITION item
    edition_pk = f"EDITION#{run_id}#{slug}"
    resp = table.get_item(Key={"PK": edition_pk, "SK": "META"})
    edition = resp.get("Item")
    if not edition:
        return {"error": "not_found"}

    sizes = edition.get("sizes", {})
    size = sizes.get(size_key)
    if not size:
        return {"error": "invalid_size"}

    if size["sold"] >= size["limit"]:
        return {"error": "sold_out"}

    # Create Stripe Checkout Session
    stripe.api_key = stripe_key
    title = slug.replace("-", " ").title()
    dims = size["dims"]

    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{
            "price_data": {
                "currency": "usd",
                "unit_amount": int(size["price_cents"]),
                "product_data": {
                    "name": f"{title} — {dims}\" Limited Edition Print",
                    "description": f"Giclée print on Hahnemühle German Etching 310gsm. Edition of {size['limit']}. Certificate of Authenticity included.",
                },
            },
            "quantity": 1,
        }],
        metadata={
            "run_id": run_id,
            "slug": slug,
            "size_key": size_key,
        },
        shipping_address_collection={
            "allowed_countries": [
                "US", "GB", "CA", "AU", "DE", "FR", "IT", "ES", "NL", "BE",
                "AT", "CH", "SE", "NO", "DK", "FI", "IE", "PT", "PL", "CZ",
                "JP", "SG", "NZ", "LU", "HK",
            ],
        },
        success_url=f"{base_url}/shop/success/?session_id={{CHECKOUT_SESSION_ID}}&edition=pending&size={size_key}&title={slug}",
        cancel_url=f"{base_url}/weather/{run_id}/{slug}/",
    )

    return {"url": session.url}
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/jamest/art-generator && python -m pytest tests/print_shop/test_checkout.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add lambdas/print_shop/checkout.py tests/print_shop/test_checkout.py
git commit -m "feat(print-shop): add checkout action with Stripe session creation"
```

---

## Task 7: theprintspace API client

**Files:**
- Create: `lambdas/print_shop/tps_client.py`
- Create: `tests/print_shop/test_tps_client.py`

- [ ] **Step 1: Write tps_client tests**

```python
# tests/print_shop/test_tps_client.py
from unittest.mock import patch, MagicMock
from lambdas.print_shop.tps_client import TpsClient


@patch("lambdas.print_shop.tps_client.requests")
def test_create_embryonic_order(mock_requests):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "Id": 12345,
        "DeliveryOptions": [
            {"Id": 1, "DeliveryChargeExcludingSalesTax": 15.00, "DeliveryChargeSalesTax": 0, "Method": "Standard"},
            {"Id": 2, "DeliveryChargeExcludingSalesTax": 30.00, "DeliveryChargeSalesTax": 0, "Method": "Express"},
        ],
    }
    mock_resp.raise_for_status = MagicMock()
    mock_requests.post.return_value = mock_resp

    client = TpsClient(api_key="test-key", base_url="https://api.sandbox.tps-test.io")
    result = client.create_embryonic_order(
        product_id=100,
        print_option_id=200,
        first_name="John",
        last_name="Doe",
        email="john@example.com",
        address={"line1": "123 Main St", "town": "London", "post_code": "SW1A 1AA", "country_code": "GB", "phone": "+447700900000"},
    )

    assert result["Id"] == 12345
    assert len(result["DeliveryOptions"]) == 2


@patch("lambdas.print_shop.tps_client.requests")
def test_confirm_order_picks_cheapest(mock_requests):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"Id": 12345, "OrderState": "NewOrder"}
    mock_resp.raise_for_status = MagicMock()
    mock_requests.post.return_value = mock_resp

    client = TpsClient(api_key="test-key")
    delivery_options = [
        {"Id": 2, "DeliveryChargeExcludingSalesTax": 30.00, "DeliveryChargeSalesTax": 3.00},
        {"Id": 1, "DeliveryChargeExcludingSalesTax": 15.00, "DeliveryChargeSalesTax": 1.50},
    ]
    result = client.confirm_order(order_id=12345, delivery_options=delivery_options)

    call_kwargs = mock_requests.post.call_args[1]["json"]
    assert call_kwargs["DeliveryOptionId"] == 1  # Cheapest
    assert result["Id"] == 12345
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/jamest/art-generator && python -m pytest tests/print_shop/test_tps_client.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write tps_client implementation**

```python
# lambdas/print_shop/tps_client.py
"""theprintspace CreativeHub API client."""

import requests

DEFAULT_BASE_URL = "https://api.creativehub.io"


class TpsClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL):
        self.base_url = base_url
        self.headers = {
            "Authorization": f"ApiKey {api_key}",
            "Content-Type": "application/json",
        }

    def create_embryonic_order(
        self,
        product_id: int,
        print_option_id: int,
        first_name: str,
        last_name: str,
        email: str,
        address: dict,
        coa_print_option_id: int | None = None,
    ) -> dict:
        """Create a draft order. Returns order with delivery options."""
        order_items = [{
            "ProductId": product_id,
            "PrintOptionId": print_option_id,
            "Quantity": 1,
        }]
        if coa_print_option_id:
            order_items.append({
                "ProductId": product_id,
                "PrintOptionId": coa_print_option_id,
                "Quantity": 1,
            })

        payload = {
            "FirstName": first_name,
            "LastName": last_name,
            "Email": email,
            "OrderItems": order_items,
            "ShippingAddress": {
                "FirstName": first_name,
                "LastName": last_name,
                "Line1": address["line1"],
                "Line2": address.get("line2", ""),
                "Town": address.get("town", ""),
                "County": address.get("county", ""),
                "PostCode": address["post_code"],
                "CountryCode": address.get("country_code", ""),
                "PhoneNumber": address["phone"],
            },
        }

        resp = requests.post(
            f"{self.base_url}/api/v1/orders/embryonic",
            headers=self.headers,
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    def confirm_order(self, order_id: int, delivery_options: list) -> dict:
        """Confirm order with cheapest delivery option."""
        cheapest = min(delivery_options, key=lambda d: d["DeliveryChargeExcludingSalesTax"])

        payload = {
            "OrderId": order_id,
            "DeliveryOptionId": cheapest["Id"],
            "DeliveryChargeExcludingSalesTax": cheapest["DeliveryChargeExcludingSalesTax"],
            "DeliveryChargeSalesTax": cheapest.get("DeliveryChargeSalesTax", 0),
        }

        resp = requests.post(
            f"{self.base_url}/api/v1/orders/confirmed",
            headers=self.headers,
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    def query_products(self, page: int = 1, page_size: int = 50) -> dict:
        """Query all products."""
        resp = requests.post(
            f"{self.base_url}/api/v1/products/query",
            headers=self.headers,
            json={"Page": page, "PageSize": page_size},
        )
        resp.raise_for_status()
        return resp.json()

    def get_product(self, product_id: int) -> dict:
        """Get product details with print options."""
        resp = requests.get(
            f"{self.base_url}/api/v1/products/{product_id}",
            headers=self.headers,
        )
        resp.raise_for_status()
        return resp.json()
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/jamest/art-generator && python -m pytest tests/print_shop/test_tps_client.py -v`
Expected: All 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add lambdas/print_shop/tps_client.py tests/print_shop/test_tps_client.py
git commit -m "feat(print-shop): add theprintspace API client"
```

---

## Task 8: SES email helper

**Files:**
- Create: `lambdas/print_shop/email.py`

- [ ] **Step 1: Write email helper**

```python
# lambdas/print_shop/email.py
"""SES email helpers for order confirmation and failure alerts."""

import boto3

SENDER = "art@jamestannahill.com"


def send_confirmation(to_email: str, artwork_title: str, edition_number: int, edition_limit: int, size_dims: str, artwork_url: str):
    """Send branded order confirmation to buyer."""
    subject = f"Your Limited Edition Print — {artwork_title}"
    body = f"""Thank you for your purchase.

Your print details:
- {artwork_title}
- Edition {edition_number} of {edition_limit}
- {size_dims}" on Hahnemühle German Etching 310gsm
- Certificate of Authenticity included

Your print is being prepared by our fine art print studio. Production typically takes 5-7 business days, after which it will be shipped to you with tracking.

View the original artwork: {artwork_url}

If you have any questions, reply to this email.

James Tannahill
art.jamestannahill.com
"""
    _send(to_email, subject, body)


def send_fulfillment_alert(order_id: str, error: str, customer_email: str, artwork_title: str):
    """Send alert to admin when fulfillment fails."""
    subject = f"ALERT: Print fulfillment failed — {order_id}"
    body = f"""Fulfillment failed for order {order_id}.

Customer: {customer_email}
Artwork: {artwork_title}
Error: {error}

The customer has been charged but the order was not submitted to theprintspace.
Manual intervention required — either retry the order or issue a refund.
"""
    _send(SENDER, subject, body)


def _send(to: str, subject: str, body: str):
    ses = boto3.client("ses", region_name="us-east-1")
    ses.send_email(
        Source=SENDER,
        Destination={"ToAddresses": [to]},
        Message={
            "Subject": {"Data": subject},
            "Body": {"Text": {"Data": body}},
        },
    )
```

- [ ] **Step 2: Commit**

```bash
git add lambdas/print_shop/email.py
git commit -m "feat(print-shop): add SES email helper for confirmations and alerts"
```

---

## Task 9: Stripe webhook handler

**Files:**
- Create: `lambdas/print_shop/stripe_webhook.py`
- Create: `tests/print_shop/test_stripe_webhook.py`

- [ ] **Step 1: Write stripe webhook tests**

```python
# tests/print_shop/test_stripe_webhook.py
from unittest.mock import MagicMock, patch, ANY
from decimal import Decimal
import json

from lambdas.print_shop.stripe_webhook import handle_checkout_completed


def _make_session(run_id="2026-03-16-130500", slug="arctic-70n-20w", size_key="L"):
    return {
        "id": "cs_test_123",
        "payment_intent": "pi_test_456",
        "metadata": {"run_id": run_id, "slug": slug, "size_key": size_key},
        "customer_details": {"email": "buyer@example.com"},
        "shipping_details": {
            "name": "John Doe",
            "address": {
                "line1": "123 Main St",
                "city": "London",
                "state": "",
                "postal_code": "SW1A 1AA",
                "country": "GB",
            },
            "phone": "+447700900000",
        },
    }


def test_idempotency_skips_duplicate(mock_table):
    """If SESSION# lookup item exists, return early."""
    mock_table.get_item.return_value = {"Item": {"PK": "SESSION#cs_test_123", "SK": "META", "order_id": "existing"}}

    result = handle_checkout_completed(mock_table, _make_session(), "test-tps-key")

    assert result["status"] == "already_processed"
    mock_table.update_item.assert_not_called()


@patch("lambdas.print_shop.stripe_webhook._fulfill_order")
def test_successful_order(mock_fulfill, mock_table, sample_edition_item):
    mock_table.get_item.return_value = {}  # No SESSION# item (not a duplicate)
    mock_table.update_item.return_value = {"Attributes": {"sizes": {"L": {"sold": Decimal("1")}}}}

    mock_fulfill.return_value = {"tps_order_id": 12345}

    result = handle_checkout_completed(mock_table, _make_session(), "test-tps-key")

    assert result["status"] == "success"
    assert result["edition_number"] == 1
    # Verify atomic increment was called
    mock_table.update_item.assert_called_once()
    update_args = mock_table.update_item.call_args[1]
    assert "sizes.#sk.sold" in update_args["UpdateExpression"]


@patch("lambdas.print_shop.stripe_webhook.stripe")
def test_refund_on_sold_out(mock_stripe, mock_table):
    """If edition sold out (conditional check fails), refund."""
    from botocore.exceptions import ClientError
    mock_table.get_item.return_value = {}  # No SESSION# item
    mock_table.update_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem"
    )

    result = handle_checkout_completed(mock_table, _make_session(), "test-tps-key")

    assert result["status"] == "refunded"
    mock_stripe.Refund.create.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/jamest/art-generator && python -m pytest tests/print_shop/test_stripe_webhook.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write stripe webhook implementation**

```python
# lambdas/print_shop/stripe_webhook.py
"""Stripe webhook handler — processes checkout.session.completed events."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import stripe
from botocore.exceptions import ClientError

from .email import send_confirmation, send_fulfillment_alert
from .tps_client import TpsClient


def handle_checkout_completed(table, session: dict, tps_api_key: str) -> dict:
    """Process a completed Stripe Checkout session.

    Handles: idempotency check, atomic edition increment, order creation, fulfillment.
    """
    session_id = session["id"]
    payment_intent = session.get("payment_intent", "")
    metadata = session.get("metadata", {})
    run_id = metadata["run_id"]
    slug = metadata["slug"]
    size_key = metadata["size_key"]
    customer_email = session.get("customer_details", {}).get("email", "")

    # Idempotency: check if we already processed this session via dedicated lookup item
    session_resp = table.get_item(Key={"PK": f"SESSION#{session_id}", "SK": "META"})
    if "Item" in session_resp and session_resp["Item"]:
        return {"status": "already_processed"}

    # Atomic edition increment
    edition_pk = f"EDITION#{run_id}#{slug}"
    try:
        update_resp = table.update_item(
            Key={"PK": edition_pk, "SK": "META"},
            UpdateExpression="SET sizes.#sk.sold = sizes.#sk.sold + :one",
            ConditionExpression="sizes.#sk.sold < sizes.#sk.#limit",
            ExpressionAttributeNames={"#sk": size_key, "#limit": "limit"},
            ExpressionAttributeValues={":one": Decimal("1")},
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            # Sold out — refund
            stripe.Refund.create(payment_intent=payment_intent)
            order_id = str(uuid.uuid4())
            table.put_item(Item={
                "PK": f"ORDER#{order_id}", "SK": "META",
                "stripe_session_id": session_id,
                "stripe_payment_intent": payment_intent,
                "status": "refunded",
                "artwork_run_id": run_id, "artwork_slug": slug,
                "size_key": size_key, "customer_email": customer_email,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            return {"status": "refunded"}
        raise

    # Read updated sold count for edition number
    updated_sizes = update_resp["Attributes"]["sizes"]
    edition_number = int(updated_sizes[size_key]["sold"])
    edition_limit = int(updated_sizes[size_key]["limit"])
    size_dims = str(updated_sizes[size_key]["dims"])
    price_cents = int(updated_sizes[size_key]["price_cents"])

    # Create ORDER item + SESSION lookup item for idempotency
    order_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    order_item = {
        "PK": f"ORDER#{order_id}", "SK": "META",
        "stripe_session_id": session_id,
        "stripe_payment_intent": payment_intent,
        "status": "paid",
        "customer_email": customer_email,
        "artwork_run_id": run_id,
        "artwork_slug": slug,
        "size_key": size_key,
        "edition_number": edition_number,
        "edition_limit": edition_limit,
        "price_cents": price_cents,
        "created_at": now,
    }
    table.put_item(Item=order_item)
    # Write SESSION lookup item (for idempotency check) and TPS_ORDER lookup (for webhook routing)
    table.put_item(Item={"PK": f"SESSION#{session_id}", "SK": "META", "order_id": order_id})

    # Fulfillment
    try:
        fulfill_result = _fulfill_order(
            table=table,
            tps_api_key=tps_api_key,
            run_id=run_id,
            slug=slug,
            size_key=size_key,
            session=session,
        )
        tps_order_id = fulfill_result["tps_order_id"]
        table.update_item(
            Key={"PK": f"ORDER#{order_id}", "SK": "META"},
            UpdateExpression="SET #s = :s, tps_order_id = :tid",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "fulfilling", ":tid": tps_order_id},
        )
        # Write TPS_ORDER lookup item for webhook routing
        table.put_item(Item={"PK": f"TPS_ORDER#{tps_order_id}", "SK": "META", "order_pk": f"ORDER#{order_id}"})
    except Exception as e:
        error_detail = str(e)
        table.update_item(
            Key={"PK": f"ORDER#{order_id}", "SK": "META"},
            UpdateExpression="SET #s = :s, error_detail = :err",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "fulfillment_failed", ":err": error_detail},
        )
        artwork_title = slug.replace("-", " ").title()
        send_fulfillment_alert(order_id, error_detail, customer_email, artwork_title)
        return {"status": "fulfillment_failed", "order_id": order_id, "error": error_detail}

    # Send confirmation email
    artwork_title = slug.replace("-", " ").title()
    artwork_url = f"https://art.jamestannahill.com/weather/{run_id}/{slug}/"
    try:
        send_confirmation(customer_email, artwork_title, edition_number, edition_limit, size_dims, artwork_url)
    except Exception as e:
        print(f"Confirmation email failed: {e}")  # Non-fatal

    return {"status": "success", "order_id": order_id, "edition_number": edition_number}


def _fulfill_order(table, tps_api_key: str, run_id: str, slug: str, size_key: str, session: dict) -> dict:
    """Register product with theprintspace (if needed) and create order.

    Returns: {"tps_order_id": int}
    """
    # Check for cached PRODUCT item
    product_pk = f"PRODUCT#{run_id}#{slug}"
    product_resp = table.get_item(Key={"PK": product_pk, "SK": "META"})
    product = product_resp.get("Item")

    client = TpsClient(api_key=tps_api_key)

    if not product:
        # Lazy product registration: upload artwork SVG to theprintspace,
        # query back to get product ID and print options per size.
        # NOTE: theprintspace product upload requires manual setup via their dashboard
        # for v1. The PRODUCT# item is pre-populated manually after uploading artwork
        # to theprintspace. See Task 15 Step 6 for the manual workflow.
        raise ValueError(f"Product not registered with theprintspace for {product_pk}. Upload artwork via theprintspace dashboard and create PRODUCT# item manually.")

    print_option_id = product["print_options"].get(size_key)
    if not print_option_id:
        raise ValueError(f"No print option for size {size_key} on product {product_pk}")

    # Look up COA print option (SpecialOrderType 11 = ClassicCOA)
    coa_print_option_id = product.get("coa_print_option_id")

    # Extract shipping address from Stripe session
    shipping = session.get("shipping_details", {})
    address = shipping.get("address", {})
    name_parts = shipping.get("name", "").split(" ", 1)
    first_name = name_parts[0] if name_parts else ""
    last_name = name_parts[1] if len(name_parts) > 1 else ""

    embryonic = client.create_embryonic_order(
        product_id=int(product["tps_product_id"]),
        print_option_id=int(print_option_id),
        first_name=first_name,
        last_name=last_name,
        email=session.get("customer_details", {}).get("email", ""),
        coa_print_option_id=int(coa_print_option_id) if coa_print_option_id else None,
        address={
            "line1": address.get("line1", ""),
            "line2": address.get("line2", ""),
            "town": address.get("city", ""),
            "county": address.get("state", ""),
            "post_code": address.get("postal_code", ""),
            "country_code": address.get("country", ""),
            "phone": shipping.get("phone", ""),
        },
    )

    confirmed = client.confirm_order(
        order_id=embryonic["Id"],
        delivery_options=embryonic.get("DeliveryOptions", []),
    )

    return {"tps_order_id": confirmed["Id"]}
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/jamest/art-generator && python -m pytest tests/print_shop/test_stripe_webhook.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add lambdas/print_shop/stripe_webhook.py tests/print_shop/test_stripe_webhook.py
git commit -m "feat(print-shop): add Stripe webhook handler with idempotency and fulfillment"
```

---

## Task 10: theprintspace webhook handler

**Files:**
- Create: `lambdas/print_shop/tps_webhook.py`
- Create: `tests/print_shop/test_tps_webhook.py`

- [ ] **Step 1: Write tps_webhook tests**

```python
# tests/print_shop/test_tps_webhook.py
import hashlib
import hmac
import json
from unittest.mock import MagicMock
from lambdas.print_shop.tps_webhook import handle_tps_webhook


def test_updates_order_on_dispatch(mock_table):
    mock_table.get_item.return_value = {"Item": {"PK": "TPS_ORDER#12345", "SK": "META", "order_pk": "ORDER#abc"}}

    body = json.dumps({
        "ApiWebhookKind": "OrderStateChanged",
        "Order": {"Id": 12345, "OrderState": "Dispatched", "TrackingNumber": "TRACK123"},
    })
    # Generate valid HMAC
    secret = "test-secret"
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha1).hexdigest()

    result = handle_tps_webhook(mock_table, body, sig, secret)

    assert result["status"] == "updated"
    mock_table.update_item.assert_called_once()
    update_args = mock_table.update_item.call_args[1]
    assert ":track" in update_args["ExpressionAttributeValues"]


def test_rejects_invalid_signature(mock_table):
    body = json.dumps({"ApiWebhookKind": "Test"})

    result = handle_tps_webhook(mock_table, body, "bad-sig", "test-secret")

    assert result["error"] == "invalid_signature"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/jamest/art-generator && python -m pytest tests/print_shop/test_tps_webhook.py -v`
Expected: FAIL

- [ ] **Step 3: Write tps_webhook implementation**

```python
# lambdas/print_shop/tps_webhook.py
"""theprintspace webhook handler — processes OrderStateChanged events."""

import hashlib
import hmac
import json


def handle_tps_webhook(table, body: str, signature: str, webhook_secret: str) -> dict:
    """Process a theprintspace webhook.

    Verifies HMAC-SHA1 signature, updates ORDER status on dispatch.
    """
    # Verify signature
    expected = hmac.new(webhook_secret.encode(), body.encode(), hashlib.sha1).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return {"error": "invalid_signature"}

    payload = json.loads(body)
    kind = payload.get("ApiWebhookKind")

    if kind == "Test":
        return {"status": "ok"}

    if kind != "OrderStateChanged":
        return {"status": "ignored", "kind": kind}

    order_data = payload.get("Order", {})
    tps_order_id = order_data.get("Id")
    order_state = order_data.get("OrderState", "")
    tracking = order_data.get("TrackingNumber", "")

    if not tps_order_id:
        return {"error": "missing_order_id"}

    # Find our ORDER item via TPS_ORDER lookup
    lookup_resp = table.get_item(Key={"PK": f"TPS_ORDER#{tps_order_id}", "SK": "META"})
    lookup = lookup_resp.get("Item")
    if not lookup:
        return {"error": "order_not_found", "tps_order_id": tps_order_id}

    order_pk = lookup["order_pk"]

    # Map theprintspace states to our statuses
    status_map = {
        "Dispatched": "dispatched",
        "Delivered": "delivered",
    }
    new_status = status_map.get(order_state)
    if not new_status:
        return {"status": "ignored", "order_state": order_state}

    update_expr = "SET #s = :s"
    expr_values = {":s": new_status}
    expr_names = {"#s": "status"}

    if tracking:
        update_expr += ", tracking_number = :track"
        expr_values[":track"] = tracking

    table.update_item(
        Key={"PK": order_pk, "SK": "META"},
        UpdateExpression=update_expr,
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )

    return {"status": "updated", "new_status": new_status}
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/jamest/art-generator && python -m pytest tests/print_shop/test_tps_webhook.py -v`
Expected: All 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add lambdas/print_shop/tps_webhook.py tests/print_shop/test_tps_webhook.py
git commit -m "feat(print-shop): add theprintspace webhook handler"
```

---

## Task 11: Lambda handler — router

**Files:**
- Create: `lambdas/print_shop/handler.py`
- Create: `lambdas/print_shop/requirements.txt`

- [ ] **Step 1: Write Lambda handler**

```python
# lambdas/print_shop/handler.py
"""Print Shop Lambda — routes to action handlers based on ?action= query param."""

import json
import os
from urllib.parse import parse_qs

import boto3
import stripe

TABLE_NAME = os.environ.get("TABLE_NAME", "art-generator")
BUCKET_NAME = os.environ.get("BUCKET_NAME", "art-generator-216890068001")

_table = None


def _get_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _table


def handler(event, context):
    """Lambda Function URL handler — routes by action query param."""
    qs = event.get("queryStringParameters") or {}
    action = qs.get("action", "")
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")

    try:
        if action == "editions":
            return _handle_editions(qs)
        elif action == "checkout" and method == "POST":
            return _handle_checkout(event, qs)
        elif action == "stripe_webhook" and method == "POST":
            return _handle_stripe_webhook(event)
        elif action == "tps_webhook" and method == "POST":
            return _handle_tps_webhook(event)
        else:
            return _response(400, {"error": "invalid_action"})
    except Exception as e:
        print(f"Error in {action}: {e}")
        return _response(500, {"error": "internal_error"})


def _handle_editions(qs):
    from .editions import get_editions
    run_id = qs.get("run_id", "")
    slug = qs.get("slug", "")
    if not run_id or not slug:
        return _response(400, {"error": "missing run_id or slug"})

    result = get_editions(_get_table(), run_id, slug)
    if result is None:
        return _response(404, {"error": "artwork_not_found"})
    return _response(200, result)


def _handle_checkout(event, qs):
    from .checkout import create_checkout_session
    from .secrets import get_secrets

    body = json.loads(event.get("body", "{}"))
    run_id = body.get("run_id", qs.get("run_id", ""))
    slug = body.get("slug", qs.get("slug", ""))
    size_key = body.get("size_key", qs.get("size_key", ""))

    if not all([run_id, slug, size_key]):
        return _response(400, {"error": "missing run_id, slug, or size_key"})

    secrets = get_secrets()
    result = create_checkout_session(
        table=_get_table(),
        stripe_key=secrets["stripe_secret_key"],
        run_id=run_id,
        slug=slug,
        size_key=size_key,
        base_url="https://art.jamestannahill.com",
    )

    if "error" in result:
        return _response(400 if result["error"] != "not_found" else 404, result)
    return _response(200, result)


def _handle_stripe_webhook(event):
    from .stripe_webhook import handle_checkout_completed
    from .secrets import get_secrets

    secrets = get_secrets()
    body = event.get("body", "")
    sig_header = (event.get("headers") or {}).get("stripe-signature", "")

    try:
        evt = stripe.Webhook.construct_event(body, sig_header, secrets["stripe_webhook_secret"])
    except (ValueError, stripe.error.SignatureVerificationError):
        return _response(400, {"error": "invalid_signature"})

    if evt["type"] == "checkout.session.completed":
        result = handle_checkout_completed(_get_table(), evt["data"]["object"], secrets["tps_api_key"])
        return _response(200, result)

    return _response(200, {"status": "ignored", "type": evt["type"]})


def _handle_tps_webhook(event):
    from .tps_webhook import handle_tps_webhook
    from .secrets import get_secrets

    secrets = get_secrets()
    body = event.get("body", "")
    sig = (event.get("headers") or {}).get("x-creativehub-signature", "")

    result = handle_tps_webhook(_get_table(), body, sig, secrets.get("tps_webhook_secret", secrets["tps_api_key"]))
    status_code = 200 if "error" not in result else 400
    return _response(status_code, result)


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }
```

- [ ] **Step 2: Write requirements.txt**

```
stripe>=8.0.0
requests>=2.31.0
```

- [ ] **Step 3: Commit**

```bash
git add lambdas/print_shop/handler.py lambdas/print_shop/requirements.txt
git commit -m "feat(print-shop): add Lambda handler router with all four actions"
```

---

## Task 12: CDK stack — print-shop Lambda + Secrets Manager

**Files:**
- Modify: `cdk/lib/art-generator-stack.ts:1-14,290-350`

- [ ] **Step 1: Add Secrets Manager import and print-shop Lambda to CDK stack**

At the top of the file (after line 12), add:
```typescript
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
```

After the API Lambda block (after line 349, before the closing `}`), add:

```typescript
    // === Print Shop Lambda ===
    const printShopSecret = secretsmanager.Secret.fromSecretNameV2(
      this, 'PrintShopSecret', 'art-generator/print-shop'
    );

    const printShopFn = new lambda.Function(this, 'PrintShopFn', {
      functionName: 'art-print-shop',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../lambdas/print_shop')),
      timeout: cdk.Duration.seconds(60),
      memorySize: 256,
      environment: {
        TABLE_NAME: table.tableName,
        BUCKET_NAME: bucket.bucketName,
        SECRET_ID: 'art-generator/print-shop',
      },
    });
    table.grantReadWriteData(printShopFn);
    bucket.grantRead(printShopFn);
    printShopSecret.grantRead(printShopFn);
    printShopFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['ses:SendEmail'],
      resources: ['*'],
    }));

    const printShopUrl = printShopFn.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.NONE,
      cors: {
        allowedOrigins: ['https://art.jamestannahill.com'],
        allowedMethods: [lambda.HttpMethod.GET, lambda.HttpMethod.POST],
        allowedHeaders: ['Content-Type'],
      },
    });

    new cdk.CfnOutput(this, 'PrintShopUrl', {
      value: printShopUrl.url,
      description: 'URL for the print shop Lambda',
    });
```

- [ ] **Step 2: Add PRINT_SHOP_URL env var to site-rebuild Lambda**

In the siteRebuild Lambda environment block (around line 173-177), add:
```typescript
        PRINT_SHOP_URL: printShopUrl.url,
```

Note: This requires moving the siteRebuild Lambda declaration after the printShopFn, or using a `CfnOutput` value. Since the stack is declarative, CDK handles ordering. However, the simplest approach is to add the env var as a separate statement after both are defined:

```typescript
    siteRebuild.addEnvironment('PRINT_SHOP_URL', printShopUrl.url);
```

- [ ] **Step 3: Update CF invalidation paths in site_rebuild**

In `lambdas/site_rebuild/handler.py`, line 386, change the invalidation paths:
```python
                "Paths": {
                    "Quantity": 4,
                    "Items": ["/index.html", "/weather/*", "/palettes/*", "/shop/*"],
                },
```

- [ ] **Step 4: Commit**

```bash
git add cdk/lib/art-generator-stack.ts lambdas/site_rebuild/handler.py
git commit -m "feat(cdk): add print-shop Lambda with Function URL, Secrets Manager, SES"
```

---

## Task 13: Frontend — drawer UI on artwork page

**Files:**
- Modify: `lambdas/site_rebuild/templates/weather_single.html:19-59,72-74,130-131,141-145`
- Modify: `lambdas/site_rebuild/templates/base.html:263`
- Modify: `lambdas/site_rebuild/handler.py:79-83`

- [ ] **Step 1: Add drawer CSS to base.html**

Before the closing `</style>` tag (line 263 of `base.html`), add:

```css
    /* Print shop drawer */
    .drawer-overlay { position:fixed; inset:0; background:rgba(0,0,0,0.6); z-index:100; opacity:0; pointer-events:none; transition:opacity 0.3s; }
    .drawer-overlay.open { opacity:1; pointer-events:auto; }
    .drawer { position:fixed; top:0; right:0; bottom:0; width:min(420px,90vw); background:#0d0d0d; border-left:1px solid #2a2a2a; z-index:101; transform:translateX(100%); transition:transform 0.3s; overflow-y:auto; padding:1.5rem; }
    .drawer.open { transform:translateX(0); }
    .drawer-close { position:absolute; top:1rem; right:1rem; background:none; border:none; color:#888; font-size:1.5rem; cursor:pointer; }
    .drawer-close:hover { color:#fff; }
    .drawer-thumb { width:100%; max-width:200px; display:block; margin:0 auto 1rem; border-radius:4px; }
    .drawer h3 { color:#fff; font-size:1.1rem; margin-bottom:0.3rem; }
    .drawer .subtitle { color:#888; font-size:0.85rem; margin-bottom:1.5rem; }
    .size-options { display:flex; flex-direction:column; gap:0.75rem; margin-bottom:1.5rem; }
    .size-option { display:flex; justify-content:space-between; align-items:center; padding:0.75rem 1rem; background:#141414; border:1px solid #2a2a2a; border-radius:6px; cursor:pointer; transition:border-color 0.2s; }
    .size-option:hover { border-color:#444; }
    .size-option.selected { border-color:#c4b5fd; background:#1a1a2e; }
    .size-option.sold-out { opacity:0.4; pointer-events:none; }
    .size-option .size-label { color:#fff; font-size:0.95rem; }
    .size-option .size-dims { color:#888; font-size:0.8rem; }
    .size-option .size-edition { color:#666; font-size:0.75rem; }
    .size-option .size-price { color:#c4b5fd; font-size:1rem; font-weight:600; }
    .sold-out-badge { background:#331111; color:#ff6b6b; font-size:0.7rem; padding:0.15rem 0.5rem; border-radius:3px; }
    .checkout-btn { display:block; width:100%; padding:0.85rem; background:linear-gradient(135deg,#1a1a3e,#2a1a4e); border:1px solid #444; border-radius:6px; color:#c4b5fd; font-size:1rem; font-weight:600; cursor:pointer; text-align:center; transition:background 0.2s; margin-bottom:1rem; }
    .checkout-btn:hover { background:linear-gradient(135deg,#2a2a5e,#3a2a6e); border-color:#666; }
    .checkout-btn:disabled { opacity:0.4; pointer-events:none; }
    .trust-signals { color:#666; font-size:0.75rem; line-height:1.8; }
    .trust-signals span { display:block; }
```

- [ ] **Step 2: Update weather_single.html — replace print CTA with drawer**

Replace the existing "Inquire About Prints" block (lines 141-145) with a "Buy Print" button:

```html
<div style="margin:1.5rem 0;">
  <button class="generate-btn" id="buy-print-btn" onclick="openPrintDrawer()">Buy Print</button>
</div>
```

Replace the hardcoded format in Technical Details (line 130-131):
```html
    <dt>Format</dt>
    <dd>SVG ({{ artwork.canvas_format|default('2048x2048') }} viewBox)</dd>
```

Update Schema.org width/height (lines 29-30 in the schema block):
```html
  "width": {"@type": "Distance", "name": "{{ artwork.canvas_format|default('2048x2048') | replace('x', 'x') }}px"},
  "height": {"@type": "Distance", "name": "{{ artwork.canvas_format|default('2048x2048') | replace('x', 'x') }}px"},
```

Actually, split the canvas_format into width/height:
```html
  {% set dims = (artwork.canvas_format|default('2048x2048')).split('x') %}
  "width": {"@type": "Distance", "name": "{{ dims[0] }}px"},
  "height": {"@type": "Distance", "name": "{{ dims[1] }}px"},
```

- [ ] **Step 3: Add drawer HTML and JS to weather_single.html**

At the end of `{% block content %}`, before `{% endblock %}`, add:

```html
<!-- Print drawer -->
<div class="drawer-overlay" id="drawer-overlay" onclick="closePrintDrawer()"></div>
<div class="drawer" id="print-drawer">
  <button class="drawer-close" onclick="closePrintDrawer()">&times;</button>
  <img class="drawer-thumb" src="/weather/{{ run_id }}/{{ slug }}/artwork.svg" alt="{{ slug }}">
  <h3>{{ slug|replace('-', ' ')|title }}</h3>
  <p class="subtitle">{{ artwork.artist|default('sam_francis')|replace('_', ' ')|title }} — {{ date }}</p>
  <div class="size-options" id="size-options">
    <p style="color:#666;">Loading sizes...</p>
  </div>
  <button class="checkout-btn" id="checkout-btn" disabled onclick="checkout()">Select a size</button>
  <div class="trust-signals">
    <span>Ships worldwide via theprintspace</span>
    <span>Hahnemühle German Etching 310gsm</span>
    <span>Certificate of Authenticity included</span>
  </div>
</div>
```

Add the JS in `{% block scripts %}`:

```html
{% block scripts %}
<script>
var PRINT_SHOP_URL = '{{ print_shop_url }}';
var RUN_ID = '{{ run_id }}';
var SLUG = '{{ slug }}';
var selectedSize = null;

function openPrintDrawer() {
  document.getElementById('drawer-overlay').classList.add('open');
  document.getElementById('print-drawer').classList.add('open');
  loadEditions();
}

function closePrintDrawer() {
  document.getElementById('drawer-overlay').classList.remove('open');
  document.getElementById('print-drawer').classList.remove('open');
}

function loadEditions() {
  fetch(PRINT_SHOP_URL + '?action=editions&run_id=' + RUN_ID + '&slug=' + SLUG)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var container = document.getElementById('size-options');
      container.innerHTML = '';
      var order = ['S','M','L','XL','XXL'];
      order.forEach(function(key) {
        var s = data.sizes[key];
        if (!s) return;
        var soldOut = s.sold >= s.limit;
        var div = document.createElement('div');
        div.className = 'size-option' + (soldOut ? ' sold-out' : '');
        div.setAttribute('data-size', key);
        div.setAttribute('data-price', s.price_cents);
        div.onclick = function() { selectSize(key, s.price_cents); };
        div.innerHTML =
          '<div>' +
            '<div class="size-label">' + key + ' — ' + s.dims + '"</div>' +
            '<div class="size-edition">' + s.sold + ' of ' + s.limit + ' sold</div>' +
          '</div>' +
          '<div>' +
            (soldOut ? '<span class="sold-out-badge">Sold Out</span>' :
            '<span class="size-price">$' + (s.price_cents / 100).toFixed(0) + '</span>') +
          '</div>';
        container.appendChild(div);
      });
    })
    .catch(function(e) {
      document.getElementById('size-options').innerHTML = '<p style="color:#ff6b6b;">Failed to load sizes</p>';
    });
}

function selectSize(key, priceCents) {
  selectedSize = key;
  document.querySelectorAll('.size-option').forEach(function(el) {
    el.classList.toggle('selected', el.getAttribute('data-size') === key);
  });
  var btn = document.getElementById('checkout-btn');
  btn.disabled = false;
  btn.textContent = 'Checkout — $' + (priceCents / 100).toFixed(0);
}

function checkout() {
  if (!selectedSize) return;
  var btn = document.getElementById('checkout-btn');
  btn.disabled = true;
  btn.textContent = 'Redirecting...';
  fetch(PRINT_SHOP_URL + '?action=checkout', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({run_id: RUN_ID, slug: SLUG, size_key: selectedSize}),
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    if (data.url) {
      window.location.href = data.url;
    } else {
      btn.textContent = data.error === 'sold_out' ? 'Sold Out' : 'Error — try again';
      setTimeout(function() { btn.disabled = false; btn.textContent = 'Checkout'; }, 3000);
    }
  })
  .catch(function() {
    btn.disabled = false;
    btn.textContent = 'Error — try again';
  });
}
</script>
{% endblock %}
```

- [ ] **Step 4: Pass print_shop_url and canvas_format to template in site_rebuild**

In `lambdas/site_rebuild/handler.py`, around line 79-83 where individual artwork pages are rendered, update:

```python
        for artwork in artworks:
            slug = artwork.get("SK", artwork.get("slug", ""))
            pages[f"site/weather/{date}/{slug}/index.html"] = env.get_template(
                "weather_single.html"
            ).render(
                artwork=artwork,
                date=date,
                slug=slug,
                print_shop_url=os.environ.get("PRINT_SHOP_URL", ""),
            )
```

- [ ] **Step 5: Commit**

```bash
git add lambdas/site_rebuild/templates/base.html lambdas/site_rebuild/templates/weather_single.html lambdas/site_rebuild/handler.py
git commit -m "feat(frontend): add print shop drawer UI with size picker and Stripe checkout"
```

---

## Task 14: Shop success and cancel pages

**Files:**
- Create: `lambdas/site_rebuild/templates/shop_success.html`
- Create: `lambdas/site_rebuild/templates/shop_cancel.html`
- Modify: `lambdas/site_rebuild/handler.py`

- [ ] **Step 1: Write shop_success.html**

```html
{% extends "base.html" %}

{% block title %}Order Confirmed — art.jt{% endblock %}
{% block canonical_path %}/shop/success/{% endblock %}
{% block meta %}<meta name="robots" content="noindex">{% endblock %}

{% block content %}
<div style="max-width:600px; margin:3rem auto; text-align:center;">
  <h1 style="margin-bottom:1rem;">Your Print is Being Prepared</h1>
  <div id="order-details" style="background:#111; border:1px solid #2a2a2a; border-radius:8px; padding:1.5rem; margin:1.5rem 0; text-align:left;">
    <p style="color:#888; margin-bottom:0.5rem;">Loading order details...</p>
  </div>
  <p style="color:#888; line-height:1.8;">
    Production typically takes 5-7 business days.<br>
    You will receive a confirmation email with tracking information.<br>
    Printed on Hahnemühle German Etching 310gsm with Certificate of Authenticity.
  </p>
  <a href="/" class="download-btn" style="margin-top:1.5rem;">Back to Gallery</a>
</div>

<script>
(function() {
  var params = new URLSearchParams(window.location.search);
  var title = (params.get('title') || '').replace(/-/g, ' ').replace(/\b\w/g, function(l) { return l.toUpperCase(); });
  var size = params.get('size') || '';
  var el = document.getElementById('order-details');
  if (title) {
    el.innerHTML =
      '<p style="color:#fff; font-size:1.1rem; margin-bottom:0.5rem;">' + title + '</p>' +
      '<p style="color:#c4b5fd;">Size: ' + size + ' — Limited Edition</p>';
  }
})();
</script>
{% endblock %}
```

- [ ] **Step 2: Write shop_cancel.html**

```html
{% extends "base.html" %}

{% block title %}Checkout Cancelled — art.jt{% endblock %}
{% block canonical_path %}/shop/cancel/{% endblock %}
{% block meta %}<meta name="robots" content="noindex">{% endblock %}

{% block content %}
<div style="max-width:600px; margin:3rem auto; text-align:center;">
  <h1 style="margin-bottom:1rem;">No Worries</h1>
  <p style="color:#888; line-height:1.8; margin-bottom:1.5rem;">
    Your checkout was cancelled. The edition is still available if you change your mind.
  </p>
  <a href="/" class="download-btn">Back to Gallery</a>
</div>
{% endblock %}
```

- [ ] **Step 3: Add shop pages to site_rebuild handler**

In `lambdas/site_rebuild/handler.py`, after the about/privacy/terms rendering block (around line 280), add:

```python
    # Render shop pages
    pages["site/shop/success/index.html"] = env.get_template("shop_success.html").render()
    pages["site/shop/cancel/index.html"] = env.get_template("shop_cancel.html").render()
```

- [ ] **Step 4: Commit**

```bash
git add lambdas/site_rebuild/templates/shop_success.html lambdas/site_rebuild/templates/shop_cancel.html lambdas/site_rebuild/handler.py
git commit -m "feat(frontend): add shop success and cancel pages"
```

---

## Task 15: Run all tests + deploy

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/jamest/art-generator && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Create Secrets Manager secret (manual)**

```bash
aws secretsmanager create-secret \
  --name art-generator/print-shop \
  --secret-string '{"stripe_secret_key":"sk_test_...","stripe_webhook_secret":"whsec_...","tps_api_key":"<key>"}'
```

- [ ] **Step 3: CDK deploy**

Run: `cd /Users/jamest/art-generator/cdk && npx cdk deploy --require-approval never`
Expected: Stack deploys with new PrintShopFn Lambda + Function URL output

- [ ] **Step 4: Run backfill script**

Run: `cd /Users/jamest/art-generator && python scripts/backfill_canvas_format.py`
Expected: All WEATHER# items updated with canvas_format, PNGs re-rendered

- [ ] **Step 5: Trigger site rebuild**

Run: `curl -s "$(aws cloudformation describe-stacks --stack-name ArtGeneratorStack --query 'Stacks[0].Outputs[?OutputKey==\`TriggerUrl\`].OutputValue' --output text)?artist=sam_francis"`
Or manually invoke the site-rebuild Lambda to regenerate pages with drawer UI.

- [ ] **Step 6: Smoke test**

1. Visit an artwork page — confirm "Buy Print" button appears
2. Click "Buy Print" — confirm drawer opens with correct sizes for the artwork's aspect ratio
3. Select a size — confirm price updates
4. Click "Checkout" — confirm redirect to Stripe (use test mode)
5. Complete test payment — confirm webhook processes, ORDER item created in DynamoDB
6. Visit `/shop/success/` — confirm page renders with order details

- [ ] **Step 7: Configure Stripe webhook in dashboard**

In Stripe Dashboard > Developers > Webhooks, add endpoint:
- URL: `<PrintShopUrl>?action=stripe_webhook`
- Events: `checkout.session.completed`
- Copy webhook signing secret to Secrets Manager

- [ ] **Step 8: Configure theprintspace webhook**

In theprintspace dashboard > API Keys > Webhook endpoint:
- URL: `<PrintShopUrl>?action=tps_webhook`

- [ ] **Step 9: Final commit**

```bash
git add -A
git commit -m "feat(print-shop): complete print shop v1 — Stripe + theprintspace + tiered editions"
```
