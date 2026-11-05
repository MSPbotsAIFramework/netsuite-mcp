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
"""Request shapes, OAuth 1.0a signing, error mapping and tool behaviour against a mocked NetSuite API."""

import json
import re

import httpx
import pytest

from netsuite_mcp import api_client, oauth1
from netsuite_mcp.api_client import NetSuiteClient, NetSuiteCredentials, NetSuiteError, normalize_account_id
from netsuite_mcp.server import _credentials_var, create_mcp_server

CREDS = NetSuiteCredentials("1234567", "consumer-key", "consumer-secret", "token-id", "token-secret")
BASE = "https://1234567.suitetalk.api.netsuite.com/services/rest"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(api_client.asyncio, "sleep", instant)


class Recorder:
    """Mock transport: replies by method + path below /services/rest/."""

    def __init__(self, routes=None, default=None):
        self.requests: list[httpx.Request] = []
        self.routes = routes or {}
        self.default = default if default is not None else {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/services/rest/")
        reply = self.routes.get(f"{request.method} {path}", self.routes.get(path, self.default))
        if callable(reply):
            return reply(request)
        return httpx.Response(200, json=reply)

    def calls(self, path: str, method: str | None = None) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == f"/services/rest/{path}" and (method is None or r.method == method)]


def body(request: httpx.Request):
    return json.loads(request.content) if request.content else None


async def _call(monkeypatch, recorder: Recorder, tool: str, args: dict):
    http = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    monkeypatch.setattr(api_client, "_get_shared_http_client", lambda: http)
    ctx = _credentials_var.set(CREDS)
    try:
        content, _ = await create_mcp_server().call_tool(tool, args)
    finally:
        _credentials_var.reset(ctx)
    return json.loads(content[0].text)


def _client(recorder: Recorder) -> NetSuiteClient:
    return NetSuiteClient(CREDS, httpx.AsyncClient(transport=httpx.MockTransport(recorder)))


# ---- account id / base URL --------------------------------------------------


@pytest.mark.parametrize(
    "account_id,subdomain",
    [("1234567", "1234567"), ("1234567_SB1", "1234567-sb1"), (" 1234567_SB2 ", "1234567-sb2"), ("ABC_DEF_GH1", "abc-def-gh1")],
)
def test_normalize_account_id(account_id, subdomain):
    assert normalize_account_id(account_id) == subdomain


def test_client_base_url_derived_from_account_id():
    client = NetSuiteClient(NetSuiteCredentials("1234567_SB1", "ck", "cs", "ti", "ts"))
    assert client.base_url == "https://1234567-sb1.suitetalk.api.netsuite.com/services/rest"


# ---- OAuth 1.0a signature shape and freshness (TC19, AC14-AC17) -------------


def _parse_oauth_header(value: str) -> dict:
    assert value.startswith("OAuth ")
    return dict(re.findall(r'(\w+)="([^"]*)"', value))


def test_oauth1_signature_header_shape_and_freshness():
    header1 = oauth1.authorization_header(
        "GET",
        f"{BASE}/record/v1/customer",
        realm="1234567",
        consumer_key="ck",
        consumer_secret="cs",
        token_id="ti",
        token_secret="ts",
        query_params={"limit": "50"},
    )
    header2 = oauth1.authorization_header(
        "GET",
        f"{BASE}/record/v1/customer",
        realm="1234567",
        consumer_key="ck",
        consumer_secret="cs",
        token_id="ti",
        token_secret="ts",
        query_params={"limit": "50"},
    )
    fields = _parse_oauth_header(header1)
    assert fields["realm"] == "1234567"
    assert fields["oauth_consumer_key"] == "ck"
    assert fields["oauth_token"] == "ti"
    assert fields["oauth_signature_method"] == "HMAC-SHA256"
    assert fields["oauth_version"] == "1.0"
    assert fields["oauth_timestamp"].isdigit()
    assert len(fields["oauth_nonce"]) > 0
    assert len(fields["oauth_signature"]) > 0
    # Fresh nonce/timestamp each call -> a different signature even for the same request.
    fields2 = _parse_oauth_header(header2)
    assert fields["oauth_nonce"] != fields2["oauth_nonce"]
    assert fields["oauth_signature"] != fields2["oauth_signature"]


