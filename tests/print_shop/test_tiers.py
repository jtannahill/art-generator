import pytest
from lambdas.print_shop.tiers import (
    FORMAT_TO_RATIO,
    ASPECT_RATIOS,
    format_to_aspect_ratio,
    get_tiers_for_format,
)


# ---------------------------------------------------------------------------
# FORMAT_TO_RATIO mapping
# ---------------------------------------------------------------------------

class TestFormatToRatioMapping:
    """All 7 canvas formats map to the correct aspect ratio string."""

    def test_2048x2048_is_1_1(self):
        assert FORMAT_TO_RATIO["2048x2048"] == "1:1"

    def test_1920x1920_is_1_1(self):
        assert FORMAT_TO_RATIO["1920x1920"] == "1:1"

    def test_2560x1440_is_16_9(self):
        assert FORMAT_TO_RATIO["2560x1440"] == "16:9"

    def test_2400x1600_is_3_2(self):
        assert FORMAT_TO_RATIO["2400x1600"] == "3:2"

    def test_2048x1024_is_2_1(self):
        assert FORMAT_TO_RATIO["2048x1024"] == "2:1"

    def test_1440x2560_is_9_16(self):
        assert FORMAT_TO_RATIO["1440x2560"] == "9:16"

    def test_1024x2048_is_1_2(self):
        assert FORMAT_TO_RATIO["1024x2048"] == "1:2"

    def test_exactly_7_formats(self):
        assert len(FORMAT_TO_RATIO) == 7


# ---------------------------------------------------------------------------
# format_to_aspect_ratio()
# ---------------------------------------------------------------------------

class TestFormatToAspectRatio:
    def test_known_format_returns_ratio(self):
        assert format_to_aspect_ratio("2048x2048") == "1:1"

    def test_all_known_formats_round_trip(self):
        for fmt, ratio in FORMAT_TO_RATIO.items():
            assert format_to_aspect_ratio(fmt) == ratio

    def test_unknown_format_raises_value_error(self):
        with pytest.raises(ValueError):
            format_to_aspect_ratio("9999x9999")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            format_to_aspect_ratio("")

    def test_error_message_contains_format(self):
        bad = "bad_format"
        with pytest.raises(ValueError, match=bad):
            format_to_aspect_ratio(bad)


# ---------------------------------------------------------------------------
# ASPECT_RATIOS data integrity
# ---------------------------------------------------------------------------

EXPECTED_TIERS = ["S", "M", "L", "XL", "XXL"]
ALL_RATIOS = ["1:1", "16:9", "3:2", "2:1", "9:16", "1:2"]


class TestAspectRatiosStructure:
    def test_all_six_ratios_present(self):
        assert set(ASPECT_RATIOS.keys()) == set(ALL_RATIOS)

    def test_each_ratio_has_five_tiers(self):
        for ratio, tiers in ASPECT_RATIOS.items():
            assert list(tiers.keys()) == EXPECTED_TIERS, f"{ratio} missing tiers"

    def test_each_tier_has_required_keys(self):
        for ratio, tiers in ASPECT_RATIOS.items():
            for tier, data in tiers.items():
                assert "dims" in data, f"{ratio}/{tier} missing dims"
                assert "limit" in data, f"{ratio}/{tier} missing limit"
                assert "price_cents" in data, f"{ratio}/{tier} missing price_cents"

    def test_limits_decrease_s_to_xxl(self):
        for ratio, tiers in ASPECT_RATIOS.items():
            limits = [tiers[t]["limit"] for t in EXPECTED_TIERS]
            assert limits == sorted(limits, reverse=True), (
                f"{ratio} limits not descending: {limits}"
            )

    def test_prices_increase_s_to_xxl(self):
        for ratio, tiers in ASPECT_RATIOS.items():
            prices = [tiers[t]["price_cents"] for t in EXPECTED_TIERS]
            assert prices == sorted(prices), (
                f"{ratio} prices not ascending: {prices}"
            )

    def test_all_price_cents_are_positive_integers(self):
        for ratio, tiers in ASPECT_RATIOS.items():
            for tier, data in tiers.items():
                assert isinstance(data["price_cents"], int) and data["price_cents"] > 0

    def test_all_limits_are_positive_integers(self):
        for ratio, tiers in ASPECT_RATIOS.items():
            for tier, data in tiers.items():
                assert isinstance(data["limit"], int) and data["limit"] > 0


