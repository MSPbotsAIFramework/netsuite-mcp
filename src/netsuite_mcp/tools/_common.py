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
"""Shared argument types, paging helpers, confirmation and the error-handling wrapper."""

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped, error_envelope
from ..api_client import NetSuiteClient, NetSuiteError
from ..config import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

logger = logging.getLogger("netsuite_mcp.tools")

ClientFactory = Callable[[], NetSuiteClient | None]

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True)
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
WRITES_IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True)

NOT_CONFIGURED = error_envelope(
    "not_configured",
    "NetSuite credentials are missing for this request (X-Netsuite-Account-Id, "
    "X-Netsuite-Consumer-Key, X-Netsuite-Consumer-Secret, X-Netsuite-Token-Id, "
    "X-Netsuite-Token-Secret).",
    False,
)

RecordType = Annotated[str, Field(description="NetSuite record type, e.g. customer, salesOrder, invoice. See netsuite_list_record_types.", min_length=1)]
RecordId = Annotated[str, Field(description="Internal id of the record.", min_length=1)]
Limit = Annotated[int | None, Field(description=f"Max rows per page (default {DEFAULT_PAGE_SIZE}, hard cap {MAX_PAGE_SIZE}).", ge=1, le=MAX_PAGE_SIZE)]
Offset = Annotated[int | None, Field(description="Row position to start from (0-based). Pass next_offset from the previous result to continue.", ge=0)]
Confirm = Annotated[bool | None, Field(description="Must be true, set only after the user approved this exact deletion.")]
Fields = Annotated[dict[str, Any], Field(description="Record field values, close to NetSuite's own JSON shape. See netsuite_get_record_schema for the field list.")]


def compact(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None}


def page_size(limit: int | None) -> int:
    return min(limit, MAX_PAGE_SIZE) if limit else DEFAULT_PAGE_SIZE


def not_confirmed(action: str) -> dict:
    return {
        "done": False,
        "action": action,
        "message": "Not done. Describe this deletion to the user and call again with confirm: true only after they approve it.",
    }


def self_link(item: dict) -> str | None:
    for link in item.get("links") or []:
        if link.get("rel") == "self":
            return link.get("href")
    return None


def id_from_link(href: str | None) -> str | None:
    if not href:
        return None
    return href.rstrip("/").rsplit("/", 1)[-1]


def paged_items(payload: Any, limit: int, offset: int) -> dict:
    """Normalize a NetSuite record-list or SuiteQL response into {items/rows, count, offset, has_more, next_offset, total_results}."""
    payload = payload or {}
    items = payload.get("items") if isinstance(payload, dict) else None
    items = items if isinstance(items, list) else []
    has_more = bool(payload.get("hasMore")) if isinstance(payload, dict) else False
    count = len(items)
    result = {
        "count": count,
        "offset": offset,
        "has_more": has_more,
        "next_offset": offset + count if has_more else None,
    }
    if isinstance(payload, dict) and payload.get("totalResults") is not None:
        result["total_results"] = payload["totalResults"]
    return items, result


async def run_tool(
    name: str,
    client_factory: ClientFactory,
    action: Callable[[NetSuiteClient], Awaitable[Any]],
) -> str:
    """Resolve the caller's client, run ``action``, and always return a string.

    Errors never escape as exceptions: they become the standard error envelope.
    """
    client = client_factory()
    if client is None:
        return NOT_CONFIGURED
    started = time.monotonic()
    try:
        result = await action(client)
    except NetSuiteError as e:
        logger.info("tool=%s outcome=error status=%s ms=%d", name, e.status_code, _ms(started))
        return e.to_envelope()
    except ValueError as e:
        logger.info("tool=%s outcome=invalid_argument ms=%d", name, _ms(started))
        return error_envelope("invalid_argument", str(e), False)
    except Exception as e:  # noqa: BLE001 - tools must never raise
        logger.warning("tool=%s outcome=unexpected error=%s ms=%d", name, type(e).__name__, _ms(started))
        return error_envelope("upstream_error", f"Unexpected error: {type(e).__name__}", True)
    logger.info("tool=%s outcome=ok ms=%d", name, _ms(started))
    if result is None:
        result = {"done": True}
    return result if isinstance(result, str) else dump_json_capped(result)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
