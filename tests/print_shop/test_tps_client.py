from unittest.mock import patch, MagicMock
from lambdas.print_shop.tps_client import TpsClient


@patch("lambdas.print_shop.tps_client.requests")
def test_create_embryonic_order(mock_requests):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "Id": 12345,
        "DeliveryOptions": [
            {"Id": 1, "DeliveryChargeExcludingSalesTax": 15.00, "DeliveryChargeSalesTax": 0, "Method": "Standard"},
            {"Id": 2, "DeliveryChargeExcludingSalesTax": 30.00, "DeliveryChargeSalesTax": 0, "Method": "Express"},
        ],
    }
    mock_resp.raise_for_status = MagicMock()
    mock_requests.post.return_value = mock_resp

    client = TpsClient(api_key="test-key", base_url="https://api.sandbox.tps-test.io")
    result = client.create_embryonic_order(
        product_id=100, print_option_id=200,
        first_name="John", last_name="Doe", email="john@example.com",
        address={"line1": "123 Main St", "town": "London", "post_code": "SW1A 1AA", "country_code": "GB", "phone": "+447700900000"},
    )
    assert result["Id"] == 12345
    assert len(result["DeliveryOptions"]) == 2


@patch("lambdas.print_shop.tps_client.requests")
def test_confirm_order_picks_cheapest(mock_requests):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"Id": 12345, "OrderState": "NewOrder"}
    mock_resp.raise_for_status = MagicMock()
    mock_requests.post.return_value = mock_resp

    client = TpsClient(api_key="test-key")
    delivery_options = [
        {"Id": 2, "DeliveryChargeExcludingSalesTax": 30.00, "DeliveryChargeSalesTax": 3.00},
        {"Id": 1, "DeliveryChargeExcludingSalesTax": 15.00, "DeliveryChargeSalesTax": 1.50},
    ]
    result = client.confirm_order(order_id=12345, delivery_options=delivery_options)
    call_kwargs = mock_requests.post.call_args[1]["json"]
    assert call_kwargs["DeliveryOptionId"] == 1
    assert result["Id"] == 12345
