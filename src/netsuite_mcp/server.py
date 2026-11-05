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
"""FastMCP factory and the per-request credential middleware."""

import contextvars
import uuid
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ._arguments import EnvelopeFastMCP
from .api_client import NetSuiteClient, NetSuiteCredentials
from .config import Settings

HEADER_ACCOUNT_ID = "X-Netsuite-Account-Id"
HEADER_CONSUMER_KEY = "X-Netsuite-Consumer-Key"
HEADER_CONSUMER_SECRET = "X-Netsuite-Consumer-Secret"
HEADER_TOKEN_ID = "X-Netsuite-Token-Id"
HEADER_TOKEN_SECRET = "X-Netsuite-Token-Secret"
REQUIRED_HEADERS = [HEADER_ACCOUNT_ID, HEADER_CONSUMER_KEY, HEADER_CONSUMER_SECRET, HEADER_TOKEN_ID, HEADER_TOKEN_SECRET]

# Per-request isolation: the middleware sets these for the duration of one
# HTTP request and resets them in `finally`. Nothing credential-related is
# ever stored at module level (AC17).
_credentials_var: contextvars.ContextVar[NetSuiteCredentials | None] = contextvars.ContextVar(
    "netsuite_credentials", default=None
)
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("netsuite_request_id", default="-")

INSTRUCTIONS = (
    "NetSuite is a cloud ERP/CRM suite: customers, vendors, sales orders, invoices and "
    "hundreds of other record types, account-specific (every account can add custom record "
    "types and custom fields on standard ones).\n"
    "Usage (discover-then-act):\n"
    "- netsuite_list_record_types -> confirm the record type name exists in this account.\n"
    "- netsuite_get_record_schema -> learn required/optional fields before writing.\n"
    "- netsuite_run_suiteql -> find candidate records with a SELECT statement (the only "
    "way to filter/search/join; the plain list tool only pages through ids).\n"
    "- netsuite_get_record -> read one record's full detail.\n"
    "- netsuite_create_record / netsuite_update_record -> write; "
    "netsuite_delete_record needs confirm: true and is permanent.\n"
    "Lists and SuiteQL page with limit/offset; has_more says whether to page on."
)


def get_client_from_context() -> NetSuiteClient | None:
    """The client for the current request, or None outside a request."""
    credentials = _credentials_var.get()
    if credentials is None:
        return None
    return NetSuiteClient(credentials)


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


class GatewayCredentialsMiddleware:
    """Reads the five X-Netsuite-* headers on /mcp requests; 401 when any is missing."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        values = {
            HEADER_ACCOUNT_ID: _clean(request.headers.get(HEADER_ACCOUNT_ID)),
            HEADER_CONSUMER_KEY: _clean(request.headers.get(HEADER_CONSUMER_KEY)),
            HEADER_CONSUMER_SECRET: _clean(request.headers.get(HEADER_CONSUMER_SECRET)),
            HEADER_TOKEN_ID: _clean(request.headers.get(HEADER_TOKEN_ID)),
            HEADER_TOKEN_SECRET: _clean(request.headers.get(HEADER_TOKEN_SECRET)),
        }
        missing = [h for h in REQUIRED_HEADERS if not values[h]]
        if missing:
            await JSONResponse(
                {
                    "error": "Missing credentials",
                    "message": f"Missing header(s): {', '.join(missing)}",
                    "required_headers": REQUIRED_HEADERS,
                },
                status_code=401,
            )(scope, receive, send)
            return

        request_id = (request.headers.get("x-request-id") or "").strip()[:64] or uuid.uuid4().hex[:12]
        creds = NetSuiteCredentials(
            account_id=values[HEADER_ACCOUNT_ID],
            consumer_key=values[HEADER_CONSUMER_KEY],
            consumer_secret=values[HEADER_CONSUMER_SECRET],
            token_id=values[HEADER_TOKEN_ID],
            token_secret=values[HEADER_TOKEN_SECRET],
        )
        creds_token = _credentials_var.set(creds)
        rid_token = request_id_var.set(request_id)
        try:
            await self.app(scope, receive, send)
        finally:
            _credentials_var.reset(creds_token)
            request_id_var.reset(rid_token)


def create_mcp_server(settings: Settings | None = None) -> FastMCP:
    """Build the FastMCP server and register every tool."""
    mcp = EnvelopeFastMCP(
        name="netsuite-mcp",
        instructions=INSTRUCTIONS,
        # The service sits behind the gateway, so Host is never localhost;
        # DNS-rebinding protection would reject those requests with 421.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        stateless_http=True,
    )

    client_factory: Callable[[], NetSuiteClient | None] = get_client_from_context

    from .tools import metadata, query, records

    for module in (metadata, records, query):
        module.register(mcp, client_factory)

    return mcp