def test_oauth1_signature_is_deterministic_for_fixed_nonce_and_timestamp():
    header = oauth1.authorization_header(
        "GET", f"{BASE}/record/v1/customer", realm="r", consumer_key="ck", consumer_secret="cs",
        token_id="ti", token_secret="ts", query_params={}, nonce="fixed-nonce", timestamp="1700000000",
    )
    again = oauth1.authorization_header(
        "GET", f"{BASE}/record/v1/customer", realm="r", consumer_key="ck", consumer_secret="cs",
        token_id="ti", token_secret="ts", query_params={}, nonce="fixed-nonce", timestamp="1700000000",
    )
    assert header == again  # same inputs -> same signature (regression anchor, not a live NetSuite fixture)


async def test_requests_carry_a_valid_authorization_header():
    rec = Recorder({"record/v1/customer": {"items": []}})
    await _client(rec).get("record/v1/customer", {"limit": 10})
    fields = _parse_oauth_header(rec.requests[0].headers["authorization"])
    assert fields["realm"] == "1234567"
    assert fields["oauth_consumer_key"] == "consumer-key"
    assert fields["oauth_token"] == "token-id"


# ---- error mapping (AC5, AC7, AC12, AC15, AC16) -----------------------------


async def test_404_is_structured_not_found(monkeypatch):
    rec = Recorder({"record/v1/customer/999": lambda r: httpx.Response(404, json={"title": "Not Found"})})
    result = await _call(monkeypatch, rec, "netsuite_get_record", {"recordType": "customer", "id": "999"})
    assert result["error"]["code"] == "not_found"
    assert result["error"]["retryable"] is False


async def test_validation_rejection_is_structured_invalid_argument(monkeypatch):
    rec = Recorder({
        "POST record/v1/customer": lambda r: httpx.Response(
            400, json={"title": "Bad Request", "o:errorDetails": [{"detail": "companyName is required", "o:errorCode": "INVALID_FLD_VALUE"}]}
        )
    })
    result = await _call(monkeypatch, rec, "netsuite_create_record", {"recordType": "customer", "fields": {}})
    assert result["error"]["code"] == "invalid_argument"
    assert "companyName is required" in result["error"]["message"]
    assert result["error"]["retryable"] is False


@pytest.mark.parametrize("status", [401, 403])
async def test_401_403_mapped_to_unauthorized(monkeypatch, status):
    rec = Recorder({"record/v1/customer": lambda r: httpx.Response(status, json={"title": "unauthorized"})})
    result = await _call(monkeypatch, rec, "netsuite_list_records", {"recordType": "customer"})
    assert result["error"]["code"] == "unauthorized"
    assert result["error"]["retryable"] is False


async def test_429_governance_limit_mapped_to_rate_limited(monkeypatch):
    rec = Recorder({"record/v1/customer": lambda r: httpx.Response(429, json={"title": "Too Many Requests"})})
    result = await _call(monkeypatch, rec, "netsuite_list_records", {"recordType": "customer"})
    assert result["error"] == {"code": "rate_limited", "message": "NetSuite returned HTTP 429 for record/v1/customer: Too Many Requests", "retryable": True}


async def test_suiteql_rejected_statement_is_structured_and_not_parsed_locally(monkeypatch):
    rec = Recorder({
        "POST query/v1/suiteql": lambda r: httpx.Response(
            400, json={"title": "Bad Request", "o:errorDetails": [{"detail": "Only SELECT statements are allowed"}]}
        )
    })
    result = await _call(monkeypatch, rec, "netsuite_run_suiteql", {"q": "DELETE FROM customer"})
    assert result["error"]["code"] == "invalid_argument"
    assert "Only SELECT statements are allowed" in result["error"]["message"]
    # The connector never pre-validates q itself: whatever was sent reached NetSuite verbatim.
    assert body(rec.requests[0])["q"] == "DELETE FROM customer"


