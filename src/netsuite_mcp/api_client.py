# Copyright 2026 MSPbots.ai
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Async client for NetSuite's SuiteTalk REST Web Services, OAuth 1.0a (TBA) signed."""

import asyncio
import logging
import time
from dataclasses import dataclass
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Any

import httpx

from . import oauth1
from ._json import error_envelope

logger = logging.getLogger("netsuite_mcp.api")

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
_MAX_RETRIES = 2
_MAX_BACKOFF_SECONDS = 20.0
# Whole-call budget including retries; the gateway times a tool call out at 120s.
_CALL_BUDGET_SECONDS = 100.0

# One connection pool for the process. It never holds credentials: the
# Authorization header is signed per request from the caller's own keys, so
# sharing the pool across tenants is safe.
_shared_http_client: httpx.AsyncClient | None = None


class _RejectAllCookies(DefaultCookiePolicy):
    """Never store or send a cookie: the HTTP client is shared by every tenant."""

    def set_ok(self, cookie, request) -> bool:  # noqa: ANN001 - stdlib signature
        return False

    def return_ok(self, cookie, request) -> bool:  # noqa: ANN001 - stdlib signature
        return False


def _no_cookies() -> CookieJar:
    return CookieJar(policy=_RejectAllCookies())


def _get_shared_http_client() -> httpx.AsyncClient:
    global _shared_http_client
    if _shared_http_client is None:
        _shared_http_client = httpx.AsyncClient(cookies=_no_cookies(), timeout=_TIMEOUT)
    return _shared_http_client


# status_code -> (error code, retryable). 0 means no HTTP response at all
# (connection error or timeout). Matches the ticket's error-mapping table (AC14-AC16).
_STATUS_TO_CODE: dict[int, tuple[str, bool]] = {
    0: ("upstream_error", True),
    400: ("invalid_argument", False),
    401: ("unauthorized", False),
    403: ("unauthorized", False),
    404: ("not_found", False),
    422: ("invalid_argument", False),
    429: ("rate_limited", True),
}


def classify_status(status_code: int) -> tuple[str, bool]:
    if status_code in _STATUS_TO_CODE:
        return _STATUS_TO_CODE[status_code]
    if status_code >= 500:
        return "upstream_error", True
    if status_code >= 400:
        return "invalid_argument", False
    return "upstream_error", True


class NetSuiteError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"NetSuite API error {status_code}: {message}")

    def to_envelope(self) -> str:
        code, retryable = classify_status(self.status_code)
        return error_envelope(code, self.message, retryable)


def normalize_account_id(account_id: str) -> str:
    """Account id -> subdomain segment: lowercase, underscores become hyphens.

    e.g. ``1234567_SB1`` -> ``1234567-sb1`` (NetSuite's documented sandbox-account convention,
    Open Question Q3 in the tech design: not re-verified against a live account this pass).
    """
    return account_id.strip().lower().replace("_", "-")


@dataclass(frozen=True)
class NetSuiteCredentials:
    account_id: str
    consumer_key: str
    consumer_secret: str
    token_id: str
    token_secret: str

    def __repr__(self) -> str:  # never leak secrets into logs or tracebacks
        return f"NetSuiteCredentials(account_id={self.account_id!r}, consumer_key=<redacted>, consumer_secret=<redacted>, token_id=<redacted>, token_secret=<redacted>)"


