# art.jt API — Product Specification

## Overview

Productize the art generator's ML models and data pipelines as a paid API. Five endpoints, tiered pricing, API key auth, Stripe billing, hosted documentation.

**Domain:** `api.art.jamestannahill.com`

---

## Endpoints

### 1. Art Critic API — `POST /v1/critique`

Score any image on composition, color harmony, complexity, and emotional impact.

**Request:**
```json
{
  "image": "<base64-encoded PNG/JPG>",
  "image_url": "https://example.com/artwork.png"  // alternative to base64
}
```
One of `image` or `image_url` required. Max image size: 5MB. Accepted: PNG, JPG, WebP.

**Response:**
```json
{
  "composition": 8,
  "color": 7,
  "complexity": 9,
  "impact": 8,
  "overall": 8,
  "critique": "A commanding abstract piece with sophisticated layering and strong visual rhythm.",
  "request_id": "req_abc123"
}
```

**Cost:** $0.02/request | Bedrock Haiku vision | ~1.5s latency

---

### 2. Weather Drama API — `GET /v1/weather/rankings`

Get today's ranked global locations by atmospheric visual interest.

**Query params:** `?limit=10` (default 10, max 20)

**Response:**
```json
{
  "date": "2026-03-19",
  "rankings": [
    {
      "rank": 1,
      "lat": 70.0,
      "lng": 20.0,
      "score": 43.8,
      "pressure_hpa": 990.1,
      "wind_speed_kmh": 60.2,
      "temp_c": 3.5,
      "humidity_pct": 90,
      "precipitation_mm": 0.2,
      "region": "Arctic Norway"
    }
  ],
  "model": "GFS (NOAA)",
  "scan_points": 54
}
```

**Additional routes:**
- `GET /v1/weather/forecast` — Tomorrow's predicted top 20
- `GET /v1/weather/score?lat=X&lng=Y` — Score a specific location (fetches live GFS data)

**Cost:** $0.005/request | DynamoDB read (rankings/forecast) or Open-Meteo + scoring (score) | <200ms for cached, ~2s for live

---

### 3. Weather-to-Art API — `POST /v1/generate`

Generate original SVG artwork from any coordinates in any artist style.

**Request:**
```json
{
  "lat": 64.9,
  "lng": -18.5,
  "artist": "mark_rothko",
  "format": "png",
  "width": 2048
}
```

**Params:**
- `lat`, `lng` — required, any global coordinates
- `artist` — optional, one of: `sam_francis`, `gerhard_richter`, `hilma_af_klint`, `wassily_kandinsky`, `helen_frankenthaler`, `piet_mondrian`, `yayoi_kusama`, `mark_rothko`, `bridget_riley`, `kazimir_malevich`, `lesley_tannahill`. Default: random.
- `format` — `svg` (default) or `png`
- `width` — PNG width in pixels (512-4096, default 2048). Ignored for SVG.

**Response:**
```json
{
  "svg": "<svg viewBox=\"0 0 2048 2048\">...</svg>",
  "png_url": "https://api.art.jamestannahill.com/renders/req_abc123.png",
  "rationale": "Over the Icelandic highlands, moderate winds and...",
  "weather": {
    "pressure_hpa": 1002.3,
    "wind_speed_ms": 12.4,
    "temp_k": 271.2,
    "humidity_pct": 88,
    "precipitation_mm": 0.1
  },
  "quality_score": 7,
  "request_id": "req_abc123"
}
```

**Cost:** $1.00/request | Bedrock Sonnet + CairoSVG render + art critic | ~15-30s latency

---

### 4. Satellite Palette API — `GET /v1/palette`

Get the dominant color palette for any location from Sentinel-2 satellite imagery.

**Query params:** `?lat=X&lng=Y`

**Response:**
```json
{
  "lat": -24.7,
  "lng": 15.3,
  "location": "Namib Desert",
  "colors": ["#C4956A", "#8B6642", "#D4A574", "#5C3D2E", "#E8C9A0"],
  "mood": "Vast ochre dunes under pale sky — the mineral warmth of deep geological time.",
  "source": "Copernicus Sentinel-2 L2A",
  "capture_date": "2026-03-15",
  "request_id": "req_abc123"
}
```

**Cost:** $0.01/request | Sentinel-2 API + Pillow quantization + Bedrock Haiku mood | ~5s latency (cache hit <200ms)

---

### 5. Dynamic Pricing API — `POST /v1/price`

Compute a scarcity-based price multiplier from quality, rarity, and demand signals.

**Request:**
```json
{
  "quality_score": 8,
  "rarity_score": 72,
  "total_supply": 25,
  "total_sold": 8
}
```

**Response:**
```json
{
  "multiplier": 1.46,
  "components": {
    "quality_bonus": 0.30,
    "rarity_bonus": 0.13,
    "scarcity_bonus": 0.03
  },
  "suggested_action": "Price above base — high quality + moderate rarity",
  "request_id": "req_abc123"
}
```

**Cost:** $0.005/request | Pure computation, no external calls | <50ms latency

---

## Pricing Tiers