async def test_get_retries_5xx_but_writes_do_not():
    replies = iter([httpx.Response(502), httpx.Response(200, json={"foo": "bar"})])
    rec = Recorder({
        "GET record/v1/customer/1": lambda r: next(replies),
        "PATCH record/v1/customer/1": lambda r: httpx.Response(500, json={}),
    })
    client = _client(rec)
    await client.get("record/v1/customer/1")
    with pytest.raises(NetSuiteError):
        await client.patch("record/v1/customer/1", {"companyName": "Acme"})
    assert len(rec.calls("record/v1/customer/1", "GET")) == 2
    assert len(rec.calls("record/v1/customer/1", "PATCH")) == 1


async def test_429_is_retried_even_for_writes():
    replies = iter([httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(204)])
    rec = Recorder({"PATCH record/v1/customer/1": lambda r: next(replies)})
    await _client(rec).patch("record/v1/customer/1", {"companyName": "Acme"})
    assert len(rec.calls("record/v1/customer/1", "PATCH")) == 2


# ---- metadata ----------------------------------------------------------------


async def test_list_record_types_returns_catalog(monkeypatch):
    rec = Recorder({"record/v1/metadata-catalog": {
        "items": [
            {"name": "customer", "links": [{"rel": "self", "href": f"{BASE}/record/v1/metadata-catalog/customer"}]},
            {"name": "salesOrder", "links": [{"rel": "self", "href": f"{BASE}/record/v1/metadata-catalog/salesOrder"}]},
        ]
    }})
    result = await _call(monkeypatch, rec, "netsuite_list_record_types", {})
    assert [t["name"] for t in result["record_types"]] == ["customer", "salesOrder"]
    assert result["count"] == 2
    assert result["record_types"][0]["link"].endswith("/metadata-catalog/customer")


async def test_get_record_schema_returns_fields(monkeypatch):
    rec = Recorder({"record/v1/metadata-catalog/customer": {
        "properties": {
            "companyName": {"type": "string"},
            "email": {"type": "string"},
            "balance": {"type": "number"},
        },
        "required": ["companyName"],
    }})
    result = await _call(monkeypatch, rec, "netsuite_get_record_schema", {"recordType": "customer"})
    assert result["record_type"] == "customer"
    fields = {f["name"]: f for f in result["fields"]}
    assert fields["companyName"] == {"name": "companyName", "type": "string", "required": True}
    assert fields["email"]["required"] is False
    # The request asked NetSuite for the JSON-Schema representation specifically.
    assert rec.requests[0].headers["accept"] == "application/schema+json"


# ---- record list (AC3) --------------------------------------------------------


def _record_items(n: int, record_type: str = "customer") -> list[dict]:
    return [{"links": [{"rel": "self", "href": f"{BASE}/record/v1/{record_type}/{i}"}]} for i in range(n)]


async def test_list_records_default_and_cap_limit(monkeypatch):
    rec = Recorder({"record/v1/customer": {"items": _record_items(3), "hasMore": False, "totalResults": 3}})
    result = await _call(monkeypatch, rec, "netsuite_list_records", {"recordType": "customer"})
    assert rec.requests[0].url.params["limit"] == "50"  # DEFAULT_PAGE_SIZE
    assert [i["id"] for i in result["items"]] == ["0", "1", "2"]
    assert result["has_more"] is False and result["next_offset"] is None

    rec2 = Recorder({"record/v1/customer": {"items": [], "hasMore": False}})
    await _call(monkeypatch, rec2, "netsuite_list_records", {"recordType": "customer", "limit": 200})
    assert rec2.requests[0].url.params["limit"] == "200"  # MAX_PAGE_SIZE hard cap, accepted at the boundary
    # Above the hard cap is rejected by the schema itself before any request is made
    # (test_limit_above_cap_is_rejected in test_tools.py); page_size() is the defense in depth:
    from netsuite_mcp.tools._common import page_size

    assert page_size(500) == 200
    assert page_size(None) == 50