class NetSuiteClient:
    """Thin wrapper over NetSuite's SuiteTalk REST Web Services for one caller's TBA credentials."""

    def __init__(self, credentials: NetSuiteCredentials, http_client: httpx.AsyncClient | None = None):
        self._credentials = credentials
        self._http = http_client
        self.account_id = credentials.account_id
        self.base_url = f"https://{normalize_account_id(credentials.account_id)}.suitetalk.api.netsuite.com/services/rest"

    def _authorization(self, method: str, url: str, query: dict[str, str]) -> str:
        c = self._credentials
        return oauth1.authorization_header(
            method,
            url,
            realm=c.account_id,
            consumer_key=c.consumer_key,
            consumer_secret=c.consumer_secret,
            token_id=c.token_id,
            token_secret=c.token_secret,
            query_params=query,
        )

    async def get(self, path: str, params: dict[str, Any] | None = None, accept: str | None = None) -> Any:
        return (await self.request("GET", path, params=params, accept=accept))[0]

    async def post(self, path: str, body: Any = None, params: dict[str, Any] | None = None, prefer: str | None = None) -> tuple[Any, httpx.Response]:
        return await self.request("POST", path, params=params, body=body, prefer=prefer)

    async def patch(self, path: str, body: Any = None) -> tuple[Any, httpx.Response]:
        return await self.request("PATCH", path, body=body)

    async def delete(self, path: str) -> tuple[Any, httpx.Response]:
        return await self.request("DELETE", path)

    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: Any = None,
        accept: str | None = None,
        prefer: str | None = None,
    ) -> tuple[Any, httpx.Response]:
        http = self._http or _get_shared_http_client()
        url = f"{self.base_url}/{path.lstrip('/')}"
        query = {k: _param_value(v) for k, v in (params or {}).items() if v is not None}
        query_str = {k: str(v) for k, v in query.items()}

        headers = {
            "Authorization": self._authorization(method, url, query_str),
            "Accept": accept or "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        if body is not None:
            headers["Content-Type"] = "application/json"

        started = time.monotonic()
        attempt = 0
        while True:
            t0 = time.monotonic()
            resp: httpx.Response | None = None
            error: NetSuiteError | None = None
            try:
                resp = await http.request(method, url, params=query, json=body, headers=headers)
                status = resp.status_code
            except httpx.TimeoutException:
                status, error = 0, NetSuiteError(0, f"Timed out calling NetSuite ({method} {path})")
            except httpx.RequestError as e:
                status, error = 0, NetSuiteError(0, f"Network error calling NetSuite ({method} {path}): {type(e).__name__}")

            logger.info("netsuite_api method=%s path=%s status=%s ms=%d attempt=%d", method, path, status, (time.monotonic() - t0) * 1000, attempt)

            # 429 is refused before processing, so any method may retry it; other
            # failures are retried only for reads, so a write never runs twice.
            retryable = status == 429 or (method == "GET" and (status == 0 or status >= 500))
            if retryable and attempt < _MAX_RETRIES:
                delay = _retry_delay(resp, attempt)
                if time.monotonic() - started + delay < _CALL_BUDGET_SECONDS:
                    attempt += 1
                    await asyncio.sleep(delay)
                    # A fresh request needs a fresh signature (nonce/timestamp): re-sign.
                    headers["Authorization"] = self._authorization(method, url, query_str)
                    continue
            if error is not None:
                raise error
            assert resp is not None
            if resp.status_code >= 400:
                raise NetSuiteError(resp.status_code, _error_message(resp, path))
            if not resp.content or not resp.content.strip():
                return None, resp
            try:
                return resp.json(), resp
            except ValueError:
                return {"raw_response": resp.text[:2000]}, resp


def _param_value(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _retry_delay(resp: httpx.Response | None, attempt: int) -> float:
    if resp is not None:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, min(float(retry_after), _MAX_BACKOFF_SECONDS))
            except ValueError:
                pass
    return min(float(2**attempt), _MAX_BACKOFF_SECONDS)


def _error_message(resp: httpx.Response, path: str) -> str:
    fallback = f"NetSuite returned HTTP {resp.status_code} for {path}"
    try:
        payload = resp.json()
    except ValueError:
        text = resp.text.strip()
        if text.lower().startswith("<"):
            return fallback  # an HTML error page, not useful to the agent
        return f"{fallback}: {text[:300]}" if text else fallback
    if isinstance(payload, dict):
        details = payload.get("o:errorDetails")
        if isinstance(details, list) and details:
            message = "; ".join(str(d.get("detail") or d.get("o:errorCode") or d) for d in details if d)
            if message:
                return f"{fallback}: {message[:500]}"
        title = payload.get("title") or payload.get("message")
        if title:
            return f"{fallback}: {str(title)[:500]}"
    return fallback
