# Print Shop — art.jamestannahill.com

## Overview

Add limited-edition print sales to the art generator. Users browse the existing gallery, click "Buy Print" on any artwork page, select a size from a slide-out drawer, and check out via Stripe. Fulfillment is handled by theprintspace (white-label giclée printing on Hahnemühle German Etching 310gsm). Every print ships with a certificate of authenticity. Edition sizes are tiered by print size — larger prints are rarer and more expensive.

## Architecture

Single new Lambda (`art-print-shop`) with Function URL, three actions:
- `?action=editions` — returns edition data for an artwork
- `?action=checkout` — creates Stripe Checkout Session, returns redirect URL
- `?action=webhook` — receives Stripe + theprintspace webhooks

No API Gateway, no new DynamoDB tables, no CloudFront changes. The Lambda is called directly from client-side JS on the artwork page.

### Fulfillment: theprintspace (CreativeHub API)

- **API base:** `https://api.creativehub.io`
- **Auth:** `Authorization: ApiKey <key>`
- **Sandbox:** `https://api.sandbox.tps-test.io`
- **Paper:** Hahnemühle German Etching 310gsm (100% cotton, no OBAs, museum-grade)
- **Image source:** `preview-4k.png` from S3 (must fix render_png to respect aspect ratio)

Order flow:
1. `POST /api/v1/orders/embryonic` — create draft order with artwork product ID, print option, shipping address
2. Select cheapest delivery option from response
3. `POST /api/v1/orders/confirmed` — finalize with chosen delivery option
4. theprintspace sends `OrderStateChanged` webhook on dispatch with tracking number

Products are uploaded to theprintspace on first purchase (lazy registration). The returned `product_id` and `print_option_ids` per size are cached in a `PRODUCT#` DynamoDB item.

### Payment: Stripe Checkout (hosted)

- Stripe Checkout Session created by Lambda with line item details
- Session metadata includes: artwork PK, size key, edition number
- Stripe collects payment + shipping address
- Webhook `checkout.session.completed` triggers order fulfillment
- Stripe handles receipts automatically

### Secrets Management

Stripe secret key, Stripe webhook secret, and theprintspace API key stored in AWS Secrets Manager. Lambda reads on cold start, caches in memory. Not stored as plaintext env vars.

## Data Model

New item patterns in existing `art-generator` DynamoDB table (PAY_PER_REQUEST, no GSIs needed):

### EDITION items

```
PK: EDITION#{run_id}#{slug}
SK: META
```

Attributes:
- `canvas_format` — e.g., `"2048x2048"`, `"2560x1440"`
- `aspect_ratio` — e.g., `"1:1"`, `"16:9"`, `"2:1"`, `"3:2"`, `"9:16"`, `"1:2"`
- `featured` — boolean, manually set
- `sizes` — map of size_key to edition info:
  ```json
  {
    "S":    {"dims": "12x12", "limit": 100, "sold": 0, "price_cents": 9500},
    "M":    {"dims": "20x20", "limit": 75,  "sold": 0, "price_cents": 19500},
    "L":    {"dims": "30x30", "limit": 50,  "sold": 0, "price_cents": 35000},
    "XL":   {"dims": "40x40", "limit": 25,  "sold": 0, "price_cents": 59500},
    "XXL":  {"dims": "60x60", "limit": 10,  "sold": 0, "price_cents": 120000}
  }
  ```

EDITION items are created on first purchase with default tiers based on aspect ratio. The `sold` count is incremented atomically via DynamoDB conditional update.

### ORDER items

```
PK: ORDER#{order_id}
SK: META
```

Attributes:
- `stripe_session_id` — Stripe Checkout Session ID
- `stripe_payment_intent` — for refund capability
- `tps_order_id` — theprintspace order ID (set after embryonic order created)
- `status` — `paid` → `fulfilling` → `dispatched` → `delivered`
- `customer_email` — from Stripe session
- `artwork_pk` — `WEATHER#{run_id}`
- `artwork_sk` — slug
- `size_key` — S/M/L/XL/XXL
- `edition_number` — e.g., 8
- `edition_limit` — e.g., 50
- `price_cents` — amount paid
- `tracking_number` — set on dispatch
- `created_at` — ISO 8601

### PRODUCT items

```
PK: PRODUCT#{run_id}#{slug}
SK: META
```

Attributes:
- `tps_product_id` — theprintspace product ID
- `print_options` — map of size_key to `print_option_id`
- `uploaded_at` — ISO 8601

## Canvas Formats & Size Tiers

Seven canvas formats map to six aspect ratios:

