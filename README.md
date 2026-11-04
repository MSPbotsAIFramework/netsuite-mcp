# NetSuite MCP Server

An [MCP](https://modelcontextprotocol.io) server for NetSuite's SuiteTalk REST Web Services. It
lets an AI agent discover an account's record types and their fields, search/report with
SuiteQL, and read, create, update and (with confirmation) delete individual ERP/CRM records
(customers, sales orders, invoices, and any custom record type the account has configured).

- Transport: MCP Streamable HTTP at `POST /mcp`, health probe at `GET /health`
- Stateless and multi-tenant: credentials arrive on every request as HTTP headers and are
  never stored; every tool is listed from the first `tools/list`
- Python 3.12, [uv](https://docs.astral.sh/uv/), FastMCP, httpx
- License: Apache-2.0, Copyright 2026 MSPbots.ai

<div align="center">

## MSPbots Connector

**The MCP Gateway built for MSPs: visible, controlled, and your data stays where it belongs.**

[**Get started at connect.mspbots.ai →**](https://connect.mspbots.ai)

</div>

---

MSPbots Connector gives your AI clients one MCP URL to reach your entire MSP stack (RMM, PSA, documentation, security, finance), and gives you control over who can call which tool and a record of everything that was called.

**Hosted service: [connect.mspbots.ai](https://connect.mspbots.ai)** · Free to use.

### Why Connector

![MSPbots Connector](https://stgoss.mspbots.ai/files/mcp/mspbots-mcp.png)

| | |
|---|---|
| **Visible** | Every tool call is logged: who, which vendor, when. Usage dashboard, CSV export, SIEM streaming. |
| **Controlled** | Role-based, per-tool allowlist. Deny by default. |
| **Protected** | Credentials encrypted per record. AI clients never see your vendor passwords or tokens. |
| **MSP-native** | Built for the MSP stack only: RMM, PSA, documentation, security, finance. |

### Quick start

1. **Sign up** at [connect.mspbots.ai](https://connect.mspbots.ai).
2. **Connect your vendors.** Pick from 150+ connectors and authorize each one on the vendor's own page, or bring your own API keys. No extra vendor accounts needed.
3. **Set access.** Create roles and decide which tools each role may call. Nothing is allowed until you allow it.
4. **Add your MCP URL to your AI client.** Copy the URL from your Connector dashboard into Claude, ChatGPT, Cursor, VS Code, Gemini, Codex, or any MCP-capable client.
5. **Ask your AI to do real work**, and watch every call appear in the audit log.

### Features

**Unified gateway**
- One MCP URL for all your AI clients
- 150+ connectors across the MSP stack
- Custom MCP servers supported, free
- Authorization happens on each vendor's own page; the AI never sees passwords or tokens
- Bring your own credentials: use the vendor API keys you already have
- Multiple accounts per app; connect once, reuse across AI clients
- Tool search: finds the right action for the task
- One sign-in; vendor credentials are injected behind the scenes and OAuth tokens refresh automatically

**Visibility**
- Audit log for every tool call (who, which vendor, when)
- Usage dashboard: call volume, top vendors, error rate, per-user activity
- Export audit logs to CSV, or stream to Loki, Graylog, or LogScale (SIEM)
- Optional recording of tool arguments and result summaries (off by default, enabled per organization)

**Control**
- Per-role, per-tool allowlist, deny by default
- owner / admin / member roles
- SCIM provisioning with Entra ID, Okta, JumpCloud, and Google Workspace

**Security**
- Credentials encrypted one record at a time (AES-256-GCM, per-record key)
- AI clients authenticate with OAuth 2.1 + PKCE
- Vendor responses are scanned for prompt-injection patterns and returned as structured data. This is one layer of defense, not a guarantee against every attack.
- Static egress IP for customer firewall and API allowlisting

### Pricing

**Free.**

Links: [connect.mspbots.ai](https://connect.mspbots.ai) · [mspbots.ai](https://mspbots.ai)

---

## Authentication headers

NetSuite Token-Based Authentication (TBA) is five independent values, not a single bearer
token; every outbound call is signed with OAuth 1.0a (HMAC-SHA256) using all five.

| Header | Required | Type | Default | Allowed values | Description | Example |
|---|---|---|---|---|---|---|
| `X-Netsuite-Account-Id` | Yes | string | none | - | NetSuite account id. Builds the account-specific API host and is used as the OAuth `realm`. | `1234567` or `1234567_SB1` (sandbox) |
| `X-Netsuite-Consumer-Key` | Yes | string | none | - | Integration record's consumer key | `Setup > Integration > Manage Integrations > New` |
| `X-Netsuite-Consumer-Secret` | Yes | string | none | - | Integration record's consumer secret (shown once at creation) | same integration record |
| `X-Netsuite-Token-Id` | Yes | string | none | - | Access token id, scoped to one role | `Setup > Users/Roles > Access Tokens > New` |
| `X-Netsuite-Token-Secret` | Yes | string | none | - | Access token secret (shown once at creation) | same access token record |

Where to get them: in NetSuite, enable **REST Web Services** and **Token-Based Authentication**
features (`Setup > Company > Enable Features > SuiteCloud`), create an integration record
(`Setup > Integration > Manage Integrations > New`) to get the consumer key/secret, then create
an access token for a role with the "REST Web Services" and "Log in using Access Tokens"
permissions (`Setup > Users/Roles > Access Tokens > New`) to get the token id/secret. The
account id is shown in the NetSuite UI footer or at `Setup > Company > Company Information`.

How credentials are used: every call is signed per request with OAuth 1.0a (HMAC-SHA256) — a
fresh nonce and timestamp each time — and sent as the `Authorization` header; the account id
also selects the account-specific API host
(`https://{account-id-normalized}.suitetalk.api.netsuite.com/services/rest`, lowercase,
underscores become hyphens, e.g. `1234567_SB1` -> `1234567-sb1`).

A request to `/mcp` missing any of the five headers gets HTTP 401 listing the missing ones.

## Tools

All tools return a JSON string. `netsuite_list_records` and `netsuite_run_suiteql` take `limit`
(default 50, hard cap 200) and `offset` (0-based; pass `next_offset` from the previous result to
continue) and return `has_more`/`next_offset`. There is **one tool per operation, not one per
record type** — NetSuite ships hundreds of standard record types plus whatever custom ones an
account adds, so `recordType` is a parameter rather than part of the tool name.

| Area | Tools |
|---|---|
| Metadata | `netsuite_list_record_types`, `netsuite_get_record_schema` (**recordType**) |
| Records | `netsuite_list_records` (**recordType**), `netsuite_get_record` (**recordType**, **id**); **write**: `netsuite_create_record` (**recordType**, **fields**), `netsuite_update_record` (**recordType**, **id**, **fields**); **confirm**: `netsuite_delete_record` (**recordType**, **id**) |
| Query | `netsuite_run_suiteql` (**q**, params, limit, offset) |

`netsuite_run_suiteql` runs a single SuiteQL `SELECT` statement against `/query/v1/suiteql` —
this is NetSuite's own documented way to filter/join/aggregate; the plain list endpoint only
pages through ids with no server-side filtering. Use `params` for bound `?`-style parameters
instead of interpolating caller input into `q`.

Errors come back as a JSON envelope instead of an exception:

```json
{"error": {"code": "invalid_argument", "message": "NetSuite returned HTTP 400 for record/v1/customer: companyName is required", "retryable": false}}
```

`code` is one of `not_configured`, `unauthorized`, `not_found`, `invalid_argument`,
`rate_limited`, `upstream_error`. Reads are retried with backoff on 429 and 5xx; writes only on
429 (NetSuite governance/concurrency-limit rejections).

## Run locally

```bash
uv sync
uv run python -m netsuite_mcp          # listens on 0.0.0.0:8080
```

or with Docker:

```bash
docker build --platform linux/amd64 -t netsuite-mcp:dev .
docker run --rm -p 8080:8080 netsuite-mcp:dev
```

Configuration (environment variables, all optional): `MCP_HTTP_HOST` (default `0.0.0.0`),
`MCP_HTTP_PORT` (default `8080`), `LOG_LEVEL` (default `INFO`). Credentials are never read
from the environment.

## Testing example

```bash
# list tools
curl -s http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-Netsuite-Account-Id: 1234567_SB1" \
  -H "X-Netsuite-Consumer-Key: your-consumer-key" \
  -H "X-Netsuite-Consumer-Secret: your-consumer-secret" \
  -H "X-Netsuite-Token-Id: your-token-id" \
  -H "X-Netsuite-Token-Secret: your-token-secret" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

# a SuiteQL query
curl -s http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-Netsuite-Account-Id: 1234567_SB1" \
  -H "X-Netsuite-Consumer-Key: your-consumer-key" \
  -H "X-Netsuite-Consumer-Secret: your-consumer-secret" \
  -H "X-Netsuite-Token-Id: your-token-id" \
  -H "X-Netsuite-Token-Secret: your-token-secret" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"netsuite_run_suiteql","arguments":{"q":"SELECT id, companyname FROM customer"}}}'
```

Unit tests (no network access needed):

```bash
uv run pytest
```

## Delivery notes

- **Full CRUD, including delete.** The ticket's own wording ("live access... every action
  logged") was read as acting on the account, not just reading it; `netsuite_delete_record`
  requires `confirm: true` and is annotated `destructiveHint=True`. See `docs/tickets/PRD-21337.md`
  ("Decisions made by DEV01") for the full reasoning and the alternative (read-only v1) that was
  not chosen.
- **OAuth 1.0a signing is hand-rolled (`src/netsuite_mcp/oauth1.py`)** against RFC 5849 rather
  than adding `requests-oauthlib`, which would pull in `requests` as a second HTTP stack
  alongside httpx for a few dozen lines of HMAC-SHA256 signing. Covered by
  `tests/test_api_client.py::test_oauth1_signature_header_shape_and_freshness` (all required
  OAuth components present, fresh nonce/signature per call) and a live request assertion.
- **Account id -> subdomain transform** (lowercase, `_` -> `-`) follows NetSuite's documented
  convention but was not re-verified against a live account (no NetSuite test account is
  configured in this pipeline's environment; not a blocker per SOP). Confirm this against a real
  account before go-live (Open Question Q3 in the tech design).
- **No per-record-type tools.** `recordType` is a parameter on a small set of generic tools
  instead of one tool per NetSuite record type, because the record catalog is large and
  account-customizable; see "Decisions made by DEV01" in the tech design.
- **Delete-of-missing-id is not assumed idempotent.** NetSuite's documented behavior for
  deleting an already-deleted id was not independently re-verified this pass; confirm the actual
  response before relying on idempotent delete semantics.

## License

Apache License 2.0 - see [LICENSE](LICENSE) and [NOTICE](NOTICE).
