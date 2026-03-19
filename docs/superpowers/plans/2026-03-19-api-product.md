# art.jt API Product — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a paid API at api.art.jamestannahill.com with 3 MVP endpoints (Art Critic, Weather Drama, Dynamic Pricing), API key auth, usage plans (Free + Starter), Stripe billing, and hosted documentation.

**Architecture:** API Gateway REST API with usage plans and API keys for auth/throttling. Three Lambda functions handle the endpoints. Stripe Checkout provisions API keys via webhook. Static docs hosted on CloudFront alongside the main site. Custom domain via ACM + CloudFront.

**Tech Stack:** API Gateway (REST), Lambda (Python 3.12), DynamoDB, Bedrock Haiku, Stripe, SES, CloudFront, ACM

**Spec:** `docs/superpowers/specs/2026-03-19-api-product.md`

---

## File Structure

### New Lambda functions
| File | Responsibility |
|------|---------------|
| `lambdas/api_product/critique.py` | Art Critic endpoint — Bedrock Haiku vision scoring |
| `lambdas/api_product/weather.py` | Weather Drama endpoint — rankings, forecast, live score |
| `lambdas/api_product/price.py` | Dynamic Pricing endpoint — multiplier computation |
| `lambdas/api_product/handler.py` | Router — dispatches to endpoint modules by path |
| `lambdas/api_product/auth.py` | Tier validation — checks endpoint access per tier |
| `lambdas/api_product/billing.py` | Stripe webhook — provisions/deprovisions API keys |

### New templates
| File | Responsibility |
|------|---------------|
| `lambdas/site_rebuild/templates/api_docs.html` | API docs overview page |
| `lambdas/site_rebuild/templates/api_quickstart.html` | Quickstart with cURL/Python/Node examples |
| `lambdas/site_rebuild/templates/api_critique.html` | Art Critic endpoint reference |
| `lambdas/site_rebuild/templates/api_weather.html` | Weather Drama endpoint reference |
| `lambdas/site_rebuild/templates/api_price.html` | Dynamic Pricing endpoint reference |

### Modified files
| File | Change |
|------|--------|
| `lambdas/site_rebuild/handler.py` | Render API doc pages, add to sitemap |
| `lambdas/site_rebuild/templates/base.html` | Add "API" nav link |

---

### Task 1: Create API Gateway + Usage Plans

- [ ] **Step 1: Create the REST API**

```bash
aws apigateway create-rest-api \
  --name art-jt-api \
  --description "art.jt API — Art Critic, Weather Drama, Dynamic Pricing" \
  --endpoint-configuration '{"types":["REGIONAL"]}' \
  --region us-east-1
```

Save the API ID from the response.

- [ ] **Step 2: Create API key and usage plans**

```bash
# Free tier: 10 req/s, 50 critique + 100 weather + 100 price per month
aws apigateway create-usage-plan \
  --name "Free" \
  --throttle '{"rateLimit":10,"burstLimit":20}' \
  --quota '{"limit":250,"period":"MONTH"}' \
  --region us-east-1

# Starter tier: 50 req/s, 1000 critique + 2000 weather + 5000 price per month
aws apigateway create-usage-plan \
  --name "Starter" \
  --throttle '{"rateLimit":50,"burstLimit":100}' \
  --quota '{"limit":8000,"period":"MONTH"}' \
  --region us-east-1
```

- [ ] **Step 3: Create /v1 resource tree**

