"""Newsletter Digest Lambda — sends daily art email to subscribers via SES.
Triggered after site-rebuild in the daily pipeline."""

import json
import os
import xml.etree.ElementTree as ET
import urllib.request
from datetime import datetime, timezone

import boto3

TABLE_NAME = os.environ.get("TABLE_NAME", "art-generator")
SENDER = os.environ.get("SENDER_EMAIL", "art@jamestannahill.com")
FEED_URL = os.environ.get("FEED_URL", "https://art.jamestannahill.com/feed.xml")
SITE_URL = "https://art.jamestannahill.com"
MAX_ITEMS = int(os.environ.get("MAX_ITEMS", "6"))

dynamodb = boto3.resource("dynamodb")
ses = boto3.client("ses", region_name="us-east-1")


def handler(event, context):
    """Fetch latest artworks from RSS, send digest to all subscribers."""
    # Get subscribers
    table = dynamodb.Table(TABLE_NAME)
    result = table.query(
        KeyConditionExpression="PK = :pk",
        ExpressionAttributeValues={":pk": "SUBSCRIBER"},
    )
    subscribers = [item["SK"] for item in result.get("Items", [])]

    if not subscribers:
        print("[INFO] No subscribers — skipping digest")
        return {"sent": 0, "subscribers": 0}

    # Fetch RSS feed for latest artworks
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": "art-newsletter/1.0"})
    resp = urllib.request.urlopen(req, timeout=10)
    root = ET.fromstring(resp.read())
    items = root.findall(".//item")[:MAX_ITEMS]

    if not items:
        print("[INFO] No items in RSS feed — skipping digest")
        return {"sent": 0, "subscribers": len(subscribers)}

    # Build email
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    subject = f"Today's Weather Art — {today}"
    html_body = build_email_html(items, today)
    text_body = build_email_text(items, today)

    # Send to each subscriber
    sent = 0
    failed = 0
    for email in subscribers:
        try:
            ses.send_email(
                Source=f"art.jt <{SENDER}>",
                Destination={"ToAddresses": [email]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {
                        "Html": {"Data": html_body, "Charset": "UTF-8"},
                        "Text": {"Data": text_body, "Charset": "UTF-8"},
                    },
                },
            )
            sent += 1
        except Exception as e:
            print(f"[ERROR] Failed to send to {email}: {e}")
            failed += 1

    print(f"[INFO] Digest sent to {sent}/{len(subscribers)} subscribers ({failed} failed)")
    return {"sent": sent, "failed": failed, "subscribers": len(subscribers)}


def build_email_html(items, today):
    """Build branded HTML email with artwork grid."""
    artwork_cards = ""
    for item in items:
        title = item.findtext("title", "")
        link = item.findtext("link", "")
        description = item.findtext("description", "")
        enclosure = item.find("enclosure")
        img_url = enclosure.get("url", "") if enclosure is not None else ""

        # Derive PNG preview URL from link
        if not img_url and link:
            img_url = link.rstrip("/") + "/preview-2048.png"

        # Parse title: "Arctic 60N 130W — Wassily Kandinsky"
        parts = title.split(" — ", 1)
        location = parts[0] if parts else title
        artist = parts[1] if len(parts) > 1 else ""

        desc_short = description[:120] + "..." if len(description) > 120 else description

        artwork_cards += f"""
        <tr><td style="padding:12px 0;">
          <a href="{link}" style="text-decoration:none; color:inherit; display:block;">
            <table cellpadding="0" cellspacing="0" border="0" width="100%"><tr>
              <td width="140" style="padding-right:16px; vertical-align:top;">
                <img src="{img_url}" width="140" height="140" alt="{title}" style="border-radius:6px; display:block; object-fit:cover; background:#111;" />
              </td>
              <td style="vertical-align:top;">
                <div style="font-size:16px; font-weight:600; color:#ffffff; margin-bottom:4px;">{location}</div>
                <div style="font-size:13px; color:#c4b5fd; margin-bottom:6px;">{artist}</div>
                <div style="font-size:13px; color:#999999; line-height:1.5;">{desc_short}</div>
              </td>
            </tr></table>
          </a>
        </td></tr>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0; padding:0; background:#0a0a0a; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<table cellpadding="0" cellspacing="0" border="0" width="100%" style="background:#0a0a0a;">
<tr><td align="center" style="padding:32px 16px;">
<table cellpadding="0" cellspacing="0" border="0" width="600" style="max-width:600px;">

  <!-- Header -->
  <tr><td style="padding-bottom:24px; border-bottom:1px solid #222;">
    <a href="{SITE_URL}" style="text-decoration:none; color:#ffffff; font-size:22px; font-weight:700; letter-spacing:-0.5px;">art.jt</a>
    <span style="color:#555; font-size:14px; margin-left:12px;">{today}</span>
  </td></tr>

  <!-- Intro -->
  <tr><td style="padding:24px 0 16px;">
    <div style="font-size:15px; color:#bbb; line-height:1.6;">Today's generative artworks from real atmospheric data.</div>
  </td></tr>

  <!-- Artworks -->
  {artwork_cards}

  <!-- CTA -->
  <tr><td style="padding:24px 0; text-align:center; border-top:1px solid #222;">
    <a href="{SITE_URL}" style="display:inline-block; padding:12px 28px; background:linear-gradient(135deg,#1a1a3e,#2a1a4e); border:1px solid #444; border-radius:6px; color:#c4b5fd; font-size:15px; font-weight:600; text-decoration:none;">View Full Gallery</a>
  </td></tr>

  <!-- Footer -->
  <tr><td style="padding:24px 0 0; text-align:center; color:#555; font-size:12px; line-height:1.6;">
    <div>Generative weather art by <a href="https://jamestannahill.com" style="color:#8ab4f8; text-decoration:none;">James Tannahill</a></div>
    <div style="margin-top:8px;">Artwork: <a href="https://creativecommons.org/licenses/by-nc-nd/4.0/" style="color:#666;">CC BY-NC-ND 4.0</a> &middot; Data: <a href="https://open-meteo.com/" style="color:#666;">Open-Meteo</a></div>
    <div style="margin-top:12px; color:#444;">You received this because you subscribed at art.jamestannahill.com</div>
  </td></tr>

</table>
</td></tr>
</table>
</body></html>"""


def build_email_text(items, today):
    """Plain text fallback."""
    lines = [f"art.jt — Today's Weather Art — {today}", "", ""]
    for item in items:
        title = item.findtext("title", "")
        link = item.findtext("link", "")
        description = item.findtext("description", "")
        desc_short = description[:150] + "..." if len(description) > 150 else description
        lines.append(f"{title}")
        lines.append(f"{desc_short}")
        lines.append(f"{link}")
        lines.append("")

    lines.append(f"View full gallery: {SITE_URL}")
    lines.append("")
    lines.append("---")
    lines.append("Generative weather art by James Tannahill")
    lines.append("You received this because you subscribed at art.jamestannahill.com")
    return "\n".join(lines)
