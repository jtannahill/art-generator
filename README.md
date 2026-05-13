# art.jamestannahill.com

![Status](https://img.shields.io/badge/status-active-success)
![Python](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white)
![AWS Lambda](https://img.shields.io/badge/AWS-Lambda-FF9900?logo=awslambda&logoColor=white)
![Bedrock](https://img.shields.io/badge/AWS-Bedrock-FF9900?logo=amazonaws&logoColor=white)
![Step Functions](https://img.shields.io/badge/AWS-Step_Functions-FF4F8B?logo=amazonaws&logoColor=white)
![Replicate](https://img.shields.io/badge/Replicate-000000?logo=replicate&logoColor=white)
[![Live](https://img.shields.io/badge/live-art.jamestannahill.com-blue)](https://art.jamestannahill.com)
![Last Commit](https://img.shields.io/github/last-commit/jtannahill/art-generator)

Generative art from real atmospheric data. Daily artworks derived entirely from live weather patterns - pressure, wind, temperature - rendered as high-resolution digital art through the lens of abstract expressionism.

**Live:** [art.jamestannahill.com](https://art.jamestannahill.com)

## What It Does

Every day, this system scans 54 weather points across the globe, identifies the 10 most visually dramatic atmospheric conditions, and generates original artwork for each. There are three rendering paths:

1. **Flux 1.1 Pro** from a descriptive text prompt (most artists)
2. **Custom FLUX.1-dev LoRA fine-tunes** for seven artists trained on their own canvas reproductions (Lesley Tannahill, Sam Francis, Hilma af Klint, Joan Mitchell, Willem de Kooning, Helen Frankenthaler, Gerhard Richter)
3. **Bedrock Claude SVG** for a parallel vector version of every piece

Users can select from 16 artist inspirations - each producing radically different visual interpretations of the same weather data across 7 canvas formats (square, landscape, portrait, cinematic, golden ratio).

A parallel pipeline extracts color palettes from Copernicus Sentinel-2 satellite imagery, building a seasonal archive of Earth's real colors as seen from 786 km above the surface.

Every piece is permanently archived, browsable via infinite-scroll artist galleries, and plotted on an interactive Mapbox globe.

## Artists

Sam Francis* | Gerhard Richter* | Hilma af Klint* | Wassily Kandinsky | Helen Frankenthaler* | Piet Mondrian | Yayoi Kusama | Mark Rothko | Bridget Riley | Kazimir Malevich | Lesley Tannahill* | Arshile Gorky | Willem de Kooning* | Joan Mitchell* | Mark Tobey | Peter Max

*Asterisked artists have a custom FLUX.1-dev LoRA fine-tune. See [Custom LoRAs](#custom-loras).*

## Custom LoRAs

Seven artists are rendered through private FLUX.1-dev LoRA fine-tunes trained on hand-curated canvas reproductions, rather than from text prompts alone. Each artist's `/artist/{key}/` page carries a "Model & Methodology" block documenting the training corpus, hyperparameters, and provenance.

| Artist | Source | Training Set | Replicate Model |
|---|---|---|---|
| Lesley Tannahill | [lesleytannahill.com](https://lesleytannahill.com) Selected Self-Portraits | 28 canvases | `jtannahill/lora-lesley-tannahill` |
| Sam Francis | [samfrancis.com](https://samfrancis.com) Works on Canvas | 31 canvases | `jtannahill/lora-sam-francis` |
| Hilma af Klint | WikiArt (Paintings for the Temple, Ten Largest, Swan, Dove, Atom Series, Altarpieces) | 27 canvases | `jtannahill/lora-hilma-af-klint` |
| Joan Mitchell | WikiArt (Vetheuil + New York periods, 1951-1992) | 60 canvases | `jtannahill/lora-joan-mitchell` |
| Willem de Kooning | [Willem de Kooning Foundation](https://www.dekooning.org/) (1916-1988) | 66 canvases | `jtannahill/lora-willem-de-kooning` |
| Helen Frankenthaler | WikiArt (Soak-Stain + Color Field works) | curated canvases | `jtannahill/lora-helen-frankenthaler` |
| Gerhard Richter | WikiArt (Abstrakte Bilder squeegee period) | curated canvases | `jtannahill/lora-gerhard-richter` |

All LoRAs use rank 32, 1500 training steps, learning rate 1e-4, trained on Lambda Cloud H100 in ~12 minutes via [`ostris/flux-dev-lora-trainer`](https://replicate.com/ostris/flux-dev-lora-trainer) on Replicate (~$1-2 per training). Per-image captions encode title, medium, dimensions, and year. Trigger words follow the convention `{artist}_style`.

The `weather_render` Lambda dispatches via `ARTIST_LORA_MODELS`: when an artist key has a registered LoRA, the prompt is prepended with the trigger word and the call routes through `replicate.com/predictions` to the per-artist model instead of the public `flux-1.1-pro` endpoint. Inference cost is similar (~$0.04/piece).

Training scaffold lives in [`~/lora-train/`](../lora-train/) (separate repo).

## Stack

| Layer | Technology |
|-------|-----------|
| Infrastructure | AWS CDK (TypeScript) |
| Orchestration | Step Functions, EventBridge (daily 06:00 UTC) |
| Weather Data | Open-Meteo API (GFS/NOAA model) |
| Satellite Imagery | Copernicus Sentinel Hub Process API (Sentinel-2 L2A) |
| Art Generation | Flux 1.1 Pro (PNG, most artists), FLUX.1-dev LoRA fine-tunes (7 artists), Amazon Bedrock Claude (SVG, parallel) |
| Color Extraction | Pillow median cut quantization |
| Storage | S3 (versioned), DynamoDB |
| CDN | CloudFront with OAC + CloudFront Function (index rewrite) |
| Templating | Jinja2 → static HTML |
| ML | Art critic (commentary scoring), weather forecaster, dynamic pricing |
| Newsletter | Resend (daily digest) |
| Social | RSS feed → dlvr.it (X/Instagram) |
| Runtime | Python 3.12 Lambda (16 functions) |
| Watermarking | Pillow + Fredoka font, on-demand at download, S3-cached |
| API | Lambda Function URLs (trigger + infinite scroll) |
| Mapping | Mapbox GL JS (dark globe, artwork markers) |
| Analytics | Google Analytics 4 |

## Architecture

```
EventBridge (daily 06:00 UTC)
    └── Step Function (concurrency 2)
        ├── Weather Branch
        │   ├── Weather Ingest (Open-Meteo → 54 global points → top 10 scored)
        │   └── Weather Render ×10 (Flux PNG or Bedrock SVG, 7 canvas formats)
        ├── Satellite Branch
        │   ├── Satellite Ingest (Sentinel Hub Process API → true-color JPEG)
        │   └── Palette Extract (median cut → 5-7 colors → Bedrock mood brief)
        ├── ML Branch
        │   ├── Art Critic (scores + commentary on generated pieces)
        │   └── Weather Forecast (atmospheric condition predictions)
        ├── Newsletter + Social
        │   └── Newsletter Digest (Resend → subscribers)
        └── Site Rebuild
            ├── Jinja2 templates → static HTML (homepage, archive, artists, studies, map, about, privacy, terms)
            ├── Asset copying (artwork PNGs/SVGs + satellite thumbs → site/ prefix)
            ├── sitemap.xml, robots.txt, llms.txt
            └── CloudFront invalidation
```

## Pages

| Page | Path | Description |
|------|------|-------------|
| Homepage | `/` | Latest generation + Generate button with artist selector |
| Artists | `/artist/` | Browse by artist - mosaic thumbnails from latest works |
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
| `art-weather-ingest` | Scans 54 global weather points, scores for visual drama |
| `art-weather-render` | Tri-mode art generation: Flux 1.1 Pro (PNG), per-artist FLUX.1-dev LoRA fine-tune (7 artists, dispatched via `ARTIST_LORA_MODELS`), or Bedrock Claude (SVG). PNG preview rendering (CairoSVG). |
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
| `art-watermark-download` | Watermarks public PNG downloads (4K/8K) on demand. Caches to `site/downloads/`, returns 302 to CloudFront. Print-shop fulfillment uses the un-watermarked source path directly. |

## SEO & Discoverability

- **Schema.org**: `WebSite`, `VisualArtwork` (per piece with geo, medium, license), `AboutPage`, `Person`
- **Open Graph + Twitter Cards**: unique title, description, PNG preview image per artwork page
- **RSS Feed**: `/feed.xml` - latest artworks for social syndication (dlvr.it → X/IG)
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
