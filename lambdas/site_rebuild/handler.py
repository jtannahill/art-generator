"""Site Rebuild Lambda — scans DynamoDB for all weather and palette items,
renders static HTML pages via Jinja2, uploads to S3, and invalidates CloudFront."""

import json
import os
from collections import defaultdict
from datetime import datetime, timezone

import boto3
from jinja2 import Environment, FileSystemLoader

BUCKET_NAME = os.environ.get("BUCKET_NAME", "")
TABLE_NAME = os.environ.get("TABLE_NAME", "")
DISTRIBUTION_ID = os.environ.get("DISTRIBUTION_ID", "")

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")


def handler(event, context):
    """Scans DynamoDB, renders all pages, uploads to S3, invalidates CloudFront."""
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(TABLE_NAME)
    items = scan_all(table)

    # Separate weather and palette items
    weather_items = [i for i in items if i.get("PK", "").startswith("WEATHER#")]
    palette_items = [i for i in items if i.get("PK", "").startswith("PALETTE#")]
    study_metas = [i for i in items if i.get("PK", "").startswith("STUDY#") and i.get("SK") == "META"]
    study_day_items = [i for i in items if i.get("PK", "").startswith("STUDY#") and i.get("SK", "").startswith("DAY#")]

    # Parse colors for palette items
    for item in palette_items:
        item["colors_parsed"] = _parse_colors(item.get("colors", "[]"))

    # Group data — weather is keyed by run_id (WEATHER#2026-03-16-130500)
    weather_by_run = group_by_date(weather_items, prefix="WEATHER#")
    palettes_by_location = group_by_location(palette_items)
    palettes_by_date = group_palette_by_date(palette_items)

    # Set up Jinja2
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)

    def format_coords(lat, lng):
        """Format lat/lng as '15°S, 70°W'."""
        try:
            lat, lng = float(lat), float(lng)
            lat_str = f"{abs(lat):.0f}°{'N' if lat >= 0 else 'S'}"
            lng_str = f"{abs(lng):.0f}°{'E' if lng >= 0 else 'W'}"
            return f"{lat_str}, {lng_str}"
        except (ValueError, TypeError):
            return ""

    env.filters["coords"] = lambda item: format_coords(item.get("lat", 0), item.get("lng", 0))

    pages = {}
    api_url = os.environ.get("API_URL", "")

    # Get latest run for index page
    latest_run = max(weather_by_run.keys()) if weather_by_run else None
    today_weather = weather_by_run[latest_run] if latest_run else []
    latest_palettes = _latest_palettes(palettes_by_location)
    pages["site/index.html"] = env.get_template("index.html").render(
        today_weather=today_weather,
        latest_palettes=latest_palettes,
        latest_run=latest_run,
        api_url=api_url,
    )

    # Render weather archive — all runs
    pages["site/weather/index.html"] = env.get_template("weather_archive.html").render(
        weather_by_run=weather_by_run,
    )

    # Render weather day pages
    for date, artworks in weather_by_run.items():
        pages[f"site/weather/{date}/index.html"] = env.get_template(
            "weather_day.html"
        ).render(date=date, artworks=artworks)

        # Render individual weather artwork pages
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

    # Render artist gallery pages with infinite scroll
    artist_info = {
        "sam_francis": ("Sam Francis", "https://www.guggenheim.org/artwork/artist/sam-francis"),
        "gerhard_richter": ("Gerhard Richter", "https://www.guggenheim.org/artwork/artist/gerhard-richter"),
        "hilma_af_klint": ("Hilma af Klint", "https://www.guggenheim.org/artwork/artist/hilma-af-klint"),
        "wassily_kandinsky": ("Wassily Kandinsky", "https://www.guggenheim.org/artwork/artist/vasily-kandinsky"),
        "helen_frankenthaler": ("Helen Frankenthaler", "https://www.guggenheim.org/artwork/artist/helen-frankenthaler"),
        "piet_mondrian": ("Piet Mondrian", "https://www.guggenheim.org/artwork/artist/piet-mondrian"),
        "yayoi_kusama": ("Yayoi Kusama", "https://www.guggenheim.org/artwork/artist/yayoi-kusama"),
        "mark_rothko": ("Mark Rothko", "https://www.guggenheim.org/artwork/artist/mark-rothko"),
        "bridget_riley": ("Bridget Riley", "https://www.tate.org.uk/art/artists/bridget-riley-1845"),
        "kazimir_malevich": ("Kazimir Malevich", "https://www.guggenheim.org/artwork/artist/kazimir-malevich"),
        "lesley_tannahill": ("Lesley Tannahill", "https://lesleytannahill.com"),
    }
    # Group weather items by artist for thumbnail mosaics
    artworks_by_artist = defaultdict(list)
    for run_id, artworks in weather_by_run.items():
        for a in artworks:
            artist_key = a.get("artist", "sam_francis")
            artworks_by_artist[artist_key].append({
                "run_id": a.get("run_id", run_id),
                "slug": a.get("SK", a.get("slug", "")),
            })
    # Sort each artist's works by run_id descending, take latest 4
    for k in artworks_by_artist:
        artworks_by_artist[k].sort(key=lambda x: x["run_id"], reverse=True)
        artworks_by_artist[k] = artworks_by_artist[k][:4]

    pages["site/artist/index.html"] = env.get_template("artist_index.html").render(
        artists=[(k, v[0], v[1]) for k, v in artist_info.items()],
        artworks_by_artist=dict(artworks_by_artist),
    )
    for artist_key, (artist_display, artist_link) in artist_info.items():
        pages[f"site/artist/{artist_key}/index.html"] = env.get_template("artist.html").render(
            artist_key=artist_key,
            artist_display=artist_display,
            artist_link=artist_link,
            api_url=api_url,
        )

    # Render world map page
    map_artworks = []
    for run_id, artworks_list in weather_by_run.items():
        for a in artworks_list:
            slug = a.get("SK", a.get("slug", ""))
            lat = float(a.get("lat", 0))
            lng = float(a.get("lng", 0))
            lat_str = f"{abs(lat):.0f}\u00b0{'N' if lat >= 0 else 'S'}"
            lng_str = f"{abs(lng):.0f}\u00b0{'E' if lng >= 0 else 'W'}"
            map_artworks.append({
                "lat": lat,
                "lng": lng,
                "title": slug.replace("-", " ").title(),
                "coords": f"{lat_str}, {lng_str}",
                "temp": str(a.get("temp", "")),
                "wind": str(a.get("wind_speed", "")),
                "svg_url": f"/weather/{a.get('run_id', run_id)}/{slug}/artwork.svg",
                "url": f"/weather/{a.get('run_id', run_id)}/{slug}/",
            })

    # Build palette markers for map
    map_palettes = []
    for slug, pals in palettes_by_location.items():
        if pals:
            p = pals[0]  # latest
            lat = float(p.get("lat", 0))
            lng = float(p.get("lng", 0))
            lat_str = f"{abs(lat):.0f}\u00b0{'N' if lat >= 0 else 'S'}"
            lng_str = f"{abs(lng):.0f}\u00b0{'E' if lng >= 0 else 'W'}"
            colors = []
            for c in p.get("colors_parsed", []):
                if isinstance(c, dict):
                    colors.append(c.get("hex", ""))
                elif isinstance(c, str):
                    colors.append(c)
            map_palettes.append({
                "lat": lat,
                "lng": lng,
                "name": p.get("name", slug).replace("-", " ").title(),
                "coords": f"{lat_str}, {lng_str}",
                "mood": p.get("mood", ""),
                "colors": colors,
                "thumb_url": f"/palettes/{p.get('SK', '')}/{slug}/source-thumb.jpg",
                "url": f"/palettes/{slug}/",
            })

    import json as _json
    mapbox_token = os.environ.get("MAPBOX_TOKEN", "")
    pages["site/map/index.html"] = env.get_template("map.html").render(
        artworks_json=_json.dumps(map_artworks),
        palettes_json=_json.dumps(map_palettes),
        mapbox_token=mapbox_token,
    )

    # Render study pages

    # Group study days by study_id
    study_days_by_id = defaultdict(list)
    for sd in study_day_items:
        sid = sd.get("PK", "").replace("STUDY#", "")
        study_days_by_id[sid].append(sd)
    for sid in study_days_by_id:
        study_days_by_id[sid].sort(key=lambda x: x.get("SK", ""))

    # Build study data for templates
    visible_studies = [s for s in study_metas if s.get("status") in ("active", "complete")]
    for study in visible_studies:
        sid = study.get("study_id", study.get("PK", "").replace("STUDY#", ""))
        days = study_days_by_id.get(sid, [])
        # Count artworks
        all_refs = []
        for d in days:
            refs = d.get("artwork_refs", [])
            if isinstance(refs, str):
                refs = _json.loads(refs)
            all_refs.extend(refs)
        study["day_count"] = len(days)
        study["artwork_count"] = len(all_refs)
        study["artworks"] = all_refs[:4]  # for mosaic thumbnail

    pages["site/studies/index.html"] = env.get_template("studies_index.html").render(
        studies=visible_studies,
    )

    # Individual study detail pages
    for study in visible_studies:
        sid = study.get("study_id", study.get("PK", "").replace("STUDY#", ""))
        days = study_days_by_id.get(sid, [])
        coords = study.get("coordinates", [])
        if isinstance(coords, str):
            coords = _json.loads(coords)

        # Build days data for template
        template_days = []
        map_days = []
        for d in days:
            refs = d.get("artwork_refs", [])
            if isinstance(refs, str):
                refs = _json.loads(refs)
            ws = d.get("weather_summary", {})
            if isinstance(ws, str):
                ws = _json.loads(ws)

            # Build artwork map keyed by (lat, lng) for grid lookup
            artwork_map = {}
            map_artworks = []
            for ref in refs:
                lat = float(ref.get("lat", 0))
                lng = float(ref.get("lng", 0))
                run_id = ref.get("run_id", "")
                slug = ref.get("slug", "")
                # Match to nearest coordinate
                for c in coords:
                    if abs(lat - float(c.get("lat", 0))) <= 5 and abs(lng - float(c.get("lng", 0))) <= 5:
                        artwork_map[(int(c["lat"]), int(c["lng"]))] = ref
                        break
                map_artworks.append({
                    "lat": lat, "lng": lng,
                    "title": slug.replace("-", " ").title(),
                    "svg_url": f"/weather/{run_id}/{slug}/artwork.svg",
                    "url": f"/weather/{run_id}/{slug}/",
                })

            template_days.append({
                "date": d.get("SK", "").replace("DAY#", ""),
                "artwork_map": artwork_map,
                "weather_summary": {k: str(v) for k, v in ws.items()} if ws else {},
            })
            map_days.append({
                "date": d.get("SK", "").replace("DAY#", ""),
                "artworks": map_artworks,
            })

        # Ensure coordinates are dicts
        template_coords = []
        for c in coords:
            if isinstance(c, dict):
                template_coords.append({"lat": int(float(c.get("lat", 0))), "lng": int(float(c.get("lng", 0)))})

        pages[f"site/studies/{sid}/index.html"] = env.get_template("study_detail.html").render(
            study={**study, "coordinates": template_coords, "study_id": sid},
            days=template_days,
            study_days_json=_json.dumps(map_days),
            study_coords_json=_json.dumps(template_coords),
            mapbox_token=mapbox_token,
        )

    # Render duets page — find artworks at same location by different artists
    duets = _find_duets(weather_items)
    pages["site/duets/index.html"] = env.get_template("duets.html").render(duets=duets)

    # Render comparison page
    comparisons = _find_comparisons(weather_items)
    pages["site/comparison/index.html"] = env.get_template("comparison.html").render(comparisons=comparisons)

    # Render about, privacy, terms pages
    pages["site/about/index.html"] = env.get_template("about.html").render()
    pages["site/privacy/index.html"] = env.get_template("privacy.html").render()
    pages["site/terms/index.html"] = env.get_template("terms.html").render()

    # Render shop pages
    pages["site/shop/success/index.html"] = env.get_template("shop_success.html").render()
    pages["site/shop/cancel/index.html"] = env.get_template("shop_cancel.html").render()

    # 404 error page
    pages["site/404.html"] = env.get_template("404.html").render()

    # Upload favicon
    # Upload static assets
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        for fname in os.listdir(static_dir):
            fpath = os.path.join(static_dir, fname)
            if os.path.isfile(fpath):
                with open(fpath, "r") as f:
                    pages[f"site/{fname}"] = f.read()

    # Generate robots.txt
    pages["site/robots.txt"] = "User-agent: *\nAllow: /\nSitemap: https://art.jamestannahill.com/sitemap.xml\n"

    # Generate sitemap.xml
    sitemap_urls = [
        ("https://art.jamestannahill.com/", "daily", "1.0"),
        ("https://art.jamestannahill.com/weather/", "daily", "0.9"),
        ("https://art.jamestannahill.com/artist/", "weekly", "0.8"),
        ("https://art.jamestannahill.com/about/", "monthly", "0.7"),
        ("https://art.jamestannahill.com/map/", "daily", "0.8"),
        ("https://art.jamestannahill.com/duets/", "daily", "0.8"),
        ("https://art.jamestannahill.com/comparison/", "daily", "0.8"),
        ("https://art.jamestannahill.com/studies/", "daily", "0.8"),
        ("https://art.jamestannahill.com/privacy/", "yearly", "0.3"),
        ("https://art.jamestannahill.com/terms/", "yearly", "0.3"),
    ]
    for artist_key in artist_info:
        sitemap_urls.append((f"https://art.jamestannahill.com/artist/{artist_key}/", "daily", "0.7"))
    for study in visible_studies:
        sid = study.get("study_id", study.get("PK", "").replace("STUDY#", ""))
        sitemap_urls.append((f"https://art.jamestannahill.com/studies/{sid}/", "daily", "0.6"))
    for run_id, artworks in weather_by_run.items():
        sitemap_urls.append((f"https://art.jamestannahill.com/weather/{run_id}/", "never", "0.6"))
        for artwork in artworks:
            slug = artwork.get("SK", artwork.get("slug", ""))
            sitemap_urls.append((f"https://art.jamestannahill.com/weather/{run_id}/{slug}/", "never", "0.5"))

    # Palette pages
    sitemap_urls.append(("https://art.jamestannahill.com/palettes/", "daily", "0.7"))
    for location_slug in palettes_by_location:
        sitemap_urls.append((f"https://art.jamestannahill.com/palettes/{location_slug}/", "weekly", "0.6"))
    for date in palettes_by_date:
        sitemap_urls.append((f"https://art.jamestannahill.com/palettes/{date}/", "never", "0.5"))

    sitemap_lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for url, freq, priority in sitemap_urls:
        sitemap_lines.append(f"  <url><loc>{url}</loc><changefreq>{freq}</changefreq><priority>{priority}</priority></url>")
    sitemap_lines.append("</urlset>")
    pages["site/sitemap.xml"] = "\n".join(sitemap_lines)

    # Generate llms.txt for AI crawlers
    pages["site/llms.txt"] = """# art.jamestannahill.com

> Daily generative art from real atmospheric data, inspired by abstract expressionism. Limited edition prints available.

## About
art.jt is a generative art project by James Tannahill. Every day, the system scans 54 global weather points across 10 latitude bands, identifies the 10 most visually dramatic atmospheric conditions, and generates original SVG artwork using AI on Amazon Bedrock. No artist's work was used to train the model — each artist is described via text prompts capturing their visual philosophy, and the AI writes SVG code from scratch. A parallel system extracts color palettes from Copernicus Sentinel-2 satellite imagery. The visual language draws from abstract expressionists including Sam Francis, Mark Rothko, Helen Frankenthaler, Hilma af Klint, Bridget Riley, Yayoi Kusama, Piet Mondrian, Wassily Kandinsky, Gerhard Richter, Kazimir Malevich, and Lesley Tannahill.

## Pages
- [Homepage](https://art.jamestannahill.com/) — Today's weather art with generate button and artist selector
- [Artists](https://art.jamestannahill.com/artist/) — Browse by artist inspiration (11 artists, infinite scroll galleries)
- [Archive](https://art.jamestannahill.com/weather/) — All past generations, browsable by run
- [World Map](https://art.jamestannahill.com/map/) — Interactive Mapbox globe with artwork and palette markers
- [Satellite Palettes](https://art.jamestannahill.com/palettes/) — Color palettes from Sentinel-2 orbital imagery
- [Compare](https://art.jamestannahill.com/comparison/) — Same weather data interpreted through 11 different artistic lenses
- [About](https://art.jamestannahill.com/about/) — About the project, methodology, weather data, and the artist

## Print Shop
Limited edition giclée prints available on every artwork page. Printed on Hahnemühle German Etching 310gsm with Certificate of Authenticity. Edition of 5 per size. Ships worldwide via theprintspace.

## How It Works
Weather data from Open-Meteo API (GFS/NOAA model, 54 scan points, 6 variables per point) → scored for visual interest (pressure anomaly 30%, wind 25%, temperature deviation 20%, precipitation 15%, humidity 10%) → top 10 with 15° geographic separation → AI on Amazon Bedrock generates SVG in selected artist's style → PNG preview rendered via CairoSVG → AI art critic scores quality 1-10 (composition, color, complexity, impact) → archived permanently in S3 → static HTML gallery on CloudFront. Satellite imagery from Copernicus Sentinel-2 → color quantization → mood briefs via Bedrock.

## ML Models
- **Art Critic**: Bedrock Haiku vision evaluates each artwork on composition, color harmony, complexity, and emotional impact (1-10 scale). Scores influence print pricing and archive curation.
- **Weather Drama Forecaster**: Runs daily at 20:00 UTC, fetches 24h GFS forecast for all 54 points, predicts tomorrow's most visually dramatic locations.
- **Dynamic Pricing**: Print prices reflect quality score + weather rarity + edition scarcity. Base price can increase up to 2× for exceptional pieces from rare atmospheric events.

## Subscribe
- [RSS Feed](https://art.jamestannahill.com/feed.xml) — Latest artworks in RSS 2.0 format
- Newsletter signup available on every page — daily art digest via email

## Licensing
Artwork: CC BY-NC-ND 4.0 (attribution required, no commercial use, no derivatives)
Code: All Rights Reserved
Contact: art@jamestannahill.com
"""

    # Generate RSS feed
    rss_items = []
    for run_id, artworks in list(weather_by_run.items())[:10]:  # Last 10 runs
        for artwork in artworks:
            slug = artwork.get("SK", artwork.get("slug", ""))
            title = slug.replace("-", " ").title()
            artist = artwork.get("artist", "sam_francis").replace("_", " ").title()
            rationale = artwork.get("rationale", "Generative weather art from atmospheric data.")
            created = artwork.get("created_at", run_id)
            rss_items.append(f"""    <item>
      <title>{title} — {artist}</title>
      <link>https://art.jamestannahill.com/weather/{run_id}/{slug}/</link>
      <description>{rationale}</description>
      <pubDate>{created}</pubDate>
      <guid>https://art.jamestannahill.com/weather/{run_id}/{slug}/</guid>
      <enclosure url="https://art.jamestannahill.com/weather/{run_id}/{slug}/preview-2048.png" type="image/png"/>
    </item>""")

    pages["site/feed.xml"] = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/1999/xhtml">
  <channel>
    <title>art.jt — Generative Weather Art</title>
    <link>https://art.jamestannahill.com</link>
    <description>Daily generative art from real atmospheric data, inspired by abstract expressionism.</description>
    <language>en-us</language>
    <atom:link href="https://art.jamestannahill.com/feed.xml" rel="self" type="application/rss+xml"/>
{"".join(rss_items)}
  </channel>