| Tier | Price | Art Critic | Weather Drama | Weather-to-Art | Palette | Dynamic Pricing |
|------|-------|-----------|---------------|----------------|---------|----------------|
| **Free** | $0/mo | 50/mo | 100/mo | — | 50/mo | 100/mo |
| **Starter** | $29/mo | 1,000/mo | 2,000/mo | 10/mo | 500/mo | 5,000/mo |
| **Pro** | $99/mo | 5,000/mo | 10,000/mo | 100/mo | 2,000/mo | 25,000/mo |
| **Scale** | $299/mo | 25,000/mo | 50,000/mo | 500/mo | 10,000/mo | 100,000/mo |
| **Enterprise** | Custom | Custom | Custom | Custom | Custom | Custom |

Overage: per-request pricing at 1.5× the per-unit rate.

---

## Architecture

```
Client → api.art.jamestannahill.com (CloudFront)
       → API Gateway (REST API, regional)
          ├── Usage Plans (Free/Starter/Pro/Scale) with API Keys
          ├── /v1/critique      → art-api-critique Lambda
          ├── /v1/weather/*     → art-api-weather Lambda
          ├── /v1/generate      → art-api-generate Lambda
          ├── /v1/palette       → art-api-palette Lambda
          └── /v1/price         → art-api-price Lambda
```

### AWS Resources
- **API Gateway** — REST API with usage plans, API key auth, throttling
- **CloudFront** — custom domain `api.art.jamestannahill.com`, edge caching for weather/palette
- **5 Lambda functions** — one per endpoint group, Python 3.12
- **DynamoDB** — `art-api-keys` table (PK=api_key, customer_id, tier, stripe_sub_id)
- **S3** — `art-api-renders` bucket for temporary PNG storage (Weather-to-Art output, 24h TTL)
- **Stripe** — subscription billing, webhook for provisioning/deprovisioning API keys
- **ACM** — cert for api.art.jamestannahill.com

### Authentication
- API key in `x-api-key` header (standard API Gateway key auth)
- Keys provisioned automatically on Stripe subscription
- Usage tracked per-key by API Gateway usage plans
- Rate limiting: 10 req/s (Free), 50 req/s (Starter), 100 req/s (Pro), 500 req/s (Scale)

### Caching
- Weather rankings: CloudFront 5min TTL (data refreshes daily)
- Weather forecast: CloudFront 1hr TTL
- Satellite palettes: CloudFront 24hr TTL (imagery refreshes weekly)
- Art Critic + Generate + Price: no caching (unique per request)

---

## API Documentation Site

Hosted at `api.art.jamestannahill.com/docs` — static HTML generated alongside the main site.

**Pages:**
- `/docs/` — Overview, authentication, rate limits, error codes
- `/docs/critique` — Art Critic endpoint reference + examples
- `/docs/weather` — Weather Drama endpoint reference + examples
- `/docs/generate` — Weather-to-Art endpoint reference + examples
- `/docs/palette` — Satellite Palette endpoint reference + examples
- `/docs/price` — Dynamic Pricing endpoint reference + examples
- `/docs/quickstart` — cURL examples, Python SDK snippet, Node.js snippet

**Error format (all endpoints):**
```json
{
  "error": {
    "code": "rate_limit_exceeded",
    "message": "You've exceeded 10 requests/second. Upgrade to Starter for higher limits.",
    "request_id": "req_abc123"
  }
}
```

**HTTP status codes:**
- 200 — Success
- 400 — Bad request (missing params, invalid image)
- 401 — Missing or invalid API key
- 403 — Endpoint not included in your tier
- 429 — Rate limit exceeded
- 500 — Internal error

---

## Billing Flow

1. User signs up at `api.art.jamestannahill.com/docs`
2. Selects tier → Stripe Checkout
3. Webhook provisions API key in DynamoDB + API Gateway usage plan
4. Key emailed to user via SES
5. Monthly billing via Stripe subscription
6. Overage calculated at billing cycle end
7. Cancellation → key deactivated, usage plan removed

---

## Cost Model

| Endpoint | Our cost/req | Price/req (Pro) | Margin |
|----------|-------------|----------------|--------|
| Art Critic | $0.0006 | $0.020 | 33× |
| Weather Rankings | $0.0001 | $0.005 | 50× |
| Weather-to-Art | $0.064 | $1.00 | 16× |
| Satellite Palette | $0.001 | $0.010 | 10× |
| Dynamic Pricing | $0.00001 | $0.005 | 500× |

**Break-even at Pro tier ($99/mo):** ~1,500 Art Critic requests (cost ~$0.90) + overhead. Everything above is margin.

---

## MVP Scope

Phase 1 (ship first):
1. Art Critic API — highest demand, simplest to build
2. Weather Drama API — reads existing DynamoDB data
3. Dynamic Pricing API — pure computation
4. API docs site
5. Free + Starter tiers only

Phase 2:
6. Weather-to-Art API — most complex (Sonnet + render)
7. Satellite Palette API — needs Sentinel-2 integration
8. Pro + Scale tiers
9. Stripe overage billing
10. Python/Node SDK packages
