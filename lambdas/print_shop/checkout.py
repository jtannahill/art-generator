"""Checkout action — creates Stripe Checkout Session."""

import stripe


def create_checkout_session(
    table, stripe_key: str, run_id: str, slug: str, size_key: str, base_url: str
) -> dict:
    """Create a Stripe Checkout Session for a print purchase.

    Returns: {"url": "https://checkout.stripe.com/..."} or {"error": "reason"}
    """
    edition_pk = f"EDITION#{run_id}#{slug}"
    resp = table.get_item(Key={"PK": edition_pk, "SK": "META"})
    edition = resp.get("Item")
    if not edition:
        return {"error": "not_found"}

    sizes = edition.get("sizes", {})
    size = sizes.get(size_key)
    if not size:
        return {"error": "invalid_size"}

    if size["sold"] >= size["limit"]:
        return {"error": "sold_out"}

    stripe.api_key = stripe_key
    title = slug.replace("-", " ").title()
    dims = size["dims"]

    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{
            "price_data": {
                "currency": "usd",
                "unit_amount": int(size["price_cents"]),
                "product_data": {
                    "name": f"{title} — {dims}\" Limited Edition Print",
                    "description": f"Giclée print on Hahnemühle German Etching 310gsm. Edition of {size['limit']}. Certificate of Authenticity included.",
                },
            },
            "quantity": 1,
        }],
        metadata={
            "run_id": run_id,
            "slug": slug,
            "size_key": size_key,
        },
        shipping_address_collection={
            "allowed_countries": [
                "US", "GB", "CA", "AU", "DE", "FR", "IT", "ES", "NL", "BE",
                "AT", "CH", "SE", "NO", "DK", "FI", "IE", "PT", "PL", "CZ",
                "JP", "SG", "NZ", "LU", "HK",
            ],
        },
        success_url=f"{base_url}/shop/success/?session_id={{CHECKOUT_SESSION_ID}}&edition=pending&size={size_key}&title={slug}",
        cancel_url=f"{base_url}/weather/{run_id}/{slug}/",
    )

    return {"url": session.url}