| Format (viewBox) | Aspect Ratio | Category |
|-----------------|-------------|----------|
| 2048x2048 | 1:1 | Square |
| 1920x1920 | 1:1 | Square |
| 2560x1440 | 16:9 | Landscape |
| 2400x1600 | 3:2 | Golden Landscape |
| 2048x1024 | 2:1 | Panoramic Wide |
| 1440x2560 | 9:16 | Tall Portrait |
| 1024x2048 | 1:2 | Tall Panoramic |

### Square (1:1)

| Size | Dims | Edition Limit | Price |
|------|------|---------------|-------|
| S | 12x12" | /100 | $95 |
| M | 20x20" | /75 | $195 |
| L | 30x30" | /50 | $350 |
| XL | 40x40" | /25 | $595 |
| XXL | 60x60" | /10 | $1,200 |

### Landscape (16:9)

| Size | Dims | Edition Limit | Price |
|------|------|---------------|-------|
| S | 16x9" | /100 | $85 |
| M | 24x14" | /75 | $175 |
| L | 36x20" | /50 | $325 |
| XL | 48x27" | /25 | $550 |
| XXL | 60x34" | /10 | $995 |

### Golden Landscape (3:2)

| Size | Dims | Edition Limit | Price |
|------|------|---------------|-------|
| S | 12x8" | /100 | $85 |
| M | 18x12" | /75 | $175 |
| L | 24x16" | /50 | $325 |
| XL | 36x24" | /25 | $550 |
| XXL | 50x34" | /10 | $995 |

### Panoramic Wide (2:1)

| Size | Dims | Edition Limit | Price |
|------|------|---------------|-------|
| S | 20x10" | /100 | $95 |
| M | 30x15" | /75 | $195 |
| L | 40x20" | /50 | $375 |
| XL | 50x25" | /25 | $650 |
| XXL | 60x30" | /10 | $1,100 |

### Tall Portrait (9:16)

| Size | Dims | Edition Limit | Price |
|------|------|---------------|-------|
| S | 9x16" | /100 | $85 |
| M | 14x24" | /75 | $175 |
| L | 20x36" | /50 | $325 |
| XL | 27x48" | /25 | $550 |
| XXL | 34x60" | /10 | $995 |

### Tall Panoramic (1:2)

| Size | Dims | Edition Limit | Price |
|------|------|---------------|-------|
| S | 10x20" | /100 | $95 |
| M | 15x30" | /75 | $195 |
| L | 20x40" | /50 | $375 |
| XL | 25x50" | /25 | $650 |
| XXL | 30x60" | /10 | $1,100 |

## Order Flow

1. **User clicks "Buy Print"** on artwork page → slide-out drawer opens from right
2. **Drawer loads edition data** — JS fetches `?action=editions&pk=WEATHER#{run_id}#{slug}` from print-shop Lambda
3. **Drawer displays sizes** — radio/card selector showing dimensions, edition status ("12 of 50 sold"), price. Sold-out sizes greyed with badge.
4. **User picks size, clicks "Checkout — $X"** → JS POSTs to `?action=checkout` with artwork PK, slug, size key
5. **Lambda validates:**
   - Edition not sold out (DynamoDB conditional check)
   - Reserves edition number (atomic increment)
   - Creates Stripe Checkout Session with metadata (artwork PK, slug, size key, edition number)
   - Returns Stripe checkout URL
6. **User redirected to Stripe hosted checkout** → enters payment + shipping address
7. **On successful payment**, Stripe fires `checkout.session.completed` webhook → `?action=webhook`
8. **Lambda processes webhook:**
   - Verifies Stripe signature
   - Reads session metadata for artwork + size + edition
   - Creates `ORDER#` item (status: `paid`)
   - Checks for `PRODUCT#` item — if missing, uploads 4K PNG to theprintspace, caches product/print option IDs
   - Creates theprintspace embryonic order with product ID, print option ID, shipping address (from Stripe), COA line item
   - Selects cheapest delivery option from response
   - Confirms order via theprintspace API
   - Updates ORDER status to `fulfilling` with `tps_order_id`
9. **theprintspace prints, frames (if applicable), ships** → fires `OrderStateChanged` webhook
10. **Lambda updates ORDER** status to `dispatched`, stores tracking number
11. **Stripe sends receipt email automatically**

### Race condition handling

Edition reservation happens at step 5 (checkout creation), not step 8 (payment). If the user abandons Stripe checkout, the reservation expires after 30 minutes (Stripe session expiry). A cleanup mechanism (either on next editions query or a scheduled check) decrements `sold` for expired unpaid sessions.

