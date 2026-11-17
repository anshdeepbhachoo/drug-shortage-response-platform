"""Health Product Shortages Canada API client (read-only).

Official base URL and auth flow:
https://healthproductshortages.ca/api/v1
Docs: https://healthproductshortages.ca/blog/52
"""

from __future__ import annotations

import os
import time
from typing import Any, Iterator

import httpx

DEFAULT_BASE_URL = "https://healthproductshortages.ca/api/v1"
DEFAULT_TIMEOUT = 30.0
MAX_RETRIES_ON_429 = 3


class ShortageAPIError(Exception):
    """Raised when the shortage API returns an error or unexpected response."""


def _as_non_negative_int(value: Any, field: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ShortageAPIError(f"Search response field '{field}' is not an integer") from exc
    if number < 0:
        raise ShortageAPIError(f"Search response field '{field}' must be >= 0")
    return number


def validate_search_payload(payload: Any) -> dict[str, Any]:
    """Ensure a search response has the expected list-page shape."""
    if not isinstance(payload, dict):
        raise ShortageAPIError("Search response is not a JSON object")
    data = payload.get("data")
    if not isinstance(data, list):
        raise ShortageAPIError("Search response 'data' must be a list")
    # Validate pagination fields when present so iter_search cannot crash oddly.
    if "remaining" in payload and payload["remaining"] is not None:
        _as_non_negative_int(payload["remaining"], "remaining")
    if "limit" in payload and payload["limit"] is not None:
        _as_non_negative_int(payload["limit"], "limit")
    return payload


class ShortageClient:
    """Small client: login once, then paginate search results."""

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.email = email if email is not None else os.environ.get("SHORTAGE_API_EMAIL", "")
        self.password = (
            password if password is not None else os.environ.get("SHORTAGE_API_PASSWORD", "")
        )
        self.base_url = (
            base_url
            if base_url is not None
            else os.environ.get("SHORTAGE_API_BASE_URL", DEFAULT_BASE_URL)
        ).rstrip("/")
        self.timeout = timeout
        self._token: str | None = None
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ShortageClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def login(self) -> str:
        if not self.email or not self.password:
            raise ShortageAPIError(
                "SHORTAGE_API_EMAIL and SHORTAGE_API_PASSWORD must be set"
            )

        response = self._client.post(
            f"{self.base_url}/login",
            data={"email": self.email, "password": self.password},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code == 403:
            raise ShortageAPIError("Login failed: account not verified (HTTP 403)")
        if response.status_code == 400:
            raise ShortageAPIError("Login failed: bad email/password (HTTP 400)")
        if response.status_code != 200:
            raise ShortageAPIError(f"Login failed: HTTP {response.status_code}")

        token = response.headers.get("auth-token")
        if not token:
            raise ShortageAPIError("Login succeeded but auth-token header was missing")

        self._token = token
        return token

    def search_page(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        orderby: str = "updated_date",
        order: str = "desc",
        **filters: Any,
    ) -> dict[str, Any]:
        """Fetch one page of search results. Logs in automatically if needed."""
        if not self._token:
            self.login()

        # Caller filters may override default sort (e.g. orderby=id for bulk extract).
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "orderby": orderby,
            "order": order,
        }
        params.update({k: v for k, v in filters.items() if v is not None})

        response: httpx.Response | None = None
        for attempt in range(MAX_RETRIES_ON_429 + 1):
            response = self._client.get(
                f"{self.base_url}/search",
                params=params,
                headers={"auth-token": self._token or ""},
            )
            if response.status_code == 429 and attempt < MAX_RETRIES_ON_429:
                # Rate limit is 1000 req/hour; brief backoff then retry.
                time.sleep(2 ** attempt)
                continue
            if response.status_code == 403:
                # Token may have expired; try one fresh login.
                self.login()
                response = self._client.get(
                    f"{self.base_url}/search",
                    params=params,
                    headers={"auth-token": self._token or ""},
                )
            break

        assert response is not None
        if response.status_code == 429:
            raise ShortageAPIError("Rate limited by shortage API (HTTP 429)")
        if response.status_code != 200:
            raise ShortageAPIError(f"Search failed: HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise ShortageAPIError("Search response is not valid JSON") from exc
        return validate_search_payload(payload)

    def iter_search(
        self,
        *,
        limit: int = 100,
        max_pages: int | None = None,
        **filters: Any,
    ) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
        """Yield (request_params, page) until remaining results are exhausted."""
        offset = 0
        pages = 0
        while True:
            request_params = {
                "limit": limit,
                "offset": offset,
                **{k: v for k, v in filters.items() if v is not None},
            }
            page = self.search_page(limit=limit, offset=offset, **filters)
            yield request_params, page
            pages += 1
            remaining = _as_non_negative_int(page.get("remaining", 0), "remaining")
            page_limit = _as_non_negative_int(page.get("limit", limit), "limit")
            offset += page_limit
            if remaining <= 0:
                break
            if max_pages is not None and pages >= max_pages:
                break