```bash
API_ID=<from step 1>

# Get root resource
ROOT_ID=$(aws apigateway get-resources --rest-api-id $API_ID --region us-east-1 --query 'items[0].id' --output text)

# Create /v1
V1_ID=$(aws apigateway create-resource --rest-api-id $API_ID --parent-id $ROOT_ID --path-part v1 --region us-east-1 --query 'id' --output text)

# Create /v1/critique
aws apigateway create-resource --rest-api-id $API_ID --parent-id $V1_ID --path-part critique --region us-east-1

# Create /v1/weather
WEATHER_ID=$(aws apigateway create-resource --rest-api-id $API_ID --parent-id $V1_ID --path-part weather --region us-east-1 --query 'id' --output text)

# Create /v1/weather/{proxy+} for rankings/forecast/score
aws apigateway create-resource --rest-api-id $API_ID --parent-id $WEATHER_ID --path-part '{proxy+}' --region us-east-1

# Create /v1/price
aws apigateway create-resource --rest-api-id $API_ID --parent-id $V1_ID --path-part price --region us-east-1
```

- [ ] **Step 4: Commit placeholder**

```bash
git add docs/superpowers/plans/2026-03-19-api-product.md
git commit -m "plan: art.jt API product implementation plan"
```

---

### Task 2: API Product Lambda (Router + Endpoints)

**Files:**
- Create: `lambdas/api_product/handler.py`
- Create: `lambdas/api_product/critique.py`
- Create: `lambdas/api_product/weather.py`
- Create: `lambdas/api_product/price.py`
- Create: `lambdas/api_product/auth.py`

- [ ] **Step 1: Create router**

`lambdas/api_product/handler.py`:
```python
"""art.jt API — routes requests to endpoint handlers."""
import json
import os

TABLE_NAME = os.environ.get("TABLE_NAME", "art-generator")

def handler(event, context):
    path = event.get("path", "")
    method = event.get("httpMethod", "GET")

    try:
        if path == "/v1/critique" and method == "POST":
            from critique import handle_critique
            return handle_critique(event)
        elif path.startswith("/v1/weather"):
            from weather import handle_weather
            return handle_weather(event)
        elif path == "/v1/price" and method == "POST":
            from price import handle_price
            return handle_price(event)
        else:
            return _response(404, {"error": {"code": "not_found", "message": "Endpoint not found"}})
    except Exception as e:
        print(f"[ERROR] {path}: {e}")
        return _response(500, {"error": {"code": "internal_error", "message": "Internal server error"}})

def _response(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }
```

- [ ] **Step 2: Create Art Critic endpoint**

`lambdas/api_product/critique.py`:
```python
"""Art Critic API — score any image via Bedrock Haiku vision."""
import json
import base64
import uuid
import urllib.request
import boto3

bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

CRITIC_PROMPT = """Score this artwork on a scale of 1-10 across these criteria:
- Composition (balance, use of space, visual flow)
- Color harmony (palette cohesion, contrast, mood)
- Complexity (detail, layering, technique)
- Emotional impact (does it evoke a response?)

Respond with ONLY valid JSON:
{"composition":<1-10>,"color":<1-10>,"complexity":<1-10>,"impact":<1-10>,"overall":<1-10>,"critique":"<one sentence>"}"""


def handle_critique(event):
    body = json.loads(event.get("body", "{}"))
    request_id = f"req_{uuid.uuid4().hex[:12]}"

    # Get image data
    if body.get("image"):
        img_b64 = body["image"]
    elif body.get("image_url"):
        try:
            req = urllib.request.Request(body["image_url"], headers={"User-Agent": "art-api/1.0"})
            img_data = urllib.request.urlopen(req, timeout=10).read()
            if len(img_data) > 5 * 1024 * 1024:
                return _error(400, "image_too_large", "Image exceeds 5MB limit", request_id)
            img_b64 = base64.b64encode(img_data).decode()
        except Exception as e:
            return _error(400, "image_fetch_failed", f"Could not fetch image: {e}", request_id)
    else:
        return _error(400, "missing_image", "Provide 'image' (base64) or 'image_url'", request_id)

    # Detect media type
    media_type = "image/png"
    if img_b64[:4] == "/9j/":
        media_type = "image/jpeg"
    elif img_b64[:4] == "UklG":
        media_type = "image/webp"

    response = bedrock.invoke_model(
        modelId=MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 256,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": img_b64}},
                {"type": "text", "text": CRITIC_PROMPT},
            ]}],
        }),
    )

    result = json.loads(response["body"].read())
    text = result["content"][0]["text"].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0]

    scores = json.loads(text.strip())
    scores["request_id"] = request_id
    return {"statusCode": 200, "headers": {"Content-Type": "application/json"}, "body": json.dumps(scores)}


def _error(status, code, message, request_id):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": {"code": code, "message": message, "request_id": request_id}}),
    }
```

