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
"""Record-type catalog and per-type field schema (NetSuite's metadata-catalog)."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..api_client import NetSuiteClient
from ._common import READ_ONLY, ClientFactory, RecordType, run_tool, self_link


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def netsuite_list_record_types() -> str:
        """List every record type available in this NetSuite account (standard and custom)."""

        async def action(client: NetSuiteClient) -> Any:
            payload = await client.get("record/v1/metadata-catalog")
            items = payload.get("items") if isinstance(payload, dict) else None
            items = items if isinstance(items, list) else []
            return {
                "record_types": [{"name": item.get("name"), "link": self_link(item)} for item in items],
                "count": len(items),
            }

        return await run_tool("netsuite_list_record_types", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def netsuite_get_record_schema(recordType: RecordType) -> str:
        """Get a record type's field list (name, type, required) from NetSuite's metadata catalog."""

        async def action(client: NetSuiteClient) -> Any:
            schema = await client.get(f"record/v1/metadata-catalog/{recordType}", accept="application/schema+json")
            properties = schema.get("properties") if isinstance(schema, dict) else None
            properties = properties if isinstance(properties, dict) else {}
            required = set(schema.get("required") or []) if isinstance(schema, dict) else set()
            fields = [
                {"name": name, "type": (spec or {}).get("type"), "required": name in required}
                for name, spec in properties.items()
            ]
            return {"record_type": recordType, "fields": fields, "count": len(fields)}

        return await run_tool("netsuite_get_record_schema", client_factory, action)
