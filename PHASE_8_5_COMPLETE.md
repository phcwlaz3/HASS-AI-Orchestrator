# Phase 8.5 — OpenClaw No Longer Required

**Status:** ✅ Complete  
**Tests:** 207 passed, 4 skipped (was 175/4; +32 new)

## What changed

The HASS-AI-Orchestrator add-on no longer depends on the **OpenClaw HASS_MCP**
add-on as a prerequisite. It ships with its own self-contained tool surface
and prompt library; an external MCP server is now purely additive.

### New modules

- **[ai-orchestrator/backend/native_ha_tools.py](ai-orchestrator/backend/native_ha_tools.py)** — `NativeHATools` exposes a 7-tool discovery + control surface (`ha_list_entities`, `ha_get_state`, `ha_search_entities`, `ha_list_domains`, `ha_list_services`, `ha_call_service`, `ha_summarise_area`) built directly on `HAWebSocketClient`. Returns `{ok, ...}` shape matching `ExternalMCPClient.call_tool`.
- **[ai-orchestrator/backend/native_prompts.py](ai-orchestrator/backend/native_prompts.py)** — `NativePromptLibrary` loads YAML prompts from one or more directories with safe template substitution.
- **[ai-orchestrator/backend/prompts/](ai-orchestrator/backend/prompts/)** — five built-in workflows shipped with the add-on:
  - `home_audit.yaml` (optional `focus`)
  - `energy_optimizer.yaml` (optional `window`)
  - `security_check.yaml`
  - `morning_routine.yaml` (optional `wake_time`)
  - `nightly_review.yaml`
- Operators can drop additional `*.yaml` files into `/data/prompts` (or whatever `PROMPTS_DIR` points to) to extend the catalog.

### Wiring

- [agents/deep_reasoning_agent.py](ai-orchestrator/backend/agents/deep_reasoning_agent.py) registers three tool providers in order:
  1. `local` — safety-checked agent tools
  2. `native_ha` — built on `HAWebSocketClient` (always on when HA is connected)
  3. `external_mcp` (prefix `ext_`) — optional, only when configured
- [main.py](ai-orchestrator/backend/main.py) instantiates `NativePromptLibrary` from `backend/prompts/` + `PROMPTS_DIR` in lifespan; `/api/reasoning/prompts*` endpoints union native + external (native wins on name collision; shadowed external prompts get an `ext_` prefix). Native is tried first in `/render` and `/run`; falls back to external if not found.
- [plan_executor.py](ai-orchestrator/backend/plan_executor.py) `PROVIDER_PREFIXES` updated to recognize `ha_`, `ext_`, `hass_`, `openclaw_`, `mcp_` for tool classification.
- [config.json](ai-orchestrator/config.json) description updated; external MCP renamed `external_mcp` (was `hass_mcp`).
- Docstrings in `external_mcp.py`, `agents/deep_reasoning_agent.py`, `reasoning_harness.py` updated to frame external MCP as optional/additive.

### Tests

- **[tests/test_native_ha_tools_smoke.py](ai-orchestrator/backend/tests/test_native_ha_tools_smoke.py)** — 20 tests covering all 7 tools, schema shape, error paths, and dispatch.
- **[tests/test_native_prompts_smoke.py](ai-orchestrator/backend/tests/test_native_prompts_smoke.py)** — 12 tests covering YAML loading, optional/required arg handling, malformed YAML resilience, multi-directory merge, and validation that all five built-in YAMLs render cleanly.
- Updated [tests/test_streaming_and_prompts_smoke.py](ai-orchestrator/backend/tests/test_streaming_and_prompts_smoke.py): existing prompt-endpoint tests now isolate external behavior via `backend_main.native_prompts = None`; the 503-on-disconnect case became a 404-unknown-prompt case (since the union resolver no longer treats "no external" as a service-unavailable condition).

## Migration notes

- **For operators currently running OpenClaw**: nothing breaks. If `MCP_URL` is set, `ExternalMCPClient` still connects and its prompts/tools merge into the catalog under the `ext_` prefix.
- **For new installs**: the orchestrator works out of the box with just a Home Assistant connection; OpenClaw is no longer needed.
- **Mutating native tools** (`ha_call_service`) flow through the existing PAE safety net (Milestone E) when the agent is in `auto`/`plan` mode.
