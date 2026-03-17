from unittest.mock import MagicMock, patch
from lambdas.print_shop.checkout import create_checkout_session


@patch("lambdas.print_shop.checkout.stripe")
def test_creates_stripe_session(mock_stripe, mock_table, sample_edition_item):
    mock_table.get_item.return_value = {"Item": sample_edition_item}
    mock_stripe.checkout.Session.create.return_value = MagicMock(url="https://checkout.stripe.com/sess_123")

    result = create_checkout_session(
        table=mock_table, stripe_key="sk_test_123",
        run_id="2026-03-16-130500", slug="arctic-70n-20w",
        size_key="L", base_url="https://art.jamestannahill.com",
    )

    assert result["url"] == "https://checkout.stripe.com/sess_123"
    call_kwargs = mock_stripe.checkout.Session.create.call_args[1]
    assert call_kwargs["metadata"]["run_id"] == "2026-03-16-130500"
    assert call_kwargs["metadata"]["size_key"] == "L"
    assert call_kwargs["line_items"][0]["price_data"]["unit_amount"] == 69500


@patch("lambdas.print_shop.checkout.stripe")
def test_returns_error_if_sold_out(mock_stripe, mock_table, sample_edition_item):
    sample_edition_item["sizes"]["L"]["sold"] = 50
    mock_table.get_item.return_value = {"Item": sample_edition_item}

    result = create_checkout_session(
        table=mock_table, stripe_key="sk_test_123",
        run_id="2026-03-16-130500", slug="arctic-70n-20w",
        size_key="L", base_url="https://art.jamestannahill.com",
    )

    assert result["error"] == "sold_out"
    mock_stripe.checkout.Session.create.assert_not_called()


@patch("lambdas.print_shop.checkout.stripe")
def test_returns_error_if_no_edition(mock_stripe, mock_table):
    mock_table.get_item.return_value = {}

    result = create_checkout_session(
        table=mock_table, stripe_key="sk_test_123",
        run_id="nonexistent", slug="nope",
        size_key="S", base_url="https://art.jamestannahill.com",
    )

    assert result["error"] == "not_found"
