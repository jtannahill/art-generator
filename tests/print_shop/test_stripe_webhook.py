from unittest.mock import MagicMock, patch
from decimal import Decimal
from lambdas.print_shop.stripe_webhook import handle_checkout_completed


def _make_session(run_id="2026-03-16-130500", slug="arctic-70n-20w", size_key="L"):
    return {
        "id": "cs_test_123", "payment_intent": "pi_test_456",
        "metadata": {"run_id": run_id, "slug": slug, "size_key": size_key},
        "customer_details": {"email": "buyer@example.com"},
        "shipping_details": {
            "name": "John Doe",
            "address": {"line1": "123 Main St", "city": "London", "state": "", "postal_code": "SW1A 1AA", "country": "GB"},
            "phone": "+447700900000",
        },
    }


def test_idempotency_skips_duplicate(mock_table):
    mock_table.get_item.return_value = {"Item": {"PK": "SESSION#cs_test_123", "SK": "META", "order_id": "existing"}}
    result = handle_checkout_completed(mock_table, _make_session(), "test-tps-key")
    assert result["status"] == "already_processed"
    mock_table.update_item.assert_not_called()


@patch("lambdas.print_shop.stripe_webhook.send_confirmation")
@patch("lambdas.print_shop.stripe_webhook._fulfill_order")
def test_successful_order(mock_fulfill, mock_email, mock_table):
    mock_table.get_item.return_value = {}  # No SESSION# item
    mock_table.update_item.return_value = {"Attributes": {"sizes": {"L": {"sold": Decimal("1"), "limit": Decimal("50"), "dims": "30x30", "price_cents": Decimal("35000")}}}}
    mock_fulfill.return_value = {"tps_order_id": 12345}

    result = handle_checkout_completed(mock_table, _make_session(), "test-tps-key")

    assert result["status"] == "success"
    assert result["edition_number"] == 1
    mock_table.update_item.assert_called()


@patch("lambdas.print_shop.stripe_webhook.stripe")
def test_refund_on_sold_out(mock_stripe, mock_table):
    from botocore.exceptions import ClientError
    mock_table.get_item.return_value = {}
    mock_table.update_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem"
    )

    result = handle_checkout_completed(mock_table, _make_session(), "test-tps-key")
    assert result["status"] == "refunded"
    mock_stripe.Refund.create.assert_called_once()