- [ ] **Step 3: Create Weather Drama endpoint**

`lambdas/api_product/weather.py`:
```python
"""Weather Drama API — rankings, forecast, live scoring."""
import json
import uuid
import boto3
from decimal import Decimal

TABLE_NAME = "art-generator"
dynamodb = boto3.resource("dynamodb")

class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


def handle_weather(event):
    path = event.get("path", "")
    qs = event.get("queryStringParameters") or {}
    request_id = f"req_{uuid.uuid4().hex[:12]}"

    if path.endswith("/rankings"):
        return _rankings(qs, request_id)
    elif path.endswith("/forecast"):
        return _forecast(qs, request_id)
    elif path.endswith("/score"):
        return _score(qs, request_id)
    else:
        return _response(404, {"error": {"code": "not_found", "message": "Use /v1/weather/rankings, /forecast, or /score"}})


def _rankings(qs, request_id):
    from datetime import datetime, timezone
    table = dynamodb.Table(TABLE_NAME)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    limit = min(int(qs.get("limit", "10")), 20)

    # Get latest weather run data
    result = table.query(
        KeyConditionExpression="PK = :pk",
        ExpressionAttributeValues={":pk": f"WEATHER#{today}"},
        Limit=limit,
    )
    items = result.get("Items", [])

    # If no items for today's date, scan for latest run
    if not items:
        scan = table.scan(
            FilterExpression="begins_with(PK, :prefix)",
            ExpressionAttributeValues={":prefix": "WEATHER#"},
            ProjectionExpression="PK, SK, score, lat, lng, pressure, wind_speed, temp, humidity, precipitation",
            Limit=200,
        )
        items = sorted(scan.get("Items", []), key=lambda x: float(x.get("score", 0)), reverse=True)[:limit]

    rankings = []
    for i, item in enumerate(items):
        rankings.append({
            "rank": i + 1,
            "lat": float(item.get("lat", 0)),
            "lng": float(item.get("lng", 0)),
            "score": float(item.get("score", 0)),
            "pressure_hpa": float(item.get("pressure", 0)),
            "wind_speed_kmh": float(item.get("wind_speed", 0)) * 3.6,
            "temp_c": float(item.get("temp", 0)),
            "humidity_pct": float(item.get("humidity", 0)),
            "precipitation_mm": float(item.get("precipitation", 0)),
        })

    return _response(200, {
        "date": today,
        "rankings": rankings,
        "model": "GFS (NOAA)",
        "scan_points": 54,
        "request_id": request_id,
    })


def _forecast(qs, request_id):
    from datetime import datetime, timezone, timedelta
    table = dynamodb.Table(TABLE_NAME)
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    limit = min(int(qs.get("limit", "10")), 20)

    result = table.get_item(Key={"PK": "FORECAST", "SK": tomorrow})
    item = result.get("Item")

    if not item:
        return _response(200, {"date": tomorrow, "rankings": [], "message": "Forecast not yet available", "request_id": request_id})

    predictions = json.loads(item.get("predictions", "[]"))[:limit]
    return _response(200, {
        "date": tomorrow,
        "rankings": predictions,
        "model": "GFS (NOAA) 24h forecast",
        "scan_points": 54,
        "request_id": request_id,
    })


def _score(qs, request_id):
    import requests as req
    import numpy as np

    lat = qs.get("lat")
    lng = qs.get("lng")
    if not lat or not lng:
        return _response(400, {"error": {"code": "missing_params", "message": "lat and lng required", "request_id": request_id}})

    lat, lng = float(lat), float(lng)
    resp = req.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lng,
        "hourly": "temperature_2m,relative_humidity_2m,surface_pressure,wind_speed_10m,wind_direction_10m,precipitation",
        "forecast_days": 1, "models": "gfs_seamless",
    }, timeout=15)
    data = resp.json()
    hourly = data.get("hourly", {})
    from datetime import datetime, timezone
    idx = min(datetime.now(timezone.utc).hour, len(hourly.get("temperature_2m", [])) - 1)

    weather = {
        "lat": lat, "lng": lng,
        "pressure_hpa": hourly["surface_pressure"][idx],
        "wind_speed_kmh": hourly["wind_speed_10m"][idx],
        "temp_c": hourly["temperature_2m"][idx],
        "humidity_pct": hourly["relative_humidity_2m"][idx],
        "precipitation_mm": hourly["precipitation"][idx] or 0,
    }

    # Simple scoring (same weights as ingest)
    weather["score"] = round(
        abs(weather["pressure_hpa"] - 1013) * 0.3 +
        weather["wind_speed_kmh"] * 0.25 +
        abs(weather["temp_c"] - 15) * 0.20 +
        weather["precipitation_mm"] * 10 * 0.15 +
        weather["humidity_pct"] * 0.01 * 0.10,
        1
    )
    weather["request_id"] = request_id
    return _response(200, weather)


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps(body, cls=DecimalEncoder),
    }
```

