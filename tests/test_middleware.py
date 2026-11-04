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
"""Credential middleware: 401 on any missing header, per-request isolation (AC14, AC17, TC15, TC18)."""

import asyncio

import pytest
from starlette.testclient import TestClient

from netsuite_mcp.__main__ import build_http_app
from netsuite_mcp.server import (
    GatewayCredentialsMiddleware,
    REQUIRED_HEADERS,
    _credentials_var,
    create_mcp_server,
    get_client_from_context,
)

MCP_HEADERS = {"Accept": "application/json, text/event-stream"}
LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
CREDS = {
    "X-Netsuite-Account-Id": "1234567_SB1",
    "X-Netsuite-Consumer-Key": "consumer-key",
    "X-Netsuite-Consumer-Secret": "consumer-secret",
    "X-Netsuite-Token-Id": "token-id",
    "X-Netsuite-Token-Secret": "token-secret",
}


def _client() -> TestClient:
    return TestClient(build_http_app(create_mcp_server()))


def test_health_is_local():
    with _client() as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.parametrize("missing", list(CREDS))
def test_missing_any_required_header_returns_401_listing_names(missing):
    headers = {k: v for k, v in CREDS.items() if k != missing}
    with _client() as client:
        resp = client.post("/mcp", json=LIST_TOOLS, headers=MCP_HEADERS | headers)
    assert resp.status_code == 401
    assert resp.json()["required_headers"] == REQUIRED_HEADERS
    assert missing in resp.json()["message"]


def test_no_headers_at_all_returns_401_listing_every_header():
    with _client() as client:
        resp = client.post("/mcp", json=LIST_TOOLS, headers=MCP_HEADERS)
    assert resp.status_code == 401
    for header in REQUIRED_HEADERS:
        assert header in resp.json()["message"]


def test_tools_list_over_http():
    with _client() as client:
        resp = client.post("/mcp", json=LIST_TOOLS, headers=MCP_HEADERS | CREDS)
    assert resp.status_code == 200
    for name in ("netsuite_list_record_types", "netsuite_run_suiteql", "netsuite_delete_record"):
        assert name in resp.text


def _run_middleware(headers: list[tuple[bytes, bytes]]) -> dict:
    seen: dict = {}

    async def app(scope, receive, send):
        seen["credentials"] = _credentials_var.get()
        seen["client"] = get_client_from_context()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    asyncio.run(GatewayCredentialsMiddleware(app)({"type": "http", "path": "/mcp", "headers": headers}, receive, send))
    return seen


def _header_tuples(values: dict[str, str]) -> list[tuple[bytes, bytes]]:
    return [(k.lower().encode(), v.encode()) for k, v in values.items()]


def test_credentials_reach_request_context_and_are_reset():
    seen = _run_middleware(_header_tuples(CREDS))
    client = seen["client"]
    assert client.account_id == "1234567_SB1"
    assert client.base_url == "https://1234567-sb1.suitetalk.api.netsuite.com/services/rest"
    assert _credentials_var.get() is None


def test_concurrent_requests_do_not_share_credentials():
    """Two different NetSuite accounts in flight at once never cross-contaminate (AC17, TC18)."""
    observed: list[tuple[str, str]] = []

    async def app(scope, receive, send):
        before = _credentials_var.get().account_id
        await asyncio.sleep(0.01)
        observed.append((before, _credentials_var.get().account_id))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    middleware = GatewayCredentialsMiddleware(app)

    def scope(account_id: bytes) -> dict:
        headers = _header_tuples(CREDS | {"X-Netsuite-Account-Id": account_id.decode()})
        return {"type": "http", "path": "/mcp", "headers": headers}

    async def both():
        await asyncio.gather(
            middleware(scope(b"tenant-a"), receive, send),
            middleware(scope(b"tenant-b"), receive, send),
        )

    asyncio.run(both())
    assert sorted(observed) == [("tenant-a", "tenant-a"), ("tenant-b", "tenant-b")]


def test_credentials_repr_hides_secrets():
    seen = _run_middleware(_header_tuples(CREDS))
    rendered = repr(seen["credentials"])
    assert "consumer-secret" not in rendered
    assert "token-secret" not in rendered
    assert "consumer-key" not in rendered
    assert "token-id" not in rendered
