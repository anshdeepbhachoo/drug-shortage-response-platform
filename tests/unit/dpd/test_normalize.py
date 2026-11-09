import pytest

from shortage_platform.ingestion.dpd.normalize import normalize_din


def test_din_pads_short_value():
    assert normalize_din("326925") == "00326925"


def test_din_keeps_full_value():
    assert normalize_din("00000809") == "00000809"


def test_din_empty_is_none():
    assert normalize_din("") is None
    assert normalize_din(None) is None


def test_din_malformed_raises():
    with pytest.raises(ValueError):
        normalize_din("ABC123")