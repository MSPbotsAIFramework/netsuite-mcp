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
"""Generic record CRUD: one recordType parameter instead of a tool per NetSuite record type.

NetSuite ships hundreds of standard record types plus whatever custom ones an account adds;
see Decisions made by DEV01 (PRD-21337 tech design) for why this is generic rather than one
tool per type.
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..api_client import NetSuiteClient
from ._common import (
    DESTRUCTIVE,
    READ_ONLY,
    WRITES,
    WRITES_IDEMPOTENT,
    ClientFactory,
    Confirm,
    Fields,
    Limit,
    Offset,
    RecordId,
    RecordType,
    compact,
    id_from_link,
    not_confirmed,
    page_size,
    paged_items,
    run_tool,
    self_link,
)


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def netsuite_list_records(recordType: RecordType, limit: Limit = None, offset: Offset = None) -> str:
        """List a record type's ids/links, paginated. Does not filter; use netsuite_run_suiteql to search."""

        async def action(client: NetSuiteClient) -> Any:
            size, start = page_size(limit), offset or 0
            payload = await client.get(f"record/v1/{recordType}", {"limit": size, "offset": start})
            raw_items, meta = paged_items(payload, size, start)
            items = [{"id": id_from_link(self_link(item)), "link": self_link(item)} for item in raw_items]
            return {"record_type": recordType, "items": items, **meta}

        return await run_tool("netsuite_list_records", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def netsuite_get_record(recordType: RecordType, id: RecordId) -> str:
        """Get one record's full field body, as NetSuite returns it."""

        async def action(client: NetSuiteClient) -> Any:
            return await client.get(f"record/v1/{recordType}/{id}")

        return await run_tool("netsuite_get_record", client_factory, action)

    @mcp.tool(annotations=WRITES)
    async def netsuite_create_record(recordType: RecordType, fields: Fields) -> str:
        """Create a record. Only when the user explicitly asks; confirm the field values first."""

        async def action(client: NetSuiteClient) -> Any:
            _body, resp = await client.post(f"record/v1/{recordType}", compact(fields))
            location = resp.headers.get("Location")
            return {"id": id_from_link(location), "link": location, "record_type": recordType}

        return await run_tool("netsuite_create_record", client_factory, action)

    @mcp.tool(annotations=WRITES_IDEMPOTENT)
    async def netsuite_update_record(recordType: RecordType, id: RecordId, fields: Fields) -> str:
        """Patch only the given fields of a record (partial update). Only when the user explicitly asks."""

        async def action(client: NetSuiteClient) -> Any:
            body = compact(fields)
            if not body:
                raise ValueError("Nothing to update: pass at least one field to change.")
            await client.patch(f"record/v1/{recordType}/{id}", body)
            return {"id": id, "record_type": recordType, "done": True}

        return await run_tool("netsuite_update_record", client_factory, action)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def netsuite_delete_record(recordType: RecordType, id: RecordId, confirm: Confirm = None) -> str:
        """Permanently delete a record. Irreversible; needs confirm: true. Only when the user explicitly asks."""

        async def action(client: NetSuiteClient) -> Any:
            if confirm is not True:
                return not_confirmed(f"Permanently delete {recordType} {id}")
            await client.delete(f"record/v1/{recordType}/{id}")
            return {"done": True, "deleted_record_type": recordType, "deleted_id": id}

        return await run_tool("netsuite_delete_record", client_factory, action)
