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
"""tools/list snapshot, description/annotation budgets, and error-envelope mapping. No network access."""

import asyncio
import json

import pytest

from netsuite_mcp.api_client import NetSuiteError
from netsuite_mcp.server import INSTRUCTIONS, create_mcp_server

RECORD_TYPE_AND_ID = {"recordType", "id"}

# Tool names are a public contract: existing agents and tenant connections depend on them.
# Changing this snapshot is a breaking change (SOP §13).
EXPECTED_TOOLS: dict[str, set[str]] = {
    "netsuite_list_record_types": set(),
    "netsuite_get_record_schema": {"recordType"},
    "netsuite_list_records": {"recordType"},
    "netsuite_get_record": RECORD_TYPE_AND_ID,
    "netsuite_create_record": {"recordType", "fields"},
    "netsuite_update_record": {"recordType", "id", "fields"},
    "netsuite_delete_record": RECORD_TYPE_AND_ID,
    "netsuite_run_suiteql": {"q"},
}
DELETE_TOOLS = {n for n in EXPECTED_TOOLS if "_delete_" in n}
WRITE_TOOLS = {"netsuite_create_record", "netsuite_update_record", "netsuite_delete_record"}
READ_TOOLS = set(EXPECTED_TOOLS) - WRITE_TOOLS


@pytest.fixture(scope="module")
def tools():
    return {t.name: t for t in asyncio.run(create_mcp_server().list_tools())}


def test_tool_names_are_stable(tools):
    assert set(tools) == set(EXPECTED_TOOLS)


def test_tool_count_is_within_guidance(tools):
    assert len(tools) <= 20


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_required_params(tools, name):
    assert set(tools[name].inputSchema.get("required", [])) == EXPECTED_TOOLS[name]


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_description_budget(tools, name):
    description = (tools[name].description or "").strip()
    assert description, f"{name} has no description"
    assert len(description) <= 500
    assert len(description.splitlines()[0]) <= 100


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_tool_name_has_vendor_prefix(name):
    assert name.startswith("netsuite_")


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_annotations_match_side_effects(tools, name):
    annotations = tools[name].annotations
    assert annotations is not None
    assert annotations.readOnlyHint is (name in READ_TOOLS)
    assert bool(annotations.destructiveHint) is (name in DELETE_TOOLS)


@pytest.mark.parametrize("name", sorted(WRITE_TOOLS))
def test_write_tools_say_when_to_use_them(tools, name):
    assert "explicitly ask" in tools[name].description


@pytest.mark.parametrize("name", sorted(DELETE_TOOLS))
def test_delete_tools_take_confirm(tools, name):
    assert "confirm" in tools[name].inputSchema["properties"]


def test_list_tools_default_and_cap_match_sop_budget(tools):
    paged = [t for t in tools.values() if "limit" in t.inputSchema["properties"]]
    assert {"netsuite_list_records", "netsuite_run_suiteql"} <= {t.name for t in paged}
    for tool in paged:
        assert tool.inputSchema["properties"]["limit"]["anyOf"][0]["maximum"] == 200, tool.name


def test_limit_above_cap_is_rejected():
    content, _ = asyncio.run(create_mcp_server().call_tool("netsuite_list_records", {"recordType": "customer", "limit": 500}))
    error = json.loads(content[0].text)["error"]
    assert error["code"] == "invalid_argument"
    assert "less than or equal to 200" in error["message"]


def test_instructions_budget():
    assert len(INSTRUCTIONS) <= 1500


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "unauthorized", False),
        (403, "unauthorized", False),
        (404, "not_found", False),
        (400, "invalid_argument", False),
        (422, "invalid_argument", False),
        (429, "rate_limited", True),
        (500, "upstream_error", True),
        (503, "upstream_error", True),
        (0, "upstream_error", True),
    ],
)
def test_error_envelope(status, code, retryable):
    envelope = json.loads(NetSuiteError(status, "boom").to_envelope())
    assert envelope == {"error": {"code": code, "message": "boom", "retryable": retryable}}


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_tool_without_credentials_returns_not_configured(name):
    args = {k: ({} if k == "fields" else "x") for k in EXPECTED_TOOLS[name]}
    content, _ = asyncio.run(create_mcp_server().call_tool(name, args))
    assert json.loads(content[0].text)["error"]["code"] == "not_configured"
