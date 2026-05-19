from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import urljoin

import requests


class AkahuError(RuntimeError):
    pass


class AkahuClient:
    def __init__(self, base_url: str, app_token: str, user_token: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/") + "/"
        self.app_token = app_token
        self.user_token = user_token
        self.timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        if not self.app_token or not self.user_token:
            raise AkahuError("Akahu tokens are not configured")
        return {
            "Authorization": f"Bearer {self.user_token}",
            "X-Akahu-Id": self.app_token,
            "Accept": "application/json",
        }

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = urljoin(self.base_url, path.lstrip("/"))
        response = requests.get(url, headers=self.headers, params=params or {}, timeout=self.timeout)
        if response.status_code >= 400:
            raise AkahuError(f"Akahu request failed {response.status_code}: {response.text[:500]}")
        return response.json()

    def _paged(self, path: str, params: dict | None = None) -> list[dict]:
        items: list[dict] = []
        cursor = None
        while True:
            request_params = dict(params or {})
            if cursor:
                request_params["cursor"] = cursor
            payload = self._get(path, request_params)
            page_items = payload.get("items")
            if page_items is None:
                page_items = payload.get("data")
            if page_items is None and isinstance(payload, list):
                page_items = payload
            if page_items is None:
                page_items = []
            items.extend(page_items)

            cursor_payload = payload.get("cursor") if isinstance(payload, dict) else None
            next_cursor = None
            if isinstance(cursor_payload, dict):
                next_cursor = cursor_payload.get("next")
            elif isinstance(cursor_payload, str):
                next_cursor = cursor_payload
            if not next_cursor:
                break
            cursor = next_cursor
        return items

    def accounts(self) -> list[dict]:
        return self._paged("/v1/accounts")

    def transactions(self, account_id: str, *, since_days: int | None = None) -> list[dict]:
        params: dict[str, str] = {}
        if since_days:
            params["start"] = (date.today() - timedelta(days=since_days)).isoformat()
        return self._paged(f"/v1/accounts/{account_id}/transactions", params=params)