async def test_list_records_marks_has_more(monkeypatch):
    rec = Recorder({"record/v1/customer": {"items": _record_items(2), "hasMore": True, "totalResults": 50}})
    result = await _call(monkeypatch, rec, "netsuite_list_records", {"recordType": "customer", "limit": 2, "offset": 10})
    assert result["has_more"] is True
    assert result["next_offset"] == 12
    assert result["total_results"] == 50


# ---- record CRUD (AC4, AC6, AC8, AC9, AC10) -----------------------------------


async def test_get_record_returns_body(monkeypatch):
    rec = Recorder({"record/v1/customer/42": {"id": "42", "companyName": "Acme"}})
    result = await _call(monkeypatch, rec, "netsuite_get_record", {"recordType": "customer", "id": "42"})
    assert result == {"id": "42", "companyName": "Acme"}


async def test_create_record_returns_new_id_from_location(monkeypatch):
    rec = Recorder({
        "POST record/v1/customer": lambda r: httpx.Response(
            204, headers={"Location": f"{BASE}/record/v1/customer/999"}
        )
    })
    result = await _call(monkeypatch, rec, "netsuite_create_record", {"recordType": "customer", "fields": {"companyName": "Acme"}})
    assert result == {"id": "999", "link": f"{BASE}/record/v1/customer/999", "record_type": "customer"}
    assert body(rec.requests[0]) == {"companyName": "Acme"}


async def test_update_record_patches_only_given_fields(monkeypatch):
    rec = Recorder({"PATCH record/v1/customer/1": lambda r: httpx.Response(204)})
    result = await _call(monkeypatch, rec, "netsuite_update_record", {"recordType": "customer", "id": "1", "fields": {"email": "a@example.com"}})
    assert body(rec.requests[0]) == {"email": "a@example.com"}
    assert result == {"id": "1", "record_type": "customer", "done": True}


async def test_update_record_without_fields_is_rejected(monkeypatch):
    rec = Recorder()
    result = await _call(monkeypatch, rec, "netsuite_update_record", {"recordType": "customer", "id": "1", "fields": {}})
    assert result["error"]["code"] == "invalid_argument"
    assert rec.requests == []


async def test_delete_record_requires_confirm(monkeypatch):
    rec = Recorder()
    result = await _call(monkeypatch, rec, "netsuite_delete_record", {"recordType": "customer", "id": "1"})
    assert result["done"] is False
    assert rec.requests == []


async def test_delete_record_with_confirm_deletes(monkeypatch):
    rec = Recorder({"DELETE record/v1/customer/1": lambda r: httpx.Response(204)})
    result = await _call(monkeypatch, rec, "netsuite_delete_record", {"recordType": "customer", "id": "1", "confirm": True})
    assert result == {"done": True, "deleted_record_type": "customer", "deleted_id": "1"}
    assert rec.calls("record/v1/customer/1", "DELETE")


# ---- SuiteQL (AC11, AC13) -----------------------------------------------------


async def test_run_suiteql_posts_query_and_returns_rows(monkeypatch):
    rec = Recorder({"POST query/v1/suiteql": {
        "items": [{"id": "1", "companyname": "Acme"}],
        "hasMore": False,
        "totalResults": 1,
    }})
    result = await _call(monkeypatch, rec, "netsuite_run_suiteql", {"q": "SELECT id, companyname FROM customer", "params": ["x"]})
    request = rec.requests[0]
    assert body(request) == {"q": "SELECT id, companyname FROM customer", "params": ["x"]}
    assert request.headers["prefer"] == "transient"
    assert result["rows"] == [{"id": "1", "companyname": "Acme"}]
    assert result["has_more"] is False
    assert request.url.params["limit"] == "50"


async def test_run_suiteql_truncates_oversized_result(monkeypatch):
    rec = Recorder({"POST query/v1/suiteql": {
        "items": [{"id": str(i)} for i in range(50)],
        "hasMore": True,
        "totalResults": 500,
    }})
    result = await _call(monkeypatch, rec, "netsuite_run_suiteql", {"q": "SELECT id FROM customer", "limit": 50, "offset": 100})
    assert len(result["rows"]) == 50
    assert result["has_more"] is True
    assert result["next_offset"] == 150
