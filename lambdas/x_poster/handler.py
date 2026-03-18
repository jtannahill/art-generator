"""X/Twitter poster Lambda — reads RSS feed for new artworks,
posts tweets via X API v2 with OAuth 1.0a."""

import json
import os
import time
import hmac
import hashlib
import base64
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET

import boto3

FEED_URL = os.environ.get("FEED_URL", "https://art.jamestannahill.com/feed.xml")
POSTED_TABLE = os.environ.get("POSTED_TABLE", "art-x-posts")
MAX_POSTS_PER_RUN = int(os.environ.get("MAX_POSTS_PER_RUN", "3"))

dynamodb = boto3.resource("dynamodb")
ssm = boto3.client("ssm")

_creds = {}


def get_creds():
    if _creds:
        return _creds
    params = ssm.get_parameters(
        Names=[
            "/art-generator/x-consumer-key",
            "/art-generator/x-consumer-secret",
            "/art-generator/x-access-token",
            "/art-generator/x-access-secret",
        ],
        WithDecryption=True,
    )
    for p in params["Parameters"]:
        _creds[p["Name"].split("/")[-1]] = p["Value"]
    return _creds


def handler(event, context):
    """Fetch RSS feed, find unposted items, tweet them."""
    posted_table = dynamodb.Table(POSTED_TABLE)

    # Fetch and parse RSS feed
    print(f"[INFO] Fetching {FEED_URL}")
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": "art-x-poster/1.0"})
    resp = urllib.request.urlopen(req, timeout=10)
    xml_data = resp.read()
    root = ET.fromstring(xml_data)

    items = root.findall(".//item")
    print(f"[INFO] Found {len(items)} items in RSS feed")

    posted_count = 0
    creds = get_creds()

    for item in items:
        if posted_count >= MAX_POSTS_PER_RUN:
            break

        guid = item.findtext("guid", "")
        title = item.findtext("title", "")
        link = item.findtext("link", "")
        description = item.findtext("description", "")

        if not guid or not link:
            continue

        # Check if already posted
        existing = posted_table.get_item(Key={"artwork_id": guid}).get("Item")
        if existing:
            continue

        # Parse title: "Arctic 60N 130W — Wassily Kandinsky"
        parts = title.split(" — ", 1)
        location_name = parts[0].strip() if parts else title
        artist = parts[1].strip() if len(parts) > 1 else ""

        tweet = compose_tweet(location_name, artist, description, link)

        try:
            result = post_tweet(creds, tweet)
            print(f"[TWEET] {result}")
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            print(f"[ERROR] X API {e.code}: {body}")
            if e.code in (402, 429):
                print("[STOP] Rate limited or credits depleted — stopping")
                break
            continue
        except Exception as e:
            print(f"[ERROR] Failed to post {guid}: {e}")
            continue

        posted_table.put_item(Item={
            "artwork_id": guid,
            "posted_at": int(time.time()),
            "tweet_text": tweet,
        })
        posted_count += 1
        print(f"[POSTED] {guid}")

    return {"posted": posted_count}


def compose_tweet(location, artist, description, url):
    url_len = 23
    hashtag = " #generativeart"
    header = location
    if artist:
        header += f"\nby {artist}"

    available = 280 - len(header) - url_len - 4 - len(hashtag)
    if description and available > 30:
        desc = description
        if len(desc) > available:
            desc = desc[:available - 1].rsplit(" ", 1)[0] + "…"
        return f"{header}\n\n{desc}\n\n{url}{hashtag}"

    return f"{header}\n\n{url}{hashtag}"


# --- OAuth 1.0a ---

def _percent_encode(s):
    return urllib.parse.quote(str(s), safe="")


def oauth_sign(method, url, params, consumer_secret, token_secret):
    sorted_params = "&".join(
        f"{_percent_encode(k)}={_percent_encode(v)}"
        for k, v in sorted(params.items())
    )
    base_string = f"{method}&{_percent_encode(url)}&{_percent_encode(sorted_params)}"
    signing_key = f"{_percent_encode(consumer_secret)}&{_percent_encode(token_secret)}"
    sig = hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    return base64.b64encode(sig).decode()


def oauth_header(method, url, body_params, creds):
    oauth_params = {
        "oauth_consumer_key": creds["x-consumer-key"],
        "oauth_nonce": uuid.uuid4().hex,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": creds["x-access-token"],
        "oauth_version": "1.0",
    }
    all_params = {**oauth_params, **body_params}
    oauth_params["oauth_signature"] = oauth_sign(
        method, url, all_params,
        creds["x-consumer-secret"], creds["x-access-secret"]
    )
    header_str = ", ".join(
        f'{_percent_encode(k)}="{_percent_encode(v)}"'
        for k, v in sorted(oauth_params.items())
    )
    return f"OAuth {header_str}"


def post_tweet(creds, text):
    url = "https://api.twitter.com/2/tweets"
    payload = json.dumps({"text": text}).encode()

    # For JSON body, OAuth signature only includes oauth_* params (not body)
    auth = oauth_header("POST", url, {}, creds)
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Authorization", auth)
    req.add_header("Content-Type", "application/json")
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())
