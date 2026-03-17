import hashlib
import hmac
import json
from lambdas.print_shop.tps_webhook import handle_tps_webhook


def test_updates_order_on_dispatch(mock_table):
    mock_table.get_item.return_value = {"Item": {"PK": "TPS_ORDER#12345", "SK": "META", "order_pk": "ORDER#abc"}}

    body = json.dumps({
        "ApiWebhookKind": "OrderStateChanged",
        "Order": {"Id": 12345, "OrderState": "Dispatched", "TrackingNumber": "TRACK123"},
    })
    secret = "test-secret"
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha1).hexdigest()

    result = handle_tps_webhook(mock_table, body, sig, secret)
    assert result["status"] == "updated"
    mock_table.update_item.assert_called_once()


def test_rejects_invalid_signature(mock_table):
    body = json.dumps({"ApiWebhookKind": "Test"})
    result = handle_tps_webhook(mock_table, body, "bad-sig", "test-secret")
    assert result["error"] == "invalid_signature"
