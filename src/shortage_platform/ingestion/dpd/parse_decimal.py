from decimal import Decimal

from shortage_platform.ingestion.dpd.normalize import normalize_din, parse_decimal


def test_decimal_leading_dot():
    assert parse_decimal(".5") == Decimal("0.5")


def test_decimal_whole_number():
    assert parse_decimal("20") == Decimal("20")


def test_decimal_strips_spaces():
    assert parse_decimal(" 1.25 ") == Decimal("1.25")


def test_decimal_empty_is_none():
    assert parse_decimal("") is None
    assert parse_decimal("   ") is None
    assert parse_decimal(None) is None


def test_decimal_malformed_raises():
    with pytest.raises(ValueError):
        parse_decimal("abc")


def test_decimal_rejects_nan_and_infinity():
    with pytest.raises(ValueError):
        parse_decimal("NaN")
    with pytest.raises(ValueError):
        parse_decimal("Infinity")