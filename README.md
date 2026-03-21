# art.jamestannahill.com

Generative art from real atmospheric data. Daily artworks derived entirely from live weather patterns — pressure, wind, temperature — translated into SVG through the lens of abstract expressionism.

**Live:** [art.jamestannahill.com](https://art.jamestannahill.com)

## What It Does

Every day, this system scans 50 weather stations across the globe, identifies the 10 most visually dramatic atmospheric conditions, and generates original SVG artwork for each using Claude on Amazon Bedrock. Users can select from 11 artist inspirations — each producing radically different visual interpretations of the same weather data across 7 canvas formats (square, landscape, portrait, cinematic, golden ratio).

A parallel pipeline extracts color palettes from Copernicus Sentinel-2 satellite imagery, building a seasonal archive of Earth's real colors as seen from 786 km above the surface.

Every piece is permanently archived, browsable via infinite-scroll artist galleries, and plotted on an interactive Mapbox globe.

## Artists

Sam Francis | Gerhard Richter | Hilma af Klint | Wassily Kandinsky | Helen Frankenthaler | Piet Mondrian | Yayoi Kusama | Mark Rothko | Bridget Riley | Kazimir Malevich | Lesley Tannahill

## Stack

| Layer | Technology |
|-------|-----------|
| Infrastructure | AWS CDK (TypeScript) |
| Orchestration | Step Functions, EventBridge (daily 06:00 UTC) |
| Weather Data | Open-Meteo API (GFS/NOAA model) |
| Satellite Imagery | Copernicus Sentinel Hub Process API (Sentinel-2 L2A) |
| Art Generation | Amazon Bedrock (Claude Sonnet 4) → SVG → PNG preview (CairoSVG) |
| Color Extraction | Pillow median cut quantization |
| Storage | S3 (versioned), DynamoDB |
| CDN | CloudFront with OAC + CloudFront Function (index rewrite) |
| Templating | Jinja2 → static HTML |
| ML | Art critic (commentary scoring), weather forecaster, dynamic pricing |
| Newsletter | Resend (daily digest) |
| Social | RSS feed → dlvr.it (X/Instagram) |
| Runtime | Python 3.12 Lambda (15 functions) |
| API | Lambda Function URLs (trigger + infinite scroll) |
| Mapping | Mapbox GL JS (dark globe, artwork markers) |
| Analytics | Google Analytics 4 |

## Architecture

```
EventBridge (daily 06:00 UTC)
    └── Step Function (concurrency 2)
        ├── Weather Branch
        │   ├── Weather Ingest (Open-Meteo → 50 global points → top 10 scored)
        │   └── Weather Render ×10 (Bedrock → SVG, 7 canvas formats, 30-60+ elements)
        ├── Satellite Branch
        │   ├── Satellite Ingest (Sentinel Hub Process API → true-color JPEG)
        │   └── Palette Extract (median cut → 5-7 colors → Bedrock mood brief)
        ├── ML Branch
        │   ├── Art Critic (scores + commentary on generated pieces)
        │   └── Weather Forecast (atmospheric condition predictions)
        ├── Newsletter + Social
        │   ├── Newsletter Digest (Resend → subscribers)
        │   └── X Poster (RSS → OAuth 1.0a posting, DynamoDB dedup)
        └── Site Rebuild
            ├── Jinja2 templates → static HTML (homepage, archive, artists, studies, map, about, privacy, terms)
            ├── Asset copying (SVGs + satellite thumbs → site/ prefix)
            ├── sitemap.xml, robots.txt, llms.txt
            └── CloudFront invalidation
```

## Pages

| Page | Path | Description |
|------|------|-------------|
| Homepage | `/` | Latest generation + Generate button with artist selector |
| Artists | `/artist/` | Browse by artist — mosaic thumbnails from latest works |
| Artist Gallery | `/artist/{key}/` | Infinite scroll gallery via API |
| Archive | `/weather/` | All runs chronologically with artist labels |
| Run | `/weather/{run_id}/` | Single generation (10 pieces) |
| Artwork | `/weather/{run_id}/{slug}/` | Full artwork + rationale + metadata + print inquiry + OG preview |
| Map | `/map/` | Mapbox dark globe with all artwork markers |
| Studies | `/studies/` | Deep-dive artistic studies of compelling pieces |
| Palettes | `/palettes/` | Satellite color palettes by location |
| Print Shop | `/prints/` | Limited edition prints via theprintspace (Hahnemühle German Etching) |
| About | `/about/` | Project story, artist bio, how it works |
| Privacy | `/privacy/` | Privacy policy (GA4 disclosure) |
| Terms | `/terms/` | Terms of use (CC BY-NC-ND 4.0 details) |

## Lambdas

| Function | Purpose |
|----------|---------|
| `art-weather-ingest` | Scans 50 global weather points, scores for visual drama |
| `art-weather-render` | Bedrock SVG generation with artist-style prompts, PNG preview rendering (CairoSVG), retry on invalid SVG |
| `art-x-poster` | RSS-driven X/Twitter posting with OAuth 1.0a (text + link, DynamoDB dedup) |
| `art-satellite-ingest` | Sentinel Hub Process API → true-color imagery for 30 rotating locations |
| `art-palette-extract` | Color quantization + Bedrock mood descriptions |
| `art-critic` | ML commentary scoring on generated artworks |
| `art-weather-forecast` | Atmospheric condition predictions for upcoming generations |
| `art-newsletter-digest` | Daily newsletter via Resend to subscribers |
| `art-study-detector` | Identifies compelling pieces for deeper artistic studies |
| `art-study-admin` | Manages study generation and publishing workflow |
| `art-print-shop` | Print inquiry handling and theprintspace integration |
| `art-api-product` | Product catalog API for print shop listings |
| `art-site-rebuild` | Static HTML, asset copying, sitemap/robots/llms.txt, CloudFront invalidation |
| `art-trigger` | Generate button endpoint (2-hour cooldown) |
| `art-api` | Paginated DynamoDB queries for infinite scroll galleries |

## SEO & Discoverability

- **Schema.org**: `WebSite`, `VisualArtwork` (per piece with geo, medium, license), `AboutPage`, `Person`
- **Open Graph + Twitter Cards**: unique title, description, PNG preview image per artwork page
- **RSS Feed**: `/feed.xml` — latest artworks for social syndication (dlvr.it → X/IG)
- **Canonical URLs**: prevent duplicate content across runs
- **robots.txt**: allows all crawlers, sitemap reference
- **sitemap.xml**: dynamic, all pages + all artwork
- **llms.txt**: structured for AI crawlers (ChatGPT, Claude, Perplexity)
- **Google Analytics 4**: GA4 with privacy policy disclosure

## Licensing

| Asset | License |
|-------|---------|
| Artwork (SVG outputs) | [CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/) |
| Generative system (code) | All Rights Reserved |
| Weather data | [Open-Meteo](https://open-meteo.com/) (GFS/NOAA) |
| Satellite imagery | [Copernicus Sentinel-2](https://dataspace.copernicus.eu/) (ESA) |

For commercial licensing, prints, or collaboration: [art@jamestannahill.com](mailto:art@jamestannahill.com)
