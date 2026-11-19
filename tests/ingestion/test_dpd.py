"""DPD client and record validation. No network, no database."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from ingestion.dpd_client import DpdAPIError, DpdClient
from ingestion.dpd_ingest import (
    parse_decimal,
    validate_coded,
    validate_ingredient,
    validate_product,
    validate_status,
)

PRODUCT = {
    "drug_code": 2049,
    "class_name": "Human",
    "drug_identification_number": "0326925",
    "brand_name": "VASOPRESSIN INJECTION USP",
    "descriptor": "",
    "number_of_ais": "1",
    "ai_group_no": "0101652001",
    "company_name": "EXAMPLE PHARMA INC",
    "last_update_date": "2025-03-14",
}


def client_with(handler) -> DpdClient:
    client = DpdClient(base_url="https://example.test/api/drug", sleep=lambda _: None)
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


# ---------------------------------------------------------
# Client
# ---------------------------------------------------------

def test_get_sends_json_params_and_returns_list():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/drug/drugproduct/"
        assert request.url.params["din"] == "00326925"
        assert request.url.params["type"] == "json"
        return httpx.Response(200, json=[PRODUCT])

    assert client_with(handler).get("drugproduct", din="00326925") == [PRODUCT]


def test_single_object_response_becomes_list():
    client = client_with(lambda r: httpx.Response(200, json={"drug_code": 2049, "external_status_code": 2}))
    assert client.get("status", id=2049) == [{"drug_code": 2049, "external_status_code": 2}]


def test_empty_response_is_empty_list():
    assert client_with(lambda r: httpx.Response(200, json=[])).get("drugproduct", din="99999999") == []


def test_retries_server_errors_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(200, json=[PRODUCT])

    assert client_with(handler).get("drugproduct", din="00326925") == [PRODUCT]
    assert len(calls) == 3


def test_gives_up_after_retries():
    with pytest.raises(DpdAPIError, match="HTTP 503"):
        client_with(lambda r: httpx.Response(503)).get("drugproduct", din="00326925")


def test_non_json_is_an_error():
    with pytest.raises(DpdAPIError, match="not JSON"):
        client_with(lambda r: httpx.Response(200, text="<html>")).get("route", id=1)


# ---------------------------------------------------------
# Validation
# ---------------------------------------------------------

def test_product_pads_din_and_parses_fields():
    row, problem = validate_product(PRODUCT)
    assert problem is None
    assert row["din"] == "00326925"
    assert row["drug_code"] == 2049
    assert row["number_of_ais"] == 1
    assert str(row["last_update_date"]) == "2025-03-14"
    assert row["descriptor"] is None


@pytest.mark.parametrize("change, code", [
    ({"drug_identification_number": "0224798A"}, "DIN_FORMAT"),
    ({"brand_name": "  "}, "MISSING_BRAND"),
    ({"drug_code": "abc"}, "BAD_DRUG_CODE"),
])
def test_bad_products_are_quarantined(change, code):
    row, problem = validate_product({**PRODUCT, **change})
    assert row is None
    assert problem[0] == code


def test_ingredient_splits_qualifier_and_computes_concentration():
    row, problem = validate_ingredient(
        {"ingredient_name": "Heparin Sodium (Porcine)", "strength": "1000", "strength_unit": "UNIT",
         "dosage_value": "1", "dosage_unit": "ML"},
        drug_code=7,
    )
    assert problem is None
    assert row["ingredient_base"] == "HEPARIN SODIUM"
    assert row["ingredient_qualifier"] == "Porcine"
    assert row["conc_per_unit"] == Decimal("1000")
    assert row["conc_unit"] == "UNIT/ML"
    assert len(row["row_hash"]) == 64


def test_negative_strength_is_quarantined():
    row, problem = validate_ingredient({"ingredient_name": "HEPARIN SODIUM", "strength": "-1"}, drug_code=7)
    assert row is None and problem[0] == "STRENGTH_NEGATIVE"


def test_unknown_status_is_quarantined():
    assert validate_status({"external_status_code": 2}, 7, {1, 2})[1] is None
    assert validate_status({"external_status_code": 7}, 7, {1, 2})[1][0] == "UNKNOWN_STATUS"


def test_route_needs_code_and_name():
    ok, _ = validate_coded({"route_of_administration_code": "1", "route_of_administration_name": "INTRAVENOUS"},
                           7, "route_of_administration_code", "route_of_administration_name")
    assert ok == {"drug_code": 7, "code": 1, "name": "INTRAVENOUS"}
    assert validate_coded({}, 7, "route_of_administration_code", "route_of_administration_name")[1][0] == "MISSING_FIELD"


@pytest.mark.parametrize("raw, expected", [(".5", Decimal("0.5")), (" 20 ", Decimal("20")), ("", None), (None, None)])
def test_parse_decimal(raw, expected):
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "NaN", "Infinity"])
def test_parse_decimal_rejects_non_numbers(raw):
    with pytest.raises(ValueError):
        parse_decimal(raw)
