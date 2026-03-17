# Print Shop — art.jamestannahill.com

## Overview

Add limited-edition print sales to the art generator. Users browse the existing gallery, click "Buy Print" on any artwork page, select a size from a slide-out drawer, and check out via Stripe. Fulfillment is handled by theprintspace (white-label giclée printing on Hahnemühle German Etching 310gsm). Every print ships with a certificate of authenticity. Edition sizes are tiered by print size — larger prints are rarer and more expensive.

## Architecture

Single new Lambda (`art-print-shop`) with Function URL, four actions:
- `?action=editions` — returns edition data for an artwork
- `?action=checkout` — creates Stripe Checkout Session, returns redirect URL
- `?action=stripe_webhook` — receives Stripe payment webhooks
- `?action=tps_webhook` — receives theprintspace fulfillment webhooks

No API Gateway, no new DynamoDB tables, no CloudFront changes. The Lambda is called directly from client-side JS on the artwork page. Webhook actions are called server-to-server by Stripe and theprintspace respectively.

### Fulfillment: theprintspace (CreativeHub API)

- **API base:** `https://api.creativehub.io`
- **Auth:** `Authorization: ApiKey <tps_api_key>` (key stored in Secrets Manager)
- **Sandbox:** `https://api.sandbox.tps-test.io`
- **Paper:** Hahnemühle German Etching 310gsm (100% cotton, no OBAs, museum-grade)
- **Image source:** SVG artwork from S3 — theprintspace accepts vector files and rasterizes at print resolution, avoiding DPI limitations of raster uploads. Fallback: high-res PNG rendered on demand at target print DPI.

Order flow:
1. `POST /api/v1/orders/embryonic` — create draft order with artwork product ID, print option, shipping address
2. Select cheapest delivery option from response
3. `POST /api/v1/orders/confirmed` — finalize with chosen delivery option
4. theprintspace sends `OrderStateChanged` webhook on dispatch with tracking number

Products are registered with theprintspace on first purchase (lazy registration). The Lambda uploads the artwork file, then queries `POST /api/v1/products/query` to retrieve the product ID and available print options. The returned `product_id` and `print_option_ids` per size are cached in a `PRODUCT#` DynamoDB item.

### Payment: Stripe Checkout (hosted)

- Stripe Checkout Session created by Lambda with line item details
- Session metadata includes: `run_id`, `slug`, `size_key`
- Stripe collects payment + shipping address
- Shipping: Stripe Checkout `shipping_address_collection` enabled with all theprintspace-supported countries. Shipping cost is included in the print price (flat-rate baked in) for v1 simplicity — avoids needing a two-step flow to query theprintspace shipping costs before creating the session.
- Webhook `checkout.session.completed` triggers order fulfillment
- Stripe handles payment receipts automatically

### Order confirmation email

Stripe receipts only show charge amount. On successful order creation, the Lambda sends a branded confirmation email via SES to the buyer with: artwork title, edition number (e.g., "Edition 8 of 50"), print size, paper details, estimated production time, and a link back to the artwork page. Sent from `art@jamestannahill.com`.

### Secrets Management

Stripe secret key, Stripe webhook secret, and theprintspace API key stored in AWS Secrets Manager. Lambda reads on cold start, caches in memory. Not stored as plaintext env vars.

Secret path: `art-generator/print-shop`
```json
{
  "stripe_secret_key": "sk_live_...",
  "stripe_webhook_secret": "whsec_...",
  "tps_api_key": "<tps_api_key>"
}
```

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

**EDITION item lifecycle:** The `?action=editions` endpoint creates the EDITION item lazily on first read (not first purchase). It reads the `canvas_format` from the WEATHER# item, determines the aspect ratio, populates default tiers, and writes to DynamoDB. Subsequent reads return the cached item. This means the drawer always has data to display.

**Prerequisite:** The `canvas_format` backfill script (see Infrastructure section) must complete before the print shop goes live. If `canvas_format` is missing from a WEATHER# item, the editions endpoint falls back to parsing the viewBox from the SVG in S3.

