"""theprintspace webhook handler — processes OrderStateChanged events."""

import hashlib
import hmac
import json


def handle_tps_webhook(table, body: str, signature: str, webhook_secret: str) -> dict:
    expected = hmac.new(webhook_secret.encode(), body.encode(), hashlib.sha1).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return {"error": "invalid_signature"}

    payload = json.loads(body)
    kind = payload.get("ApiWebhookKind")

    if kind == "Test":
        return {"status": "ok"}
    if kind != "OrderStateChanged":
        return {"status": "ignored", "kind": kind}

    order_data = payload.get("Order", {})
    tps_order_id = order_data.get("Id")
    order_state = order_data.get("OrderState", "")
    tracking = order_data.get("TrackingNumber", "")

    if not tps_order_id:
        return {"error": "missing_order_id"}

    # Find ORDER via TPS_ORDER lookup item
    lookup_resp = table.get_item(Key={"PK": f"TPS_ORDER#{tps_order_id}", "SK": "META"})
    lookup = lookup_resp.get("Item")
    if not lookup:
        return {"error": "order_not_found", "tps_order_id": tps_order_id}

    order_pk = lookup["order_pk"]

    status_map = {"Dispatched": "dispatched", "Delivered": "delivered"}
    new_status = status_map.get(order_state)
    if not new_status:
        return {"status": "ignored", "order_state": order_state}

    update_expr = "SET #s = :s"
    expr_values = {":s": new_status}
    expr_names = {"#s": "status"}

    if tracking:
        update_expr += ", tracking_number = :track"
        expr_values[":track"] = tracking

    table.update_item(
        Key={"PK": order_pk, "SK": "META"},
        UpdateExpression=update_expr,
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )

    return {"status": "updated", "new_status": new_status}