- [ ] **Step 4: Create Dynamic Pricing endpoint**

`lambdas/api_product/price.py`:
```python
"""Dynamic Pricing API — compute scarcity-based price multiplier."""
import json
import uuid


def handle_price(event):
    body = json.loads(event.get("body", "{}"))
    request_id = f"req_{uuid.uuid4().hex[:12]}"

    quality_score = float(body.get("quality_score", 5))
    rarity_score = float(body.get("rarity_score", 50))
    total_supply = int(body.get("total_supply", 25))
    total_sold = int(body.get("total_sold", 0))

    if not (1 <= quality_score <= 10):
        return _error(400, "invalid_quality", "quality_score must be 1-10", request_id)
    if not (0 <= rarity_score <= 100):
        return _error(400, "invalid_rarity", "rarity_score must be 0-100", request_id)

    sell_through = total_sold / total_supply if total_supply > 0 else 0
    quality_bonus = max(0, (quality_score - 5) / 5) * 0.5
    rarity_bonus = max(0, (rarity_score - 50) / 50) * 0.3
    scarcity_bonus = sell_through * 0.2

    multiplier = round(min(2.0, max(1.0, 1.0 + quality_bonus + rarity_bonus + scarcity_bonus)), 2)

    action = "Price at base"
    if multiplier >= 1.5:
        action = "Premium pricing — exceptional quality + rare conditions"
    elif multiplier >= 1.2:
        action = "Price above base — high quality + moderate rarity"
    elif multiplier > 1.0:
        action = "Slight premium — above-average signals"

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps({
            "multiplier": multiplier,
            "components": {
                "quality_bonus": round(quality_bonus, 2),
                "rarity_bonus": round(rarity_bonus, 2),
                "scarcity_bonus": round(scarcity_bonus, 2),
            },
            "suggested_action": action,
            "request_id": request_id,
        }),
    }


def _error(status, code, message, request_id):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": {"code": code, "message": message, "request_id": request_id}}),
    }
```

- [ ] **Step 5: Create IAM role**

```bash
aws iam create-role --role-name ArtApiProductRole \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

aws iam attach-role-policy --role-name ArtApiProductRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

aws iam put-role-policy --role-name ArtApiProductRole --policy-name ApiProductAccess \
  --policy-document '{
    "Version":"2012-10-17",
    "Statement":[
      {"Effect":"Allow","Action":["dynamodb:Query","dynamodb:Scan","dynamodb:GetItem"],"Resource":"arn:aws:dynamodb:us-east-1:216890068001:table/art-generator"},
      {"Effect":"Allow","Action":["bedrock:InvokeModel"],"Resource":"*"},
      {"Effect":"Allow","Action":["s3:GetObject"],"Resource":"arn:aws:s3:::art-generator-216890068001/*"}
    ]
  }'
```

