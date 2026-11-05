# PRD-21337 · Code review

Status: frozen @ ea89d88

## Round 1 · 2026-11-05 · reviewed dev/PRD-21337 @ ea89d88 (base 7dfdc56)

Verdict: approved

### Findings

1. [suggestion] Dockerfile (whole file) — `docker build --platform linux/amd64` could not be verified in the review environment either: `docker info` fails with `permission denied ... /var/run/docker.sock` (the `mspbots` user is not in the `docker` group on this host). Same gap the dev leg flagged (no reachable daemon on its host). The Dockerfile itself follows the SOP §8 reference sample closely (multi-stage, non-root `app` user, `curl` installed, `EXPOSE 8080`, default `MCP_HTTP_PORT`/`MCP_HTTP_HOST`, `HEALTHCHECK` on `/health`) and mirrors `hudu-mcp`'s Dockerfile shape. Confirm the image actually builds and starts before/at INT publish time (App Publish's build step will do this).
2. [suggestion] `docs/tickets/PRD-21337.md:292-295` (Self-test, gitleaks fallback) — the dev leg's note "returned no hits" is imprecise: re-running `git log -p -- . | grep -Ei '(token|secret|api[_-]?key|password|bearer)\s*[=:]' | grep -vi example` over this branch does return matches, but they are all identifiers/placeholders (`consumer_secret: str`, `X-Netsuite-Consumer-Secret`, `your-consumer-secret`, etc.) — no real credential value. Substance of the claim (no leaked secrets) holds; wording should say "no real-credential hits" next time to avoid looking like a false negative on a literal re-run.

No must-fix findings. Verified in this round:

- Gate: `uv sync && uv run pytest -q` → 105 passed, 1 unrelated deprecation warning, matching the self-test record exactly.
- `docs/tickets/PRD-21337.ac.json`: 19/19 `type: auto` cases `pass`; TC20-22 `type: manual`/`untested` each carry a specific NetSuite-test-account reason (not a blocker per SOP).
- Scope: diff `7dfdc56..ea89d88` only touches new files under `src/netsuite_mcp/`, `tests/`, `README.md`, and project scaffold (`pyproject.toml`, `Dockerfile`, `docker-compose.yml`, lockfile, license files) — matches the tech design's Change list exactly; no tool beyond the 8 listed was added.
- Security / MCP contract checklist (`mcp-track.md` §4.3): all five `X-Netsuite-*` credential headers are read fresh per request into a `ContextVar` (`server.py`), reset in `finally`, never logged (`NetSuiteCredentials.__repr__` redacts all four secret fields), no module-level singleton holds a signed client or credentials (the shared `httpx.AsyncClient` carries no auth state — each call signs its own `Authorization` header from the per-request `ContextVar`); `test_concurrent_requests_do_not_share_credentials` exercises two interleaved tenants with `asyncio.gather`, not just sequential calls. Missing-header path returns 401 listing every missing header name (`test_missing_any_required_header_returns_401_listing_names`, parametrized over all five). DNS-rebinding protection is explicitly disabled with a one-line reason (service sits behind the gateway). MCP app lifespan is wired to the outer Starlette app per the documented FastMCP/Starlette gotcha.
- Error mapping matches the tech design's table exactly (`api_client.py::_STATUS_TO_CODE`): 400/422→`invalid_argument`, 401/403→`unauthorized`, 404→`not_found`, 429→`rate_limited` (retryable), 5xx/0 (timeout/connection error)→`upstream_error` (retryable). `run_tool` never lets an exception escape a tool call (catches `NetSuiteError`, `ValueError`, and a final `Exception` fallback).
- Reuse: `_json.py` and `_arguments.py` are reused verbatim from the sibling connectors as the tech design specified; no dependency on `requests-oauthlib` was actually added — OAuth 1.0a (HMAC-SHA256) signing was hand-rolled against RFC 5849 using stdlib only (`oauth1.py`), which is a smaller footprint than the tech design's tentative "new dependency" and is explained in the README's Delivery notes. No reinvention of `@mspbots/ui` or similar applies (MCP repo, no UI).
- README Connector section: diffed byte-for-byte against `vendor-mcp-development/reference/readme-connector-section.md` — identical (3150/3150 chars match).
- Chinese-character scan (`grep -rnP '[\x{4e00}-\x{9fff}]' --exclude-dir=.git --exclude-dir=.venv .`) — no output, confirmed independently.
- `.gitignore`/`.dockerignore` both exclude `.env`/`.env.*`/`.venv`; `docker-compose.yml` carries no real credentials, only env vars with no credential fields.
- Tool descriptions: all first lines well under the 100-char/500-char SOP §2 budget; no internal jargon (`recordType`, `confirm: true`, etc. are the only implementation-adjacent terms and are user-facing contract, not internals).
- AC coverage: spot-checked AC5 (`not_found` on 404 in `_STATUS_TO_CODE`), AC9/AC10 (`netsuite_delete_record` checks `confirm is not True` before calling `client.delete`, returns a `not_confirmed` result otherwise), AC13 (`paged_items`/`has_more`/`next_offset` logic shared by both `netsuite_list_records` and `netsuite_run_suiteql`) — all match the tech design and AC text.

Host: dev06
