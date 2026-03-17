from unittest.mock import MagicMock, patch
from lambdas.print_shop.editions import get_editions


def test_returns_existing_edition(mock_table, sample_edition_item):
    mock_table.get_item.return_value = {"Item": sample_edition_item}
    result = get_editions(mock_table, "2026-03-16-130500", "arctic-70n-20w")
    assert result["aspect_ratio"] == "1:1"
    assert result["sizes"]["S"]["dims"] == "12x12"
    mock_table.get_item.assert_called_once_with(
        Key={"PK": "EDITION#2026-03-16-130500#arctic-70n-20w", "SK": "META"}
    )


def test_creates_edition_lazily_from_weather_item(mock_table, sample_weather_item):
    mock_table.get_item.side_effect = [
        {},  # No EDITION
        {"Item": sample_weather_item},  # WEATHER item
    ]
    result = get_editions(mock_table, "2026-03-16-130500", "arctic-70n-20w")
    assert result["aspect_ratio"] == "1:1"
    assert result["sizes"]["S"]["limit"] == 100
    mock_table.put_item.assert_called_once()
    written = mock_table.put_item.call_args[1]["Item"]
    assert written["PK"] == "EDITION#2026-03-16-130500#arctic-70n-20w"
    assert written["SK"] == "META"


def test_returns_none_if_artwork_not_found(mock_table):
    mock_table.get_item.side_effect = [{}, {}]
    result = get_editions(mock_table, "nonexistent", "slug")
    assert result is None


@patch("lambdas.print_shop.editions._parse_viewbox_from_s3")
def test_falls_back_to_s3_viewbox(mock_parse, mock_table):
    weather_no_format = {
        "PK": "WEATHER#run1", "SK": "slug1",
        "run_id": "run1", "slug": "slug1",
    }
    mock_table.get_item.side_effect = [
        {},  # No EDITION
        {"Item": weather_no_format},  # WEATHER without canvas_format
    ]
    mock_parse.return_value = "2560x1440"
    result = get_editions(mock_table, "run1", "slug1")
    assert result["aspect_ratio"] == "16:9"
    mock_parse.assert_called_once_with("run1", "slug1")
