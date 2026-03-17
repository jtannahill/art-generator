"""Stripe webhook handler — processes checkout.session.completed events."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import stripe
from botocore.exceptions import ClientError

from .email import send_confirmation, send_fulfillment_alert
from .tps_client import TpsClient


def handle_checkout_completed(table, session: dict, tps_api_key: str) -> dict:
    """Process a completed Stripe Checkout session."""
    session_id = session["id"]
    payment_intent = session.get("payment_intent", "")
    metadata = session.get("metadata", {})
    run_id = metadata["run_id"]
    slug = metadata["slug"]
    size_key = metadata["size_key"]
    customer_email = session.get("customer_details", {}).get("email", "")

    # Idempotency: check via dedicated SESSION# lookup item
    session_resp = table.get_item(Key={"PK": f"SESSION#{session_id}", "SK": "META"})
    if "Item" in session_resp and session_resp["Item"]:
        return {"status": "already_processed"}

    # Atomic edition increment
    edition_pk = f"EDITION#{run_id}#{slug}"
    try:
        update_resp = table.update_item(
            Key={"PK": edition_pk, "SK": "META"},
            UpdateExpression="SET sizes.#sk.sold = sizes.#sk.sold + :one",
            ConditionExpression="sizes.#sk.sold < sizes.#sk.#limit",
            ExpressionAttributeNames={"#sk": size_key, "#limit": "limit"},
            ExpressionAttributeValues={":one": Decimal("1")},
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            stripe.Refund.create(payment_intent=payment_intent)
            order_id = str(uuid.uuid4())
            table.put_item(Item={
                "PK": f"ORDER#{order_id}", "SK": "META",
                "stripe_session_id": session_id,
                "stripe_payment_intent": payment_intent,
                "status": "refunded",
                "artwork_run_id": run_id, "artwork_slug": slug,
                "size_key": size_key, "customer_email": customer_email,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            return {"status": "refunded"}
        raise

    updated_sizes = update_resp["Attributes"]["sizes"]
    edition_number = int(updated_sizes[size_key]["sold"])
    edition_limit = int(updated_sizes[size_key]["limit"])
    size_dims = str(updated_sizes[size_key]["dims"])
    price_cents = int(updated_sizes[size_key]["price_cents"])

    # Create ORDER + SESSION lookup items
    order_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    order_item = {
        "PK": f"ORDER#{order_id}", "SK": "META",
        "stripe_session_id": session_id,
        "stripe_payment_intent": payment_intent,
        "status": "paid",
        "customer_email": customer_email,
        "artwork_run_id": run_id, "artwork_slug": slug,
        "size_key": size_key,
        "edition_number": edition_number, "edition_limit": edition_limit,
        "price_cents": price_cents,
        "created_at": now,
    }
    table.put_item(Item=order_item)
    table.put_item(Item={"PK": f"SESSION#{session_id}", "SK": "META", "order_id": order_id})

    # Fulfillment
    try:
        fulfill_result = _fulfill_order(table=table, tps_api_key=tps_api_key, run_id=run_id, slug=slug, size_key=size_key, session=session)
        tps_order_id = fulfill_result["tps_order_id"]
        table.update_item(
            Key={"PK": f"ORDER#{order_id}", "SK": "META"},
            UpdateExpression="SET #s = :s, tps_order_id = :tid",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "fulfilling", ":tid": tps_order_id},
        )
        table.put_item(Item={"PK": f"TPS_ORDER#{tps_order_id}", "SK": "META", "order_pk": f"ORDER#{order_id}"})
    except Exception as e:
        error_detail = str(e)
        table.update_item(
            Key={"PK": f"ORDER#{order_id}", "SK": "META"},
            UpdateExpression="SET #s = :s, error_detail = :err",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "fulfillment_failed", ":err": error_detail},
        )
        artwork_title = slug.replace("-", " ").title()
        send_fulfillment_alert(order_id, error_detail, customer_email, artwork_title)
        return {"status": "fulfillment_failed", "order_id": order_id, "error": error_detail}

    # Confirmation email (non-fatal)
    artwork_title = slug.replace("-", " ").title()
    artwork_url = f"https://art.jamestannahill.com/weather/{run_id}/{slug}/"
    try:
        send_confirmation(customer_email, artwork_title, edition_number, edition_limit, size_dims, artwork_url)
    except Exception as e:
        print(f"Confirmation email failed: {e}")

    return {"status": "success", "order_id": order_id, "edition_number": edition_number}


def _fulfill_order(table, tps_api_key: str, run_id: str, slug: str, size_key: str, session: dict) -> dict:
    """Register product with theprintspace (if needed) and create order."""
    product_pk = f"PRODUCT#{run_id}#{slug}"
    product_resp = table.get_item(Key={"PK": product_pk, "SK": "META"})
    product = product_resp.get("Item")

    client = TpsClient(api_key=tps_api_key)

    if not product:
        raise ValueError(f"Product not registered with theprintspace for {product_pk}. Upload artwork via theprintspace dashboard and create PRODUCT# item manually.")

    print_option_id = product["print_options"].get(size_key)
    if not print_option_id:
        raise ValueError(f"No print option for size {size_key} on product {product_pk}")

    coa_print_option_id = product.get("coa_print_option_id")

    shipping = session.get("shipping_details", {})
    address = shipping.get("address", {})
    name_parts = shipping.get("name", "").split(" ", 1)
    first_name = name_parts[0] if name_parts else ""
    last_name = name_parts[1] if len(name_parts) > 1 else ""

    embryonic = client.create_embryonic_order(
        product_id=int(product["tps_product_id"]),
        print_option_id=int(print_option_id),
        first_name=first_name, last_name=last_name,
        email=session.get("customer_details", {}).get("email", ""),
        coa_print_option_id=int(coa_print_option_id) if coa_print_option_id else None,
        address={
            "line1": address.get("line1", ""), "line2": address.get("line2", ""),
            "town": address.get("city", ""), "county": address.get("state", ""),
            "post_code": address.get("postal_code", ""),
            "country_code": address.get("country", ""),
            "phone": shipping.get("phone", ""),
        },
    )

    confirmed = client.confirm_order(order_id=embryonic["Id"], delivery_options=embryonic.get("DeliveryOptions", []))
    return {"tps_order_id": confirmed["Id"]}
