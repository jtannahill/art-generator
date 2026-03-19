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
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps({"error": {"code": code, "message": message, "request_id": request_id}}),
    }