The `sold` count is incremented atomically via DynamoDB conditional update:
```
UpdateExpression: SET sizes.#sk.sold = sizes.#sk.sold + :one
ConditionExpression: sizes.#sk.sold < sizes.#sk.#limit
```
The edition number assigned is `sold + 1` (read the updated value from the response's `Attributes`).

### ORDER items

```
PK: ORDER#{order_id}
SK: META
```

`order_id` is a UUID4, generated by the Lambda when creating the ORDER item.

Attributes:
- `stripe_session_id` — Stripe Checkout Session ID (used for idempotency check)
- `stripe_payment_intent` — for refund capability
- `tps_order_id` — theprintspace order ID (set after embryonic order created)
- `status` — `paid` | `fulfilling` | `dispatched` | `delivered` | `refunded` | `fulfillment_failed`
- `customer_email` — from Stripe session
- `artwork_run_id` — e.g., `2026-03-16-130500`
- `artwork_slug` — e.g., `arctic-70n-20w`
- `size_key` — S/M/L/XL/XXL
- `edition_number` — e.g., 8
- `edition_limit` — e.g., 50
- `price_cents` — amount paid
- `tracking_number` — set on dispatch
- `error_detail` — set on fulfillment_failed, contains error message
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

1. **User clicks "Buy Print"** on artwork page — slide-out drawer opens from right
2. **Drawer loads edition data** — JS fetches `?action=editions&run_id={run_id}&slug={slug}` from print-shop Lambda
3. **Drawer displays sizes** — radio/card selector showing dimensions, edition status ("12 of 50 sold"), price. Sold-out sizes greyed with badge.
4. **User picks size, clicks "Checkout — $X"** — JS POSTs to `?action=checkout` with `run_id`, `slug`, `size_key`
5. **Lambda creates Stripe Checkout Session:**
   - Sets `metadata`: `run_id`, `slug`, `size_key`
   - Sets `shipping_address_collection` with supported countries
   - Sets `success_url` with `{CHECKOUT_SESSION_ID}` template variable
   - Sets `cancel_url` back to artwork page
   - Returns Stripe checkout URL
6. **User redirected to Stripe hosted checkout** — enters payment + shipping address
7. **On successful payment**, Stripe fires `checkout.session.completed` webhook to `?action=stripe_webhook`
8. **Lambda processes Stripe webhook:**
   - Verifies Stripe signature via `stripe.Webhook.construct_event()`
   - **Idempotency check:** queries DynamoDB for existing ORDER with this `stripe_session_id`. If found, returns 200 (already processed). Prevents duplicate orders on Stripe retries.
   - Reads session metadata for `run_id`, `slug`, `size_key`
   - **Atomic edition increment:** conditional DynamoDB update on EDITION item (`sizes.{size_key}.sold < sizes.{size_key}.limit`). If condition fails (sold out), issues Stripe refund via `payment_intent` and creates ORDER with status `refunded`. Returns 200.
   - On success: edition_number = updated `sold` value. Creates `ORDER#` item (status: `paid`)
   - **Fulfillment:** checks for `PRODUCT#` item — if missing, uploads artwork to theprintspace, queries product/print options, caches in PRODUCT# item
   - Creates theprintspace embryonic order with product ID, print option ID, shipping address (from Stripe session), COA line item (SpecialOrderType: 11)
   - Selects cheapest delivery option from embryonic response
   - Confirms order via `POST /api/v1/orders/confirmed`
   - Updates ORDER status to `fulfilling` with `tps_order_id`
   - **On fulfillment failure:** if any theprintspace API call fails, sets ORDER status to `fulfillment_failed` with `error_detail`. Sends alert email via SES to `art@jamestannahill.com` with order ID, error, and customer email for manual intervention. Does NOT refund automatically — allows manual retry.
   - **Sends confirmation email** via SES to buyer (see Order confirmation email section)
9. **theprintspace prints and ships** — fires `OrderStateChanged` webhook to `?action=tps_webhook`
10. **Lambda verifies** `X-Creativehub-Signature` (HMAC-SHA1), updates ORDER status to `dispatched`, stores tracking number
11. **Stripe sends payment receipt email automatically**

### Race condition handling

Edition reservation happens at step 8 (payment confirmed), not at checkout creation. This avoids the need for reservation cleanup on abandoned checkouts.

Risk: two users could start checkout for the last edition simultaneously — one gets refunded after payment. Given the volume (low), this is acceptable for v1.

### Refund on race condition

If the atomic increment at step 8 fails (edition sold out between checkout creation and payment), the Lambda immediately issues a Stripe refund via the `payment_intent` and returns 200 to Stripe. The ORDER item is created with status `refunded`.

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

- `/shop/success/` — Stripe `success_url` includes non-sensitive summary params: `?session_id={CHECKOUT_SESSION_ID}&edition={edition_number}&size={size_key}&title={slug}`. The page renders these directly — no API callback needed. Edition number and size are not sensitive data.
- `/shop/cancel/` — "No worries" + link back to artwork

### No /shop/ index for v1

Discovery through existing gallery. Featured badges drive visibility.

## Infrastructure (CDK)

### New Lambda: `art-print-shop`

- Runtime: Python 3.12
- Memory: 256 MB
- Timeout: 60 seconds (theprintspace API calls + possible image upload need headroom)
- Function URL with CORS (`art.jamestannahill.com`). Note: webhook actions are called server-to-server without Origin header, so CORS does not apply to them.
- IAM: DynamoDB read/write on `art-generator` table, S3 read on artwork bucket, Secrets Manager read, SES send (for confirmation + alert emails)
- Dependencies: `stripe`, `requests`, `boto3`

### Secrets Manager

New secret `art-generator/print-shop` — see Secrets Management section above.

### weather_render changes

- Refactor `build_art_prompt()` to return the chosen `(width, height)` tuple alongside the prompt text, so the handler can include `canvas_format` in both the metadata dict and the DynamoDB item write
- Store `canvas_format` (e.g., `"2048x1440"`) in DynamoDB item metadata and metadata.json
- Fix `render_png()`: pass only `output_width` to CairoSVG and omit `output_height` — CairoSVG will calculate height from the SVG viewBox aspect ratio automatically

### Backfill script (blocking prerequisite)

One-time script that **must complete before the print shop goes live**. Requires CairoSVG (run locally or as a Lambda with the CairoSVG layer):
1. Scan all `WEATHER#` items in DynamoDB
2. For each, read the SVG from S3, parse viewBox dimensions
3. Write `canvas_format` attribute back to the DynamoDB item
4. Re-render both `preview-2048.png` and `preview-4k.png` with correct aspect ratio (existing non-square artworks have incorrectly squished PNGs due to the `output_height=width` bug)

### site_rebuild changes

- `weather_single.html` — drawer markup + JS, "Buy Print" button replacing mailto CTA
- `weather_single.html` — fix hardcoded `SVG (2048x2048 viewBox)` in Technical Details to use actual `canvas_format`
- `weather_single.html` — fix Schema.org VisualArtwork `width`/`height` to use actual canvas dimensions
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
- **Auth header:** `Authorization: ApiKey <tps_api_key>` (from Secrets Manager)
- **Sandbox for testing:** `https://api.sandbox.tps-test.io`

### Key endpoints used

| Action | Method | Path |
|--------|--------|------|
| Query products | POST | `/api/v1/products/query` |
| Get product | GET | `/api/v1/products/{id}` |
| Create embryonic order | POST | `/api/v1/orders/embryonic` |
| Confirm order | POST | `/api/v1/orders/confirmed` |
| Get order | GET | `/api/v1/orders/{id}` |

### Product registration (lazy)

On first purchase of an artwork, the Lambda uploads the artwork file to theprintspace, then queries products to find the newly created product ID and its available print options (matching Hahnemühle German Etching at each target size). The product ID and print option IDs per size are cached in a `PRODUCT#` DynamoDB item. Subsequent orders for the same artwork skip this step.

### Edition certificates

Added as a line item with `SpecialOrderType: 11` (ClassicCOA) on every order.

### Webhook verification

theprintspace webhooks include `X-Creativehub-Signature` header (HMAC-SHA1). Configured on the API key in their dashboard, pointing to the print-shop Lambda Function URL with `?action=tps_webhook`.

## Pricing Notes

- Prices are retail in USD
- theprintspace charges production cost + 20% dropship surcharge
- Shipping cost baked into print price (flat-rate) for v1 simplicity
- Margin varies by size — larger sizes have higher absolute margin
- Stripe fees: 2.9% + $0.30 per transaction

## Prerequisites Checklist

1. **canvas_format backfill** — run backfill script to populate all existing WEATHER# items with `canvas_format` and re-render PNGs
2. **Stripe account** — create Stripe account, get API keys, configure webhook endpoint
3. **SES domain verification** — verify `jamestannahill.com` in SES for sending confirmation/alert emails. If account is in SES sandbox, request production access.
4. **theprintspace sandbox testing** — validate product upload + order flow against sandbox API before going live
5. **Secrets Manager** — create `art-generator/print-shop` secret with all three keys
6. **Featured artworks** — manually set `featured: true` on select EDITION items via DynamoDB console (admin endpoint is v2)

## v2 Considerations (out of scope for v1)

- Admin dashboard / order list endpoint
- Dynamic shipping cost calculation (two-step checkout)
- Branded confirmation email templates (HTML)
- Stripe webhook for failed/disputed payments
- Multiple fulfillment partners (e.g., Prodigi for US)