class TestAspectRatiosSpotCheck:
    """Spot-check specific dims/prices/limits for each ratio."""

    def test_1_1_s(self):
        t = ASPECT_RATIOS["1:1"]["S"]
        assert t == {"dims": "12x12", "limit": 5, "price_cents": 15500}

    def test_1_1_xxl(self):
        t = ASPECT_RATIOS["1:1"]["XXL"]
        assert t == {"dims": "60x60", "limit": 5, "price_cents": 260000}

    def test_16_9_m(self):
        t = ASPECT_RATIOS["16:9"]["M"]
        assert t == {"dims": "24x14", "limit": 5, "price_cents": 25000}

    def test_16_9_xl(self):
        t = ASPECT_RATIOS["16:9"]["XL"]
        assert t == {"dims": "48x27", "limit": 5, "price_cents": 95000}

    def test_3_2_l(self):
        t = ASPECT_RATIOS["3:2"]["L"]
        assert t == {"dims": "24x16", "limit": 5, "price_cents": 45000}

    def test_2_1_xxl(self):
        t = ASPECT_RATIOS["2:1"]["XXL"]
        assert t == {"dims": "60x30", "limit": 5, "price_cents": 140000}

    def test_9_16_s(self):
        t = ASPECT_RATIOS["9:16"]["S"]
        assert t == {"dims": "9x16", "limit": 5, "price_cents": 12500}

    def test_1_2_m(self):
        t = ASPECT_RATIOS["1:2"]["M"]
        assert t == {"dims": "15x30", "limit": 5, "price_cents": 25000}


# ---------------------------------------------------------------------------
# get_tiers_for_format()
# ---------------------------------------------------------------------------

class TestGetTiersForFormat:
    def test_returns_dict_with_aspect_ratio_and_sizes(self):
        result = get_tiers_for_format("2048x2048")
        assert "aspect_ratio" in result
        assert "sizes" in result

    def test_aspect_ratio_correct_for_square(self):
        result = get_tiers_for_format("2048x2048")
        assert result["aspect_ratio"] == "1:1"

    def test_aspect_ratio_correct_for_landscape(self):
        result = get_tiers_for_format("2560x1440")
        assert result["aspect_ratio"] == "16:9"

    def test_sizes_has_five_tiers(self):
        result = get_tiers_for_format("2048x2048")
        assert list(result["sizes"].keys()) == EXPECTED_TIERS

    def test_each_size_has_sold_field(self):
        result = get_tiers_for_format("2048x2048")
        for tier, data in result["sizes"].items():
            assert "sold" in data, f"tier {tier} missing 'sold'"

    def test_sold_is_zero(self):
        result = get_tiers_for_format("2048x2048")
        for tier, data in result["sizes"].items():
            assert data["sold"] == 0, f"tier {tier} sold should be 0"

    def test_sold_does_not_mutate_aspect_ratios_dict(self):
        """get_tiers_for_format must not add 'sold' to the canonical ASPECT_RATIOS."""
        get_tiers_for_format("2048x2048")
        for tier in ASPECT_RATIOS["1:1"].values():
            assert "sold" not in tier

    def test_both_square_formats_produce_identical_tiers(self):
        r1 = get_tiers_for_format("2048x2048")
        r2 = get_tiers_for_format("1920x1920")
        assert r1 == r2

    def test_original_dims_and_price_preserved(self):
        result = get_tiers_for_format("2048x2048")
        s = result["sizes"]["S"]
        assert s["dims"] == "12x12"
        assert s["price_cents"] == 15500
        assert s["limit"] == 5

    def test_unknown_format_raises_value_error(self):
        with pytest.raises(ValueError):
            get_tiers_for_format("0x0")

    def test_all_formats_return_valid_structure(self):
        for fmt in FORMAT_TO_RATIO:
            result = get_tiers_for_format(fmt)
            assert result["aspect_ratio"] in ASPECT_RATIOS
            assert len(result["sizes"]) == 5
