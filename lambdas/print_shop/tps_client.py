"""theprintspace CreativeHub API client."""

import requests

DEFAULT_BASE_URL = "https://api.creativehub.io"


class TpsClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL):
        self.base_url = base_url
        self.headers = {
            "Authorization": f"ApiKey {api_key}",
            "Content-Type": "application/json",
        }

    def create_embryonic_order(
        self, product_id: int, print_option_id: int,
        first_name: str, last_name: str, email: str, address: dict,
        coa_print_option_id: int | None = None,
    ) -> dict:
        order_items = [{"ProductId": product_id, "PrintOptionId": print_option_id, "Quantity": 1}]
        if coa_print_option_id:
            order_items.append({"ProductId": product_id, "PrintOptionId": coa_print_option_id, "Quantity": 1})

        payload = {
            "FirstName": first_name, "LastName": last_name, "Email": email,
            "OrderItems": order_items,
            "ShippingAddress": {
                "FirstName": first_name, "LastName": last_name,
                "Line1": address["line1"], "Line2": address.get("line2", ""),
                "Town": address.get("town", ""), "County": address.get("county", ""),
                "PostCode": address["post_code"],
                "CountryCode": address.get("country_code", ""),
                "PhoneNumber": address["phone"],
            },
        }
        resp = requests.post(f"{self.base_url}/api/v1/orders/embryonic", headers=self.headers, json=payload)
        resp.raise_for_status()
        return resp.json()

    def confirm_order(self, order_id: int, delivery_options: list) -> dict:
        cheapest = min(delivery_options, key=lambda d: d["DeliveryChargeExcludingSalesTax"])
        payload = {
            "OrderId": order_id,
            "DeliveryOptionId": cheapest["Id"],
            "DeliveryChargeExcludingSalesTax": cheapest["DeliveryChargeExcludingSalesTax"],
            "DeliveryChargeSalesTax": cheapest.get("DeliveryChargeSalesTax", 0),
        }
        resp = requests.post(f"{self.base_url}/api/v1/orders/confirmed", headers=self.headers, json=payload)
        resp.raise_for_status()
        return resp.json()

    def query_products(self, page: int = 1, page_size: int = 50) -> dict:
        resp = requests.post(f"{self.base_url}/api/v1/products/query", headers=self.headers, json={"Page": page, "PageSize": page_size})
        resp.raise_for_status()
        return resp.json()

    def get_product(self, product_id: int) -> dict:
        resp = requests.get(f"{self.base_url}/api/v1/products/{product_id}", headers=self.headers)
        resp.raise_for_status()
        return resp.json()