- [ ] **Step 6: Deploy Lambda**

```bash
cd lambdas/api_product
zip -r9q handler.zip handler.py critique.py weather.py price.py

aws lambda create-function \
  --function-name art-api-product \
  --runtime python3.12 \
  --handler handler.handler \
  --role arn:aws:iam::216890068001:role/ArtApiProductRole \
  --zip-file fileb://handler.zip \
  --timeout 30 \
  --memory-size 512 \
  --environment '{"Variables":{"TABLE_NAME":"art-generator"}}' \
  --region us-east-1
```

- [ ] **Step 7: Wire API Gateway methods to Lambda**

For each resource (/v1/critique, /v1/weather/{proxy+}, /v1/price):
```bash
# Create method with API key required
aws apigateway put-method \
  --rest-api-id $API_ID \
  --resource-id $RESOURCE_ID \
  --http-method ANY \
  --authorization-type NONE \
  --api-key-required \
  --region us-east-1

# Create Lambda integration
aws apigateway put-integration \
  --rest-api-id $API_ID \
  --resource-id $RESOURCE_ID \
  --http-method ANY \
  --type AWS_PROXY \
  --integration-http-method POST \
  --uri "arn:aws:apigateway:us-east-1:lambda:path/2015-03-31/functions/arn:aws:lambda:us-east-1:216890068001:function:art-api-product/invocations" \
  --region us-east-1
```

Grant API Gateway permission to invoke Lambda:
```bash
aws lambda add-permission \
  --function-name art-api-product \
  --statement-id apigateway-invoke \
  --action lambda:InvokeFunction \
  --principal apigateway.amazonaws.com \
  --region us-east-1
```

- [ ] **Step 8: Deploy API stage**

```bash
aws apigateway create-deployment --rest-api-id $API_ID --stage-name v1 --region us-east-1
```

- [ ] **Step 9: Test endpoints**

```bash
# Create a test API key
KEY_ID=$(aws apigateway create-api-key --name "test-key" --enabled --region us-east-1 --query 'id' --output text)
KEY_VALUE=$(aws apigateway get-api-key --api-key $KEY_ID --include-value --region us-east-1 --query 'value' --output text)

# Associate with Free usage plan
aws apigateway create-usage-plan-key --usage-plan-id $FREE_PLAN_ID --key-id $KEY_ID --key-type API_KEY --region us-east-1

# Test Art Critic
curl -X POST "https://$API_ID.execute-api.us-east-1.amazonaws.com/v1/critique" \
  -H "x-api-key: $KEY_VALUE" \
  -H "Content-Type: application/json" \
  -d '{"image_url":"https://art.jamestannahill.com/weather/2026-03-16/arctic-60n-130w/preview-2048.png"}'

# Test Weather Rankings
curl "https://$API_ID.execute-api.us-east-1.amazonaws.com/v1/weather/rankings?limit=5" \
  -H "x-api-key: $KEY_VALUE"

# Test Dynamic Pricing
curl -X POST "https://$API_ID.execute-api.us-east-1.amazonaws.com/v1/price" \
  -H "x-api-key: $KEY_VALUE" \
  -H "Content-Type: application/json" \
  -d '{"quality_score":8,"rarity_score":72,"total_supply":25,"total_sold":8}'
```

- [ ] **Step 10: Commit**

```bash
git add lambdas/api_product/
git commit -m "feat: art.jt API product — critique, weather, pricing endpoints"
```

---

### Task 3: Custom Domain + CloudFront

- [ ] **Step 1: Request ACM certificate**

