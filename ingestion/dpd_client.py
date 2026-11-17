"""Health Canada Drug Product Database (DPD) API client (read-only, no login).

Docs: https://health-products.canada.ca/api/documentation/dpd-documentation-en.html

Endpoints used:
  /drugproduct/?din=<din>        product: drug_code, drug_identification_number, brand_name, ...
  /activeingredient/?id=<code>   ingredients and strengths
  /status/?id=<code>             current marketing status (external_status_code)
  /form/?id=<code>               dosage forms
  /route/?id=<code>              routes of administration

Field names follow the published docs; confirm them against a live response
before relying on the loader (tests use saved, hand-written responses).
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://health-products.canada.ca/api/drug"
DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 3
RETRY_STATUSES = {429, 500, 502, 503, 504}


class DpdAPIError(Exception):
    """The DPD API returned an error or an unexpected response."""


class DpdClient:
    def __init__(self, base_url: str | None = None, timeout: float = DEFAULT_TIMEOUT, sleep=time.sleep):
        self.base_url = (base_url or os.environ.get("DPD_API_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self._client = httpx.Client(timeout=timeout)
        self._sleep = sleep

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "DpdClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def get(self, endpoint: str, **params: Any) -> list[dict[str, Any]]:
        """GET one endpoint. Always returns a list (the API returns an object for single results)."""

        query = {"lang": "en", "type": "json", **params}
        url = f"{self.base_url}/{endpoint.strip('/')}/"
        response: httpx.Response | None = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._client.get(url, params=query)
            except httpx.TransportError as error:
                if attempt == MAX_RETRIES:
                    raise DpdAPIError(f"{endpoint}: network error {error}") from error
                self._sleep(2 ** attempt)
                continue
            if response.status_code in RETRY_STATUSES and attempt < MAX_RETRIES:
                self._sleep(2 ** attempt)
                continue
            break

        assert response is not None
        if response.status_code != 200:
            raise DpdAPIError(f"{endpoint}: HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as error:
            raise DpdAPIError(f"{endpoint}: response is not JSON") from error

        if isinstance(payload, dict):
            return [payload] if payload else []
        if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
            return payload
        raise DpdAPIError(f"{endpoint}: expected a JSON object or list of objects")
