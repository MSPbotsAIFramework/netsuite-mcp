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
"""Minimal OAuth 1.0a (HMAC-SHA256) request signing for NetSuite Token-Based Authentication.

NetSuite TBA signs every request with a fresh nonce/timestamp (RFC 5849 section 3). This is
implemented directly with the standard library (hmac/hashlib/secrets) rather than adding
requests-oauthlib, which would pull in `requests` as a second HTTP stack alongside httpx for a
few dozen lines of signing logic (Decisions made by DEV01, PRD-21337 tech design).

Only what this connector needs: HMAC-SHA256 signing of a URL with optional query parameters, no
form-encoded body parameters (every outbound call here sends a JSON body, which RFC 5849 does not
include in the signature base string) and no request-token/access-token handshake (TBA credentials
are long-lived, configured once in NetSuite's own UI).
"""

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import quote, urlsplit

SIGNATURE_METHOD = "HMAC-SHA256"
OAUTH_VERSION = "1.0"


def _encode(value: object) -> str:
    """RFC 3986 percent-encoding: unreserved chars (A-Za-z0-9-._~) are never escaped."""
    return quote(str(value), safe="~")


def _base_string(method: str, url: str, params: dict[str, str]) -> str:
    split = urlsplit(url)
    base_url = f"{split.scheme}://{split.netloc}{split.path}"
    normalized = "&".join(f"{_encode(k)}={_encode(v)}" for k, v in sorted(params.items()))
    return "&".join([method.upper(), _encode(base_url), _encode(normalized)])


def _signing_key(consumer_secret: str, token_secret: str) -> str:
    return f"{_encode(consumer_secret)}&{_encode(token_secret)}"


def authorization_header(
    method: str,
    url: str,
    *,
    realm: str,
    consumer_key: str,
    consumer_secret: str,
    token_id: str,
    token_secret: str,
    query_params: dict[str, str] | None = None,
    nonce: str | None = None,
    timestamp: str | None = None,
) -> str:
    """The `Authorization: OAuth ...` header value for one request.

    ``query_params`` are the request's own query-string parameters (e.g. `limit`, `offset`);
    they are part of the signature base string per RFC 5849 even though they travel in the URL,
    not the Authorization header. A fresh nonce and timestamp are generated per call unless
    supplied (tests pass fixed values for deterministic assertions).
    """
    nonce = nonce or secrets.token_hex(16)
    timestamp = timestamp or str(int(time.time()))

    oauth_params = {
        "oauth_consumer_key": consumer_key,
        "oauth_token": token_id,
        "oauth_signature_method": SIGNATURE_METHOD,
        "oauth_timestamp": timestamp,
        "oauth_nonce": nonce,
        "oauth_version": OAUTH_VERSION,
    }
    all_params = {**(query_params or {}), **oauth_params}
    base_string = _base_string(method, url, all_params)
    key = _signing_key(consumer_secret, token_secret)
    signature = base64.b64encode(hmac.new(key.encode(), base_string.encode(), hashlib.sha256).digest()).decode()

    header_params = {**oauth_params, "oauth_signature": signature}
    rendered = ", ".join(f'{k}="{_encode(v)}"' for k, v in header_params.items())
    return f'OAuth realm="{_encode(realm)}", {rendered}'