Alternative: reserve at step 8 (payment confirmed) only. Simpler — no cleanup needed. Risk: two users could start checkout for the last edition simultaneously, one gets refunded. Given the volume (low), this is acceptable for v1.

**Recommendation: Reserve at payment (step 8).** Simpler, and the refund-on-race scenario is extremely unlikely at current scale.

### Refund on race condition

If the atomic increment at step 8 fails (edition sold out between checkout creation and payment), the Lambda immediately issues a Stripe refund via the `payment_intent` and returns an error. The ORDER item is created with status `refunded`.

## Frontend

### Drawer (slide-out panel)

Replaces the existing "Inquire About Prints" mailto CTA on `weather_single.html`.

Contents (top to bottom):
- Close button (X, top-right)
- Artwork thumbnail (small SVG)
- Title + artist + date
- Size selector — cards showing: dimensions, edition status, price
- Sold-out sizes greyed with "Sold Out" badge
- Selected size highlights with total
- **"Checkout — $350"** button
- Trust signals: "Ships worldwide" / "Hahnemühle German Etching 310gsm" / "Certificate of Authenticity included"

Click outside drawer or X to close. Dark theme matching existing styles (#0a0a0a bg, #c4b5fd accent, monospace).

### Featured badge

Artworks with `featured: true` in their EDITION item get a subtle badge on gallery cards (artist pages, archive, homepage). On the artwork page itself, the "Buy Print" CTA is more prominent — sticky footer bar instead of inline button.

### New static pages

- `/shop/success/` — "Your limited edition print is being prepared" + edition details from URL params
- `/shop/cancel/` — "No worries" + link back to artwork

### No /shop/ index for v1

Discovery through existing gallery. Featured badges drive visibility.

## Infrastructure (CDK)

### New Lambda: `art-print-shop`

- Runtime: Python 3.12
- Memory: 256 MB
- Timeout: 30 seconds
- Function URL with CORS (`art.jamestannahill.com`)
- IAM: DynamoDB read/write on `art-generator` table, S3 read on artwork bucket, Secrets Manager read
- Dependencies: `stripe`, `requests`, `boto3`

### Secrets Manager

New secret `art-generator/print-shop`:
```json
{
  "stripe_secret_key": "sk_live_...",
  "stripe_webhook_secret": "whsec_...",
  "tps_api_key": "production-c0LgKw6gt0wgyWxYcmXyGrk2thltB5r6"
}
```

### weather_render changes

- Store `canvas_format` (e.g., `"2048x1440"`) in DynamoDB item metadata
- Fix `render_png()` to respect viewBox aspect ratio instead of forcing square output

### Backfill script

One-time script:
1. Scan all `WEATHER#` items in DynamoDB
2. For each, read the SVG from S3, parse viewBox dimensions
3. Write `canvas_format` attribute back to the DynamoDB item
4. Optionally re-render PNGs with correct aspect ratio

### site_rebuild changes

- `weather_single.html` — drawer markup + JS, "Buy Print" button replacing mailto CTA
- New templates: `shop_success.html`, `shop_cancel.html`
- Print-shop Lambda Function URL injected as template variable
- Featured badge on gallery cards where applicable

### No changes to

- EventBridge schedule
- Step Functions
- CloudFront distribution
- Existing Lambdas (except weather_render + site_rebuild)

## theprintspace API Integration Details

- **Base URL:** `https://api.creativehub.io`
- **Auth header:** `Authorization: ApiKey production-c0LgKw6gt0wgyWxYcmXyGrk2thltB5r6`
- **Sandbox for testing:** `https://api.sandbox.tps-test.io`

### Key endpoints used

| Action | Method | Path |
|--------|--------|------|
| List products | POST | `/api/v1/products/query` |
| Get product | GET | `/api/v1/products/{id}` |
| Create embryonic order | POST | `/api/v1/orders/embryonic` |
| Confirm order | POST | `/api/v1/orders/confirmed` |
| Get order | GET | `/api/v1/orders/{id}` |

### Edition certificates

Added as a line item with `SpecialOrderType: 11` (ClassicCOA) on every order.

### Webhook verification

theprintspace webhooks include `X-Creativehub-Signature` header (HMAC-SHA1). Configured on the API key in their dashboard, pointing to the print-shop Lambda Function URL with `?action=tps_webhook`.

## Pricing Notes

- Prices are retail in USD
- theprintspace charges production cost + 20% dropship surcharge
- Shipping passed through to buyer at actual cost (shown during Stripe checkout)
- Margin varies by size — larger sizes have higher absolute margin
- Stripe fees: 2.9% + $0.30 per transaction
