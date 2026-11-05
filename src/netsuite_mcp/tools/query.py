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
"""SuiteQL: NetSuite's own documented REST query surface, the only search/filter mechanism here."""

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..api_client import NetSuiteClient
from ._common import READ_ONLY, ClientFactory, Limit, Offset, page_size, paged_items, run_tool


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def netsuite_run_suiteql(
        q: Annotated[str, Field(description="A single SuiteQL SELECT statement. Not pre-validated here; NetSuite itself rejects anything else.", min_length=1)],
        params: Annotated[list[Any] | None, Field(description="Bound parameters for ?-style placeholders in q. Prefer these over string-interpolating caller input into q.")] = None,
        limit: Limit = None,
        offset: Offset = None,
    ) -> str:
        """Run a read-only SuiteQL SELECT; return rows plus pagination metadata.

        Use this to search/filter/join records; the plain list tool only pages through ids.
        """

        async def action(client: NetSuiteClient) -> Any:
            size, start = page_size(limit), offset or 0
            payload, _resp = await client.post(
                "query/v1/suiteql",
                {"q": q, "params": params or []},
                params={"limit": size, "offset": start},
                prefer="transient",
            )
            rows, meta = paged_items(payload, size, start)
            return {"rows": rows, **meta}

        return await run_tool("netsuite_run_suiteql", client_factory, action)
