from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from src.utils.logging import RunLogger

logger = logging.getLogger(__name__)


class TripletexClient:
    """Async HTTP client for the Tripletex REST API."""

    def __init__(self, base_url: str, session_token: str, run_logger: RunLogger | None = None):
        self.base_url = base_url.rstrip("/")
        self.auth = httpx.BasicAuth(username="0", password=session_token)
        self.client = httpx.AsyncClient(timeout=30.0)
        self.call_count = 0
        self.error_count = 0
        self.run_logger = run_logger

    async def request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> dict:
        """Make an API call to Tripletex. Returns {status_code, body, ok}."""
        url = f"{self.base_url}{path}"
        self.call_count += 1

        start = time.monotonic()
        try:
            response = await self.client.request(
                method=method.upper(),
                url=url,
                auth=self.auth,
                params=params,
                json=json_body,
            )
            duration_ms = (time.monotonic() - start) * 1000

            try:
                body = response.json()
            except Exception:
                body = response.text

            is_ok = response.status_code < 400
            if not is_ok:
                self.error_count += 1

            logger.info(
                f"API {method.upper()} {path} -> {response.status_code} ({duration_ms:.0f}ms)"
                f" | calls={self.call_count} errors={self.error_count}"
            )
            if not is_ok:
                logger.warning(f"API error response: {json.dumps(body, ensure_ascii=False, default=str)[:500]}")

            # Run log
            if self.run_logger:
                body_preview = json.dumps(body, ensure_ascii=False, default=str)
                self.run_logger.log_api_call(method.upper(), path, response.status_code, duration_ms, body_preview)

            return {
                "status_code": response.status_code,
                "body": body,
                "ok": is_ok,
            }

        except httpx.HTTPError as e:
            duration_ms = (time.monotonic() - start) * 1000
            self.error_count += 1
            logger.error(f"API {method.upper()} {path} -> HTTP error ({duration_ms:.0f}ms): {e}")
            if self.run_logger:
                self.run_logger.log_api_call(method.upper(), path, 0, duration_ms, str(e))
            return {
                "status_code": 0,
                "body": {"error": str(e)},
                "ok": False,
            }

    async def close(self):
        await self.client.aclose()
