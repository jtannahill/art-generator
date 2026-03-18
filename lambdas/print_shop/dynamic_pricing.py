"""Dynamic pricing — computes price multiplier based on artwork signals."""

import json
import boto3

dynamodb = boto3.resource("dynamodb")

# Multiplier ranges:
# Base: 1.0x
# Quality bonus: up to +0.5x for 10/10 quality
# Rarity bonus: up to +0.3x for weather score > 80
# Scarcity bonus: up to +0.2x for editions 3+ of 5 sold
# Max multiplier: 2.0x


def compute_multiplier(table_name, run_id, slug):
    """Returns a price multiplier (1.0 - 2.0) based on artwork signals."""
    table = dynamodb.Table(table_name)

    # Get artwork metadata
    item = table.get_item(Key={"PK": f"WEATHER#{run_id}", "SK": slug}).get("Item", {})

    quality_score = float(item.get("quality_score", 5))  # 1-10
    weather_score = float(item.get("score", 50))  # 0-100

    # Get edition sales
    edition_item = table.get_item(
        Key={"PK": f"EDITION#{run_id}#{slug}", "SK": "META"}
    ).get("Item", {})
    sizes = json.loads(edition_item.get("sizes", "{}")) if edition_item else {}
    total_sold = sum(int(s.get("sold", 0)) for s in sizes.values())
    total_limit = sum(int(s.get("limit", 5)) for s in sizes.values()) or 25
    sell_through = total_sold / total_limit if total_limit > 0 else 0

    # Compute multiplier components
    quality_bonus = max(0, (quality_score - 5) / 5) * 0.5  # 0-0.5
    rarity_bonus = max(0, (weather_score - 50) / 50) * 0.3  # 0-0.3
    scarcity_bonus = sell_through * 0.2  # 0-0.2

    multiplier = 1.0 + quality_bonus + rarity_bonus + scarcity_bonus
    multiplier = round(min(2.0, max(1.0, multiplier)), 2)

    return {
        "multiplier": multiplier,
        "quality_score": quality_score,
        "weather_score": weather_score,
        "sell_through": round(sell_through, 2),
        "components": {
            "quality_bonus": round(quality_bonus, 2),
            "rarity_bonus": round(rarity_bonus, 2),
            "scarcity_bonus": round(scarcity_bonus, 2),
        },
    }
