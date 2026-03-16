# art.jamestannahill.com

Generative art from real atmospheric data. Daily artworks derived entirely from weather patterns, inspired by Sam Francis.

**Live:** [art.jamestannahill.com](https://art.jamestannahill.com)

## What It Does

Every day, this system scans 50 global weather stations, identifies the 10 most visually dramatic atmospheric conditions, and generates original SVG artwork for each using Claude on Amazon Bedrock. The visual language changes every time — bold color fields, energetic splashes, lyrical abstraction driven by real pressure gradients, wind speeds, and temperature anomalies.

## Stack

| Layer | Technology |
|-------|-----------|
| Infrastructure | AWS CDK (TypeScript) |
| Orchestration | Step Functions, EventBridge (daily 06:00 UTC) |
| Weather Data | Open-Meteo API (GFS model) |
| Art Generation | Amazon Bedrock (Claude Sonnet) → SVG |
| Storage | S3, DynamoDB |
| Serving | CloudFront, static HTML via Jinja2 |
| Runtime | Python 3.12 Lambda |

## Architecture

```
EventBridge (daily cron)
    └── Step Function
        ├── Weather Ingest (Open-Meteo → 50 global points → top 10)
        │   └── Weather Render ×10 (Bedrock → SVG artwork)
        ├── Satellite Ingest (Copernicus Sentinel-2)
        │   └── Palette Extract (color quantization → mood)
        └── Site Rebuild (Jinja2 → static HTML → S3 → CloudFront)
```

## Lambdas

| Function | Purpose |
|----------|---------|
| `art-weather-ingest` | Fetches weather from Open-Meteo, scores 50 global points for visual interest |
| `art-weather-render` | Sends atmospheric data to Bedrock, generates SVG artwork, retries on invalid SVG |
| `art-satellite-ingest` | Fetches Sentinel-2 satellite imagery from Copernicus (when configured) |
| `art-palette-extract` | Extracts color palettes via median cut quantization, generates mood descriptions |
| `art-site-rebuild` | Renders Jinja2 templates to static HTML, copies assets, invalidates CloudFront |
| `art-trigger` | HTTP endpoint for the "Generate New Art" button |

## Development

```bash
# Install CDK dependencies
cd cdk && npm install

# Install Lambda dependencies (for deploy)
for d in lambdas/*/; do
  if [ -f "$d/requirements.txt" ]; then
    pip install -r "$d/requirements.txt" -t "$d" \
      --platform manylinux2014_x86_64 --implementation cp \
      --python-version 3.12 --only-binary=:all:
  fi
done

# Deploy
cd cdk && npx cdk deploy

# Run tests
python3 -m pytest tests/ -v

# Trigger pipeline manually
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:ACCOUNT:stateMachine:art-daily-pipeline \
  --input '{}'
```

## License

MIT