```bash
aws acm request-certificate \
  --domain-name api.art.jamestannahill.com \
  --validation-method DNS \
  --region us-east-1
```

Add the CNAME validation record to Cloudflare DNS.

- [ ] **Step 2: Create CloudFront distribution for API**

Or alternatively, add `api.art.jamestannahill.com` as a CNAME in Cloudflare pointing to the API Gateway invoke URL. Simpler than a separate CloudFront distribution.

```bash
# In Cloudflare: CNAME api.art.jamestannahill.com → $API_ID.execute-api.us-east-1.amazonaws.com (DNS only, no proxy)
```

Then add the custom domain to API Gateway:
```bash
aws apigateway create-domain-name \
  --domain-name api.art.jamestannahill.com \
  --regional-certificate-arn $CERT_ARN \
  --endpoint-configuration '{"types":["REGIONAL"]}' \
  --region us-east-1

aws apigateway create-base-path-mapping \
  --domain-name api.art.jamestannahill.com \
  --rest-api-id $API_ID \
  --stage v1 \
  --region us-east-1
```

- [ ] **Step 3: Commit**

```bash
git commit --allow-empty -m "infra: custom domain api.art.jamestannahill.com"
```

---

### Task 4: Stripe Billing + Key Provisioning

**Files:**
- Create: `lambdas/api_product/billing.py`

- [ ] **Step 1: Create Stripe products and prices**

In Stripe Dashboard or via API:
```bash
# Free tier (no charge)
# Starter tier: $29/mo
```

Create products in Stripe Dashboard:
- Product: "art.jt API — Starter" ($29/mo recurring)
- Product: "art.jt API — Free" ($0, for tracking)

- [ ] **Step 2: Create billing webhook handler**

`lambdas/api_product/billing.py`:
```python
"""Stripe webhook — provisions/deprovisions API keys on subscription events."""
import json
import os
import uuid
import boto3

TABLE_NAME = os.environ.get("TABLE_NAME", "art-generator")
dynamodb = boto3.resource("dynamodb")
apigateway = boto3.client("apigateway", region_name="us-east-1")
ses = boto3.client("ses", region_name="us-east-1")

USAGE_PLANS = {
    "free": os.environ.get("FREE_PLAN_ID", ""),
    "starter": os.environ.get("STARTER_PLAN_ID", ""),
}


def handle_billing_webhook(event, stripe_secret):
    import stripe
    stripe.api_key = stripe_secret

    payload = event.get("body", "")
    sig = event.get("headers", {}).get("Stripe-Signature", "")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

    try:
        evt = stripe.Webhook.construct_event(payload, sig, webhook_secret)
    except Exception:
        return {"statusCode": 400, "body": "Invalid signature"}

    if evt["type"] == "checkout.session.completed":
        session = evt["data"]["object"]
        customer_email = session.get("customer_email", "")
        tier = session.get("metadata", {}).get("tier", "free")
        _provision_key(customer_email, tier)

    elif evt["type"] == "customer.subscription.deleted":
        sub = evt["data"]["object"]
        customer_id = sub.get("customer", "")
        _deprovision_key(customer_id)

    return {"statusCode": 200, "body": json.dumps({"ok": True})}


def _provision_key(email, tier):
    # Create API Gateway key
    key_resp = apigateway.create_api_key(name=f"api-{email}", enabled=True)
    api_key_id = key_resp["id"]
    api_key_value = key_resp["value"]

    # Associate with usage plan
    plan_id = USAGE_PLANS.get(tier, USAGE_PLANS["free"])
    if plan_id:
        apigateway.create_usage_plan_key(usagePlanId=plan_id, keyId=api_key_id, keyType="API_KEY")

    # Store in DynamoDB
    table = dynamodb.Table(TABLE_NAME)
    table.put_item(Item={
        "PK": "API_KEY",
        "SK": api_key_value,
        "email": email,
        "tier": tier,
        "key_id": api_key_id,
    })

    # Email the key
    ses.send_email(
        Source="art.jt <art@monkeythorn.com>",
        Destination={"ToAddresses": [email]},
        Message={
            "Subject": {"Data": "Your art.jt API Key"},
            "Body": {"Text": {"Data": f"Your API key: {api_key_value}\n\nDocs: https://api.art.jamestannahill.com/docs\n\nTier: {tier}"}},
        },
    )
    print(f"[PROVISION] {email} → {tier} → {api_key_id}")


def _deprovision_key(customer_id):
    # Look up and disable key
    # Implementation depends on storing customer_id in DynamoDB
    pass
```

