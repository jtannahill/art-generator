# art.jamestannahill.com

Generative art from real atmospheric data. Daily artworks derived entirely from live weather patterns — pressure, wind, temperature — translated into SVG through the lens of abstract expressionism.

**Live:** [art.jamestannahill.com](https://art.jamestannahill.com)

## What It Does

Every day, this system scans 50 weather stations across the globe, identifies the 10 most visually dramatic atmospheric conditions, and generates original SVG artwork for each using Claude on Amazon Bedrock. Users can select from 11 artist inspirations — Sam Francis, Mark Rothko, Hilma af Klint, Yayoi Kusama, and more — each producing radically different visual interpretations of the same weather data.

Every piece is permanently archived and browsable via infinite-scroll artist galleries.

## Artists

Sam Francis | Gerhard Richter | Hilma af Klint | Wassily Kandinsky | Helen Frankenthaler | Piet Mondrian | Yayoi Kusama | Mark Rothko | Bridget Riley | Kazimir Malevich | Lesley Tannahill

## Stack

| Layer | Technology |
|-------|-----------|
| Infrastructure | AWS CDK (TypeScript) |
| Orchestration | Step Functions, EventBridge (daily 06:00 UTC) |
| Weather Data | Open-Meteo API (GFS/NOAA model) |
| Satellite Imagery | Copernicus Sentinel-2 |
| Art Generation | Amazon Bedrock (Claude Sonnet 4) |
| Storage | S3 (versioned), DynamoDB |
| Serving | CloudFront, static HTML (Jinja2) |
| Runtime | Python 3.12 Lambda |
| API | Lambda Function URLs (trigger + infinite scroll) |

## Architecture

```
EventBridge (daily 06:00 UTC)
    └── Step Function
        ├── Weather Ingest (Open-Meteo → 50 global points → top 10 scored)
        │   └── Weather Render ×10 (Bedrock → SVG artwork per region)
        ├── Satellite Ingest (Copernicus Sentinel-2 → color extraction)
        │   └── Palette Extract (median cut quantization → Bedrock mood brief)
        └── Site Rebuild (Jinja2 → static HTML + sitemap + llms.txt → S3 → CloudFront)
```

## Lambdas

| Function | Purpose |
|----------|---------|
| `art-weather-ingest` | Scans 50 global weather points, scores for visual drama |
| `art-weather-render` | Bedrock SVG generation with artist-style prompts, retry on invalid SVG |
| `art-satellite-ingest` | Copernicus Sentinel-2 imagery fetch with cloud cover filtering |
| `art-palette-extract` | Color quantization + Bedrock mood descriptions |
| `art-site-rebuild` | Static HTML generation, asset copying, sitemap/robots/llms.txt, CloudFront invalidation |
| `art-trigger` | HTTP endpoint for "Generate New Art" button |
| `art-api` | Paginated DynamoDB queries for infinite scroll galleries |

## SEO & Discoverability

- Schema.org markup: `WebSite`, `VisualArtwork` (per piece), `AboutPage`, `Person`
- Open Graph + Twitter Card meta on every page
- Canonical URLs, robots.txt, XML sitemap
- `llms.txt` for AI crawler discoverability (ChatGPT, Claude, Perplexity)
- Descriptive alt text on all artwork images

## Licensing

| Asset | License |
|-------|---------|
| Artwork (SVG outputs) | [CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/) |
| Generative system (code) | All Rights Reserved |
| Weather data | [Open-Meteo](https://open-meteo.com/) (GFS/NOAA) |
| Satellite imagery | [Copernicus Sentinel-2](https://dataspace.copernicus.eu/) (ESA) |

For commercial licensing, prints, or collaboration: [james@plocamium.ventures](mailto:james@plocamium.ventures)