</rss>"""

    # Render palette archive
    pages["site/palettes/index.html"] = env.get_template(
        "palette_archive.html"
    ).render(palettes_by_location=palettes_by_location)

    # Render palette location pages
    for location_slug, palettes in palettes_by_location.items():
        pages[f"site/palettes/{location_slug}/index.html"] = env.get_template(
            "palette_location.html"
        ).render(location=location_slug, palettes=palettes)

    # Render palette day pages
    for date, palettes in palettes_by_date.items():
        pages[f"site/palettes/{date}/index.html"] = env.get_template(
            "palette_day.html"
        ).render(date=date, palettes=palettes)

    # Upload all pages to S3
    s3 = boto3.client("s3")
    for key, html in pages.items():
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=key,
            Body=html.encode("utf-8"),
            ContentType=_content_type(key),
            CacheControl="public, max-age=300",
        )

    # Copy artwork assets into site/ prefix so CloudFront can serve them
    _copy_assets_to_site(s3, weather_by_run, palettes_by_date)

    # Invalidate CloudFront
    if DISTRIBUTION_ID:
        cf = boto3.client("cloudfront")
        cf.create_invalidation(
            DistributionId=DISTRIBUTION_ID,
            InvalidationBatch={
                "Paths": {
                    "Quantity": 4,
                    "Items": ["/index.html", "/weather/*", "/palettes/*", "/shop/*"],
                },
                "CallerReference": f"rebuild-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
            },
        )

    return {
        "pages_rendered": len(pages),
        "weather_dates": len(weather_by_run),
        "palette_locations": len(palettes_by_location),
    }


def scan_all(table):
    """Full DynamoDB table scan with pagination."""
    items = []
    params = {}
    while True:
        response = table.scan(**params)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        params["ExclusiveStartKey"] = last_key
    return items


def group_by_date(items, prefix="WEATHER#"):
    """Groups items by date extracted from PK (e.g. 'WEATHER#2026-03-15' -> '2026-03-15').

    Returns dict of date -> list of items, sorted by date descending.
    """
    groups = defaultdict(list)
    for item in items:
        pk = item.get("PK", "")
        if pk.startswith(prefix):
            date = pk[len(prefix):]
            groups[date].append(item)

    # Sort by date descending
    return dict(sorted(groups.items(), key=lambda x: x[0], reverse=True))


def group_by_location(items):
    """Groups palette items by location slug from PK (e.g. 'PALETTE#sahara' -> 'sahara').

    Each location's items are sorted by date (SK) descending.
    Returns dict of slug -> list of items.
    """
    groups = defaultdict(list)
    for item in items:
        pk = item.get("PK", "")
        if pk.startswith("PALETTE#"):
            slug = pk[len("PALETTE#"):]
            groups[slug].append(item)

    # Sort each location's items by SK (date) descending
    for slug in groups:
        groups[slug].sort(key=lambda x: x.get("SK", ""), reverse=True)

    return dict(sorted(groups.items()))


def group_palette_by_date(items):
    """Groups palette items by date from SK.

    Returns dict of date -> list of items, sorted by date descending.
    """
    groups = defaultdict(list)
    for item in items:
        date = item.get("SK", "")
        if date:
            groups[date].append(item)

    return dict(sorted(groups.items(), key=lambda x: x[0], reverse=True))


def _parse_colors(colors):
    """Parse colors field — may be a JSON string or already a list."""
    if isinstance(colors, list):
        return colors
    if isinstance(colors, str):
        try:
            parsed = json.loads(colors)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def _content_type(key):
    """Return content type based on file extension."""
    if key.endswith(".xml") and "feed" in key:
        return "application/rss+xml"
    if key.endswith(".xml"):
        return "application/xml"
    if key.endswith(".txt"):
        return "text/plain"
    if key.endswith(".json"):
        return "application/json"
    return "text/html"


def _find_comparisons(weather_items):
    """Find locations where 3+ artists have generated work, for style comparison."""
    from collections import defaultdict
    by_location = defaultdict(list)
    for item in weather_items:
        lat = int(float(item.get("lat", 0)) // 5) * 5
        lng = int(float(item.get("lng", 0)) // 5) * 5
        by_location[(lat, lng)].append(item)

    comparisons = []
    for (lat, lng), items in by_location.items():
        # Get one artwork per artist (latest)
        artists_seen = {}
        for item in sorted(items, key=lambda x: x.get("run_id", ""), reverse=True):
            artist = item.get("artist", "unknown")
            if artist not in artists_seen:
                artists_seen[artist] = item

        if len(artists_seen) < 3:
            continue

        # Use first item for location info
        first = list(artists_seen.values())[0]
        slug = first.get("SK", first.get("slug", ""))
        lat_val = float(first.get("lat", 0))
        lng_val = float(first.get("lng", 0))
        lat_str = f"{abs(lat_val):.0f}\u00b0{'N' if lat_val >= 0 else 'S'}"
        lng_str = f"{abs(lng_val):.0f}\u00b0{'E' if lng_val >= 0 else 'W'}"

        pieces = []
        for artist_key, item in sorted(artists_seen.items()):
            item_run = item.get("run_id", item.get("PK", "").replace("WEATHER#", ""))
            item_slug = item.get("SK", item.get("slug", ""))
            pieces.append({
                "artist": artist_key,
                "artist_name": artist_key.replace("_", " ").title(),
                "svg_url": f"/weather/{item_run}/{item_slug}/artwork.svg",
                "url": f"/weather/{item_run}/{item_slug}/",
            })

        comparisons.append({
            "location": slug.replace("-", " ").title(),
            "coords": f"{lat_str}, {lng_str}",
            "temp": str(first.get("temp", "")),
            "wind": str(first.get("wind_speed", "")),
            "pressure": str(first.get("pressure", "")),
            "date": first.get("date", ""),
            "artist_count": len(pieces),
            "pieces": pieces,
        })

    # Sort by most artists first
    comparisons.sort(key=lambda x: x["artist_count"], reverse=True)
    return comparisons[:10]


def _find_duets(weather_items):
    """Find pairs of artworks at the same location by different artists."""
    # Group by approximate location (5-degree grid)
    from collections import defaultdict
    by_location = defaultdict(list)
    for item in weather_items:
        lat = int(float(item.get("lat", 0)) // 5) * 5
        lng = int(float(item.get("lng", 0)) // 5) * 5
        artist = item.get("artist", "unknown")
        by_location[(lat, lng)].append(item)

    duets = []
    for (lat, lng), items in by_location.items():
        # Find pairs with different artists
        artists_seen = {}
        for item in sorted(items, key=lambda x: x.get("run_id", ""), reverse=True):
            artist = item.get("artist", "unknown")
            if artist not in artists_seen:
                artists_seen[artist] = item

        # Create duets from pairs
        artist_list = list(artists_seen.items())
        for i in range(len(artist_list)):
            for j in range(i + 1, len(artist_list)):
                a1_key, a1 = artist_list[i]
                a2_key, a2 = artist_list[j]
                a1_run = a1.get("run_id", a1.get("PK", "").replace("WEATHER#", ""))
                a2_run = a2.get("run_id", a2.get("PK", "").replace("WEATHER#", ""))
                a1_slug = a1.get("SK", a1.get("slug", ""))
                a2_slug = a2.get("SK", a2.get("slug", ""))

                lat_val = float(a1.get("lat", 0))
                lng_val = float(a1.get("lng", 0))
                lat_str = f"{abs(lat_val):.0f}°{'N' if lat_val >= 0 else 'S'}"
                lng_str = f"{abs(lng_val):.0f}°{'E' if lng_val >= 0 else 'W'}"

                duets.append({
                    "location": a1_slug.replace("-", " ").title(),
                    "coords": f"{lat_str}, {lng_str}",
                    "temp": str(a1.get("temp", "")),
                    "wind": str(a1.get("wind_speed", "")),
                    "pressure": str(a1.get("pressure", "")),
                    "date": a1.get("date", ""),
                    "left": {
                        "artist": a1_key,
                        "artist_name": a1_key.replace("_", " ").title(),
                        "svg_url": f"/weather/{a1_run}/{a1_slug}/artwork.svg",
                        "url": f"/weather/{a1_run}/{a1_slug}/",
                    },
                    "right": {
                        "artist": a2_key,
                        "artist_name": a2_key.replace("_", " ").title(),
                        "svg_url": f"/weather/{a2_run}/{a2_slug}/artwork.svg",
                        "url": f"/weather/{a2_run}/{a2_slug}/",
                    },
                })

                if len(duets) >= 20:
                    return duets

    return duets


def _latest_palettes(palettes_by_location):
    """Get the most recent palette for each location."""
    latest = []
    for slug, palettes in palettes_by_location.items():
        if palettes:
            item = dict(palettes[0])
            item["location_slug"] = slug
            latest.append(item)
    return latest


def _copy_assets_to_site(s3, weather_by_run, palettes_by_date):
    """Copy artwork SVGs and palette assets into the site/ prefix for CloudFront.
    Also renders preview PNGs from SVGs if they don't exist (for OG/Twitter cards)."""
    for date, artworks in weather_by_run.items():
        for artwork in artworks:
            slug = artwork.get("SK", artwork.get("slug", ""))
            src_prefix = f"weather/{date}/{slug}/"
            dst_prefix = f"site/weather/{date}/{slug}/"
            # List and copy all objects in the artwork folder
            try:
                resp = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix=src_prefix)
                for obj in resp.get("Contents", []):
                    src_key = obj["Key"]
                    filename = src_key.split("/")[-1]
                    dst_key = dst_prefix + filename
                    s3.copy_object(
                        Bucket=BUCKET_NAME,
                        CopySource={"Bucket": BUCKET_NAME, "Key": src_key},
                        Key=dst_key,
                    )
            except Exception as e:
                print(f"Failed to copy assets for {slug}: {e}")

            # Note: PNGs are rendered by weather_render Lambda during generation.
            # Existing artworks without PNGs need a one-time backfill via scripts/backfill_canvas_format.py

    for date, palettes in palettes_by_date.items():
        for palette in palettes:
            # slug is in PK (PALETTE#slug) or the slug field
            slug = palette.get("slug", "")
            if not slug:
                pk = palette.get("PK", "")
                slug = pk.replace("PALETTE#", "") if pk.startswith("PALETTE#") else ""
            if not slug:
                continue
            src_prefix = f"palettes/{date}/{slug}/"
            dst_prefix = f"site/palettes/{date}/{slug}/"
            try:
                resp = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix=src_prefix)
                for obj in resp.get("Contents", []):
                    src_key = obj["Key"]
                    filename = src_key.split("/")[-1]
                    dst_key = dst_prefix + filename
                    s3.copy_object(
                        Bucket=BUCKET_NAME,
                        CopySource={"Bucket": BUCKET_NAME, "Key": src_key},
                        Key=dst_key,
                    )
            except Exception as e:
                print(f"Failed to copy palette assets for {slug}: {e}")