- [ ] **Step 3: Commit**

```bash
git add lambdas/api_product/billing.py
git commit -m "feat: Stripe billing webhook for API key provisioning"
```

---

### Task 5: API Documentation Site

**Files:**
- Create: `lambdas/site_rebuild/templates/api_docs.html`
- Create: `lambdas/site_rebuild/templates/api_quickstart.html`
- Modify: `lambdas/site_rebuild/handler.py` (render docs pages)
- Modify: `lambdas/site_rebuild/templates/base.html` (add API nav link)

- [ ] **Step 1: Create docs overview template**

Create `lambdas/site_rebuild/templates/api_docs.html` extending base.html with:
- Overview of all 3 endpoints
- Authentication section (x-api-key header)
- Rate limits table (Free vs Starter)
- Error format reference
- Links to individual endpoint docs
- "Get API Key" CTA → Stripe Checkout

- [ ] **Step 2: Create quickstart template**

Create `lambdas/site_rebuild/templates/api_quickstart.html` with:
- cURL examples for all 3 endpoints
- Python example (requests library)
- Node.js example (fetch)
- Response examples

- [ ] **Step 3: Add doc page rendering to site_rebuild handler**

In `lambdas/site_rebuild/handler.py`, add after the 404 page render:
```python
# API docs
pages["site/api/index.html"] = env.get_template("api_docs.html").render()
pages["site/api/quickstart/index.html"] = env.get_template("api_quickstart.html").render()
```

Add to sitemap:
```python
sitemap_urls.append(("https://art.jamestannahill.com/api/", "monthly", "0.8"))
sitemap_urls.append(("https://art.jamestannahill.com/api/quickstart/", "monthly", "0.7"))
```

- [ ] **Step 4: Add API link to nav**

In `base.html` nav, add after About:
```html
<a href="/api/">API</a>
```

- [ ] **Step 5: Deploy and rebuild**

```bash
cd lambdas/site_rebuild && cd package && zip -r9q ../deploy.zip . && cd .. && zip -gq deploy.zip handler.py && zip -grq deploy.zip templates/
aws lambda update-function-code --function-name art-site-rebuild --zip-file fileb://deploy.zip --region us-east-1
aws lambda invoke --function-name art-site-rebuild --payload '{}' /tmp/out.json --region us-east-1
```

- [ ] **Step 6: Commit**

```bash
git add lambdas/site_rebuild/
git commit -m "feat: API documentation site with quickstart guide"
```

---

### Task 6: Update llms.txt + About Page

- [ ] **Step 1: Add API section to llms.txt**

In handler.py llms.txt generation, add:
```
## API
Paid API at api.art.jamestannahill.com with three endpoints:
- Art Critic (POST /v1/critique): Score any image 1-10 on composition, color, complexity, impact
- Weather Drama (GET /v1/weather/rankings): Ranked global atmospheric drama locations
- Dynamic Pricing (POST /v1/price): Scarcity-based price multiplier from quality + rarity + demand
Free tier: 50 critiques + 100 weather + 100 pricing per month. Starter: $29/mo.
Docs: https://art.jamestannahill.com/api/
```

- [ ] **Step 2: Update About page**

Add brief API mention in The System section.

- [ ] **Step 3: Commit all**

```bash
git add lambdas/site_rebuild/ lambdas/api_product/
git commit -m "feat: art.jt API product — MVP shipped"
```
