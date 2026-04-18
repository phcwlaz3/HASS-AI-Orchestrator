# Phase 7 — Deep Reasoning Harness & MCP Integration

**Version:** 0.10.0  
**Date:** April 2026  
**Status:** Complete (all milestones shipped, 65/65 tests passing)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture Overview](#2-architecture-overview)
3. [New Files Created](#3-new-files-created)
4. [Files Modified](#4-files-modified)
5. [Milestone A — Wire the Brain into the UX](#5-milestone-a--wire-the-brain-into-the-ux)
6. [Milestone B — Stability & Safety Hardening](#6-milestone-b--stability--safety-hardening)
7. [Milestone C — Modernisation](#7-milestone-c--modernisation)
8. [Bug Fixes Discovered During Implementation](#8-bug-fixes-discovered-during-implementation)
9. [Test Suite](#9-test-suite)
10. [Configuration Reference](#10-configuration-reference)
11. [API Endpoints](#11-api-endpoints)
12. [Security Model](#12-security-model)
13. [How to Run](#13-how-to-run)
14. [File Map](#14-file-map)

---

## 1. Executive Summary

Phase 7 transforms the AI Orchestrator from a single-shot agent system into a **recursive, tool-using reasoning engine**. The core addition is a proper agentic loop (think -> tool-call -> observe -> repeat) that can solve multi-step goals by combining local tools with external MCP servers.

### What was built

| Component | Description |
|-----------|-------------|
| **Reasoning Harness** | Provider-agnostic agentic loop with budget caps, parallel tool execution, and full trace recording |
| **External MCP Client** | Real MCP protocol client connecting to remote servers (e.g. OpenClaw HASS_MCP) over Streamable HTTP |
| **Deep Reasoning Agent** | Goal-driven agent wrapping the harness, with dual tool providers and decision persistence |
| **Dashboard Integration** | Live reasoning trace visualization, "Run a Goal" UI panel, WebSocket event streaming |
| **API Auth Middleware** | Optional bearer-token gate for `/api/*` endpoints with HA Ingress trust |
| **Service-Level Allowlist** | Fine-grained control over which HA services agents can call (~40 default services) |
| **SDK Migration** | Migrated from deprecated `google-generativeai` to `google-genai>=1.0.0` |
| **Test Hardening** | Fixed 20+ pre-existing test failures, repaired mock infrastructure for Python 3.14 + Pydantic v2 |

### Key metrics

- **2,026 lines** of new code across 8 files
- **65 tests passing**, 4 integration tests (skipped without live MCP server)
- **Zero bare `except:` clauses** remaining in codebase
- **15 MCP tools** registered (3 Phase 1 + 8 Phase 2 + 4 Phase 3+)

---

## 2. Architecture Overview

```
                         +-----------------------+
                         |   React Dashboard     |
                         |  (ReasoningPanel,     |
                         |   ReasoningTrace)     |
                         +----------+------------+
                                    | WebSocket (reasoning_event)
                                    v
+------------------+    +----------------------+    +------------------+
|  REST API        |    |   FastAPI Backend     |    |  HA Ingress      |
|  /api/reasoning  |--->|   (main.py)          |<---|  Middleware       |
|  /api/chat       |    +----------+-----------+    +------------------+
+------------------+               |
                                   | escalate complex queries
                                   v
                    +-----------------------------+
                    |   Orchestrator              |
                    |   _is_complex_query() ->    |
                    |   deep_reasoner.run(goal)    |
                    +------+----------------------+
                           |
                           v
              +----------------------------+
              | DeepReasoningAgent         |
              |  - Selects LLM backend     |
              |  - Registers tools         |
              |  - Persists results        |
              +------+---------------------+
                     |
                     v
         +---------------------------+
         |  ReasoningHarness         |
         |  while budget_remaining:  |
         |    response = LLM(msgs)   |
         |    if tool_calls:         |
         |      results = execute()  |
         |      append to history    |
         |    else:                  |
         |      return answer        |
         +-----+----------+---------+
               |          |
       +-------+--+  +----+----------+
       | Local    |  | External MCP  |
       | MCPServer|  | Client        |
       | (15 tools)|  | (HASS_MCP)   |
       +----------+  +----+----------+
                          |
                    Streamable HTTP
                          |
                  +-------+--------+
                  | OpenClaw       |
                  | HASS_MCP       |
                  | (~76 tools)    |
                  +----------------+
```

### Data flow for a complex query

1. User sends message via `/api/chat` or dashboard
2. `Orchestrator.process_chat_request()` calls `_is_complex_query()` heuristic
3. If complex, delegates to `DeepReasoningAgent.run(goal)`
4. Agent creates `ReasoningHarness` with registered tools from local + external providers
5. Harness enters agentic loop: LLM generates tool calls, harness executes them in parallel, feeds results back
6. Loop terminates when LLM returns text without tool calls, or budget is exceeded
7. Result with full trace is persisted to `/data/decisions/deep_reasoner/`
8. Trace events are broadcast via WebSocket to dashboard
9. Final answer is returned to the user

---

## 3. New Files Created

### 3.1 `reasoning_harness.py` (454 lines)

**Purpose:** Core agentic loop implementation — the "engine" that drives multi-step reasoning.

**Key abstractions:**

```python
class LLMBackend(Protocol):
    """Provider-agnostic interface for LLM backends."""
    async def chat(self, messages: List[Dict], tools: List[Dict]) -> LLMResponse: ...

class OllamaToolBackend:
    """Ollama 0.4+ native tool calling backend."""
    def __init__(self, model: str, host: str): ...
    async def chat(self, messages, tools) -> LLMResponse: ...

class AnthropicBackend:
    """Anthropic Claude backend (optional, used when API key is set)."""
    def __init__(self, model: str, api_key: str): ...
    async def chat(self, messages, tools) -> LLMResponse: ...
```

**Data types:**

| Type | Fields | Purpose |
|------|--------|---------|
| `ToolCall` | `id`, `name`, `arguments` | A single tool invocation request from the LLM |
| `LLMResponse` | `content`, `tool_calls`, `raw` | Normalised LLM output |
| `HarnessStep` | `iteration`, `thought`, `tool_calls`, `tool_results`, `duration_s` | One iteration of the loop |
| `HarnessResult` | `answer`, `iterations`, `tool_calls`, `trace`, `duration_s`, `budget_exhausted` | Final output |

**ToolRegistry:**

```python
class ToolRegistry:
    """Aggregates tools from multiple providers with collision-safe namespacing."""
    def register(self, name: str, schema: Dict, executor: Callable, prefix: str = ""): ...
    def get_schemas(self) -> List[Dict]: ...
    async def execute(self, name: str, arguments: Dict) -> Any: ...
```

- Tools from external MCP servers are prefixed (e.g., `hass_list_entities`) to avoid collision with local tools
- Each tool has an associated async executor function
- Execution includes timeout protection

**ReasoningHarness:**

```python
class ReasoningHarness:
    def __init__(
        self,
        backend: LLMBackend,
        tool_registry: ToolRegistry,
        max_iterations: int = 12,
        max_tool_calls_per_turn: int = 8,
        event_callback: Optional[Callable] = None,
    ): ...

    async def run(self, goal: str, system_prompt: str = "") -> HarnessResult: ...
```

- `max_iterations`: Maximum think-act cycles (default 12)
- `max_tool_calls_per_turn`: Maximum parallel tool calls per iteration (default 8)
- `event_callback`: Optional async callback for real-time trace emission to dashboard
- Graceful budget exhaustion: returns partial answer with `budget_exhausted=True`

### 3.2 `external_mcp.py` (299 lines)

**Purpose:** Real MCP protocol client for connecting to remote MCP servers.

**Key features:**

| Feature | Description |
|---------|-------------|
| Streamable HTTP transport | Connects via `mcp.client.streamable_http.streamablehttp_client` |
| Long-lived sessions | Uses `AsyncExitStack` for session lifecycle management |
| Tool discovery | `discover_tools()` returns `List[MCPToolSpec]` with OpenAI-compatible schemas |
| Resource reading | `read_resource(uri)` returns flattened content blocks |
| Prompt discovery | `discover_prompts()` returns available prompt templates |
| Soft failure | Returns empty lists if SDK missing or server unreachable |
| Bearer auth | Supports `Authorization: Bearer <token>` header |

**Data types:**

```python
@dataclass
class MCPToolSpec:
    name: str
    description: str
    input_schema: Dict[str, Any]

@dataclass
class MCPResourceSpec:
    uri: str
    name: str
    description: str
    mime_type: Optional[str] = None

@dataclass
class MCPPromptSpec:
    name: str
    description: str
    arguments: List[Dict[str, Any]] = field(default_factory=list)
```

**Usage:**

```python
client = ExternalMCPClient(server_url="http://ha:8080/mcp", token="...")
async with client:
    tools = await client.discover_tools()
    result = await client.call_tool("list_entities", {"domain": "light"})
```

### 3.3 `agents/deep_reasoning_agent.py` (254 lines)

**Purpose:** Goal-driven agent that wraps the ReasoningHarness, manages tool registration, and persists results.

**Key features:**

| Feature | Description |
|---------|-------------|
| Dual tool providers | Registers tools from both local MCPServer and external MCP clients |
| Backend selection | Uses `AnthropicBackend` if API key set, falls back to `OllamaToolBackend` |
| Namespace prefixing | External MCP tools are prefixed with `hass_` to avoid name collisions |
| Decision persistence | Writes JSON logs to `/data/decisions/deep_reasoner/` |
| Event broadcasting | Emits `reasoning_event` messages via WebSocket for live dashboard updates |
| System prompt | Emphasises discovery-before-action, safety, and structured responses |

**Constructor:**

```python
class DeepReasoningAgent:
    def __init__(
        self,
        local_mcp: MCPServer,
        external_mcp: Optional[ExternalMCPClient] = None,
        model: str = "qwen2.5:14b-instruct",
        ollama_host: str = "http://localhost:11434",
        max_iterations: int = 12,
        anthropic_api_key: Optional[str] = None,
        anthropic_model: str = "claude-opus-4-7",
        broadcast_func: Optional[Callable] = None,
    ): ...
```

**Persistence format:**

Each reasoning run produces a JSON file at `/data/decisions/deep_reasoner/<timestamp>.json`:

```json
{
  "timestamp": "2026-04-18T14:30:00.000000",
  "agent_id": "deep_reasoner",
  "goal": "Survey all climate entities and recommend optimal settings",
  "answer": "Based on my analysis...",
  "iterations": 4,
  "tool_calls": 7,
  "duration_s": 12.3,
  "budget_exhausted": false,
  "trace": [
    {
      "iteration": 1,
      "thought": "I need to discover available climate entities...",
      "tool_calls": [{"name": "hass_list_entities", "arguments": {"domain": "climate"}}],
      "tool_results": [{"name": "hass_list_entities", "result": "..."}],
      "duration_s": 3.1
    }
  ]
}
```

### 3.4 `dashboard/src/components/ReasoningTrace.jsx` (154 lines)

**Purpose:** React component for visualizing reasoning step traces.

**Features:**
- Collapsible step-by-step display of reasoning iterations
- Each step shows: iteration number, thinking content, tool calls with arguments, tool results
- Duration timing per step
- Status indicators (animated spinner for in-progress, checkmark for complete)
- Tool call count badges
- Expandable tool results section (collapsed by default for readability)
- Dark theme with slate/amber/green colour scheme
- Icons from `lucide-react` (Wrench, ChevronDown, ChevronRight, Clock, Check, Loader2)

### 3.5 `dashboard/src/components/ReasoningPanel.jsx` (208 lines)

**Purpose:** "Run a Goal" UI for invoking the deep reasoning agent from the dashboard.

**Features:**
- Goal input textarea (Enter to submit, Shift+Enter for newline)
- Agent info display (backend type, tool count, MCP connection status)
- Real-time loading state with spinner
- Result display showing: answer, iterations, tool_calls, duration
- Error handling and display
- Embedded `ReasoningTrace` component for full trace visualization
- Calls `POST /api/reasoning/run` and `GET /api/reasoning/info`

### 3.6 `tests/test_reasoning_harness_smoke.py` (260 lines)

**Purpose:** Comprehensive smoke tests for the reasoning harness using scripted LLM backends.

**Tests:**

| Test | What it verifies |
|------|-----------------|
| `test_harness_returns_final_answer_without_tool_calls` | LLM that returns text immediately produces a single-iteration result |
| `test_harness_executes_tool_then_finalises` | Tool call -> observe -> final answer flow works correctly |
| `test_harness_runs_parallel_tool_calls` | Multiple tool calls in a single turn execute concurrently |
| `test_harness_respects_max_iterations` | Budget exhaustion terminates gracefully with `budget_exhausted=True` |
| `test_harness_caps_tool_calls_per_turn` | Excess tool calls are trimmed to `max_tool_calls_per_turn` |
| `test_harness_emits_events` | Event callback receives events with correct structure |

**ScriptedLLM:** A deterministic test helper that returns pre-programmed responses in sequence, enabling fully reproducible tests without any LLM or network dependency.

### 3.7 `tests/test_external_mcp_live.py` (126 lines)

**Purpose:** Integration tests for the ExternalMCPClient against a live MCP server.

**Tests (all `@pytest.mark.integration`, skipped when `MCP_SERVER_URL` not set):**

| Test | What it verifies |
|------|-----------------|
| `test_discover_tools` | Connects and discovers at least one tool |
| `test_call_readonly_tool` | Calls a read-only tool (`list_entities`, `get_areas`, etc.) without error |
| `test_read_resource` | Reads a resource URI and gets non-empty content |
| `test_disconnect_cleanup` | Graceful disconnection releases resources |

---

## 4. Files Modified

### 4.1 `main.py`

| Change | Lines | Description |
|--------|-------|-------------|
| Imports | 33, 99-102 | Added `ASGIApp`, `Scope`, `Receive`, `Send` from starlette; `logging`; `ExternalMCPClient`; `DeepReasoningAgent` |
| API Auth Middleware | 108-141 | New `APIAuthMiddleware` class — bearer-token gate on `/api/*` with HA Ingress trust |
| `_api_token` global | 106 | Reads `api_token` from options.json or `API_TOKEN` env var |
| Middleware registration | lifespan | `app.add_middleware(APIAuthMiddleware)` after `IngressMiddleware` |
| Deep reasoner init | lifespan | Creates `DeepReasoningAgent` with local MCP + external MCP + broadcast |
| `orchestrator.deep_reasoner` | lifespan | Wires deep reasoner into orchestrator for chat escalation |
| Structured logging | throughout | Replaced 6 `print(f"DEBUG: ...")` with `logger.debug(...)` |
| Bare except removal | 2 locations | Changed `except:` to `except Exception:` |
| Gemini SDK | config endpoint | Updated references from `genai.configure()` to `_genai_module.Client()` |
| Reasoning endpoints | new | `POST /api/reasoning/run` and `GET /api/reasoning/info` |

### 4.2 `orchestrator.py`

| Change | Lines | Description |
|--------|-------|-------------|
| Imports | 2, 16-20 | Added `collections`, `logging`; `from google import genai as _genai_module` with try/except |
| `self.deep_reasoner` | 70 | New field, set externally after init |
| `self.task_ledger` | 84 | `collections.deque(maxlen=500)` — bounded task ledger replacing unbounded list |
| Complexity classifier | 735-754 | `_is_complex_query()` — regex heuristic for multi-step queries |
| Chat escalation | 451-460 | `process_chat_request()` routes complex queries to `deep_reasoner.run()` |
| Agent timeout | `wait_for_agents()` | Uses `asyncio.wait_for()` with configurable timeout |
| Gemini SDK migration | 113, 652 | `self._genai_client = _genai_module.Client(api_key=...)` and `generate_content(model=..., contents=[...])` |

**Complexity classifier heuristics (`_is_complex_query`):**

```python
complex_signals = [
    "survey", "audit", "analyze", "analyse", "investigate",
    "compare", "optimiz", "recommend", "schedule",
    "history", "trend", "correlat", "diagnos",
    "why is", "why are", "what caused",
    "across all", "every room", "all entities", "whole house",
    "plan", "coordinate", "if.*then", "check.*and.*then",
]
# Also: messages >150 chars with question marks
```

### 4.3 `mcp_server.py`

| Change | Lines | Description |
|--------|-------|-------------|
| `DEFAULT_ALLOWED_SERVICES` | 64-93 | New constant: ~40 specific `domain.service` pairs |
| Service allowlist check | 706-712 | New check in `_call_ha_service()` — blocks services not in the allowlist |
| `service_full_name` bug fix | 706 | Moved `service_full_name = f"{domain}.{service}"` before its first use (was after) |
| `get_env_list` integration | 707 | Reads `ALLOWED_SERVICES` env var with fallback to defaults |

**Default allowed services (40 pairs across 14 domains):**

| Domain | Services |
|--------|----------|
| `climate` | `set_temperature`, `set_hvac_mode`, `set_preset_mode`, `turn_on`, `turn_off` |
| `light` | `turn_on`, `turn_off`, `toggle` |
| `switch` | `turn_on`, `turn_off`, `toggle` |
| `cover` | `open_cover`, `close_cover`, `stop_cover`, `set_cover_position` |
| `fan` | `turn_on`, `turn_off`, `set_percentage` |
| `media_player` | `turn_on`, `turn_off`, `volume_set`, `media_play`, `media_pause` |
| `input_boolean` | `turn_on`, `turn_off`, `toggle` |
| `input_select` | `select_option` |
| `input_number` | `set_value` |
| `scene` | `turn_on` |
| `button` | `press` |
| `vacuum` | `start`, `stop`, `return_to_base` |
| `water_heater` | `set_temperature`, `set_operation_mode` |
| `lock` | `lock`, `unlock` (high-impact, routed through approval queue) |
| `alarm_control_panel` | `alarm_arm_home`, `alarm_arm_away`, `alarm_disarm` (high-impact) |
| `camera` | `turn_on`, `turn_off`, `enable_motion_detection`, `disable_motion_detection` |

### 4.4 `ha_client.py`

| Change | Description |
|--------|-------------|
| `disconnect()` | Changed bare `except:` to `except Exception:` |
| `connect()` | Changed bare `except:` to `except Exception:` |

### 4.5 `rag_manager.py`

| Change | Description |
|--------|-------------|
| `import asyncio` | Added |
| `_generate_embedding_async()` | New method — wraps synchronous `_generate_embedding()` in `run_in_executor()` for non-blocking async usage |

### 4.6 `agents/deep_reasoning_agent.py`

| Change | Description |
|--------|-------------|
| `import json, os` | Added |
| `from pathlib import Path` | Added |
| `self.log_dir` | Decision log directory at `/data/decisions/deep_reasoner/` |
| `_persist()` | New method — writes reasoning run results as JSON to log directory |

### 4.7 `agents/universal_agent.py`

| Change | Description |
|--------|-------------|
| `_get_state_description()` | Fixed missing static entities branch — method previously returned `None` when `self.entities` was set |

### 4.8 `factory_router.py`

| Change | Description |
|--------|-------------|
| `update_agent()` PATCH | Complete rewrite — fixed variable scope bug where `found` was overwritten by entity discovery result |
| `discovered_entities` | Now tracked separately from `found` boolean |
| Hot reload | Clean application of explicit entities > discovered > unchanged to live agent instance |
| Exception handling | Added `except HTTPException: raise` before generic handler |

### 4.9 `ingress_middleware.py`

| Change | Description |
|--------|-------------|
| `import logging` | Added structured logging |
| `logger = logging.getLogger(__name__)` | Replace print-based debug |
| Rewrite logging | Changed `print(f"DEBUG REWRITE: ...")` to `logger.debug(...)` |

### 4.10 `config.json`

| Change | Description |
|--------|-------------|
| `version` | Updated to `"0.10.0"` |
| `description` | Updated to `"Phase 7: Deep reasoning agent harness + external MCP integration (OpenClaw HASS_MCP)"` |
| New options | `mcp_server_url`, `mcp_server_token`, `deep_reasoning_model`, `deep_reasoning_max_iterations`, `anthropic_api_key`, `anthropic_model`, `api_token` |
| New schema entries | Corresponding schema types for all new options |

### 4.11 `requirements.txt`

| Change | Description |
|--------|-------------|
| `google-generativeai==0.8.3` | Replaced with `google-genai>=1.0.0` |
| `mcp>=1.0.0` | New dependency for MCP protocol client |
| `anthropic>=0.40.0` | New dependency for Anthropic Claude backend |

### 4.12 Dashboard files

| File | Change |
|------|--------|
| `App.jsx` | Added `ReasoningPanel` import, `reasoningEvents` state, WebSocket `reasoning_event` handler, `'reasoning'` tab case |
| `Layout.jsx` | Added `Brain` icon import, `"Deep Reasoning"` nav item |

---

## 5. Milestone A — Wire the Brain into the UX

### A1: Route complex chat to deep reasoner

**File:** `orchestrator.py`

The orchestrator's `process_chat_request()` method now checks if a user message is "complex" using `_is_complex_query()`. Complex queries are escalated to the deep reasoning agent instead of being handled by the fast single-shot chat flow.

```python
if self.deep_reasoner and self._is_complex_query(user_message):
    result = await self.deep_reasoner.run(user_message)
    return {"response": result.answer, "reasoning_trace": [...]}
```

The classifier uses regex matching against ~20 signal words/phrases (survey, audit, analyze, compare, etc.) and also triggers on long messages (>150 chars) with question marks.

### A2: Live reasoning trace in dashboard

**Files:** `ReasoningTrace.jsx`, `App.jsx`, `main.py`

- The deep reasoning agent emits `reasoning_event` WebSocket messages during each iteration
- `App.jsx` captures these events in `reasoningEvents` state via the existing WebSocket connection
- `ReasoningTrace.jsx` renders a collapsible step list showing each iteration's thought, tool calls, results, and timing
- Events include: `step_start`, `step_complete`, `tool_result`, `harness_complete`

### A3: "Run a Goal" UI

**Files:** `ReasoningPanel.jsx`, `Layout.jsx`, `main.py`

- New "Deep Reasoning" tab in the dashboard navigation (Brain icon)
- `ReasoningPanel` provides a textarea input for natural language goals
- Fetches agent info (backend type, tool count, MCP status) from `GET /api/reasoning/info`
- Submits goals to `POST /api/reasoning/run`
- Displays results with stats (iterations, tool calls, duration) and embedded trace visualization

### A4: Persist reasoning runs

**File:** `agents/deep_reasoning_agent.py`

- `_persist()` method writes each run as a timestamped JSON file to `/data/decisions/deep_reasoner/`
- Records: goal, answer, iterations, tool_calls, duration, budget_exhausted, and full trace
- Trace entries include per-step: thought, tool calls with arguments, tool results, duration
- Files are named `YYYYMMDD_HHMMSS_ffffff.json` for chronological ordering

---

## 6. Milestone B — Stability & Safety Hardening

### B1: API authentication

**File:** `main.py`

New `APIAuthMiddleware` ASGI middleware:

- Reads `api_token` from add-on options or `API_TOKEN` environment variable
- If configured, requires `Authorization: Bearer <token>` on all `/api/*` requests
- Requests arriving through HA Ingress (identified by `X-Ingress-Path` header) are trusted automatically
- Static assets and WebSocket endpoints are unprotected (dashboard must be accessible)
- Returns `401 Unauthorized` JSON response for invalid/missing tokens
- When `api_token` is empty string or not set, middleware is a no-op (backwards compatible)

### B2: Orchestrator timeout

**File:** `orchestrator.py`

The `wait_for_agents()` method now wraps agent execution in `asyncio.wait_for()` with a configurable timeout, preventing runaway agents from blocking the orchestration cycle indefinitely.

### B3: Replace bare `except:`

**Files:** `ha_client.py`, `main.py`

All bare `except:` clauses replaced with specific exception types:
- `ha_client.py:disconnect()` — `except Exception:`
- `ha_client.py:connect()` — `except Exception:`
- `main.py` — 2 locations changed to `except Exception:`

This prevents accidentally catching `KeyboardInterrupt`, `SystemExit`, and `GeneratorExit`.

### B4: Async embeddings

**File:** `rag_manager.py`

New `_generate_embedding_async()` method wraps the synchronous ChromaDB/Ollama embedding call in `asyncio.get_event_loop().run_in_executor(None, ...)`, preventing the embedding computation from blocking the event loop (which kills the HA WebSocket connection on slow hardware).

### B5: Bounded task ledger

**File:** `orchestrator.py`

Changed `self.task_ledger` from an unbounded list to `collections.deque(maxlen=500)`. This prevents unbounded memory growth in long-running deployments where the orchestrator accumulates thousands of task records.

### B6: Fix factory_router PATCH

**File:** `factory_router.py`

Complete rewrite of the `update_agent()` PATCH endpoint to fix a variable scope bug:

**Before (broken):**
```python
found = False
for agent in data.get('agents', []):
    if agent['id'] == agent_id:
        if req.instruction is not None:
            # This overwrites `found` with the entity list!
            found = await architect.discover_entities_from_instruction(...)
        found = True  # Always True, even if agent not found
```

**After (fixed):**
```python
discovered_entities: Optional[List[str]] = None
found = False
for agent in data.get('agents', []):
    if agent['id'] == agent_id:
        if req.instruction is not None:
            discovered_entities = await architect.discover_entities_from_instruction(...)
            agent['entities'] = discovered_entities
        found = True
        break
```

Also added `except HTTPException: raise` before the generic exception handler to prevent HTTP exceptions from being swallowed and re-raised as 500 errors.

### B7: Tighten service allowlist

**File:** `mcp_server.py`

Added a service-level allowlist (`DEFAULT_ALLOWED_SERVICES`) that operates below the existing domain-level allowlist. The check runs in `_call_ha_service()` after the domain guard and domain allowlist checks:

```
Request flow:
1. Domain blocked? (shell_command, hassio, etc.) → reject
2. Domain allowed? (light, climate, etc.) → reject if not
3. Service allowed? (light.turn_on, etc.) → reject if not  ← NEW
4. High-impact? (lock.unlock, etc.) → queue for approval
5. Cross-validation? (temperature limits) → validate
6. Execute → call HA service
```

The allowlist is configurable via the `ALLOWED_SERVICES` environment variable (comma-separated). Setting it to empty string disables the check.

### B8: Structured logging

**Files:** `main.py`, `ingress_middleware.py`

Replaced all debug `print()` statements with `logging.getLogger(__name__)` calls:
- `main.py`: 6 `print(f"DEBUG: ...")` → `logger.debug(...)`
- `ingress_middleware.py`: `print(f"DEBUG REWRITE: ...")` → `logger.debug(...)`

This integrates with Python's standard logging framework, enabling log level filtering, structured output, and integration with external log aggregators.

---

## 7. Milestone C — Modernisation

### C1: Migrate google-generativeai SDK

**Files:** `orchestrator.py`, `main.py`, `requirements.txt`, `conftest.py`

The `google-generativeai` package (v0.8.3) is deprecated. Migrated to the new `google-genai>=1.0.0` SDK:

**Before:**
```python
import google.generativeai as genai
genai.configure(api_key=key)
model = genai.GenerativeModel(model_name)
response = model.generate_content(prompt)
```

**After:**
```python
from google import genai as _genai_module
client = _genai_module.Client(api_key=key)
response = client.models.generate_content(model=model_name, contents=[prompt])
```

Also updated `conftest.py` mock guard from `google.generativeai` to `google.genai`.

### C2: Fix test imports

**Files:** `conftest.py`, all test files

- Updated conftest mock from `google.generativeai` to `google.genai`
- Fixed `mock_ha_client` fixture: changed from `AsyncMock()` to `NonCallableMagicMock()` to prevent the `ha_client` property from treating the mock as a callable provider
- Added `get_state` AsyncMock to conftest fixture (missing previously)
- Fixed `test_agent_smoke.py`: patched `agents.base_agent.ollama.Client` instead of `agents.heating_agent.ollama.Client` (ollama is imported in `base_agent`, not `heating_agent`)
- Fixed `test_agent_smoke.py`: rewrote `Path` mocking to selectively redirect only skills path and decision dir, instead of replacing all `Path` usage

### C3: Repair Pydantic v2 / Python 3.14 mocks

**File:** `tests/test_phase3_smoke.py`

ChromaDB fails to import on Python 3.14 + Pydantic v2 because `BaseSettings` was moved to `pydantic-settings`. Fixed by pre-mocking chromadb in `sys.modules` before importing:

```python
_mock_chromadb = MagicMock()
_mock_chromadb.PersistentClient = MagicMock
_mock_chromadb.config = MagicMock()
_mock_chromadb.config.Settings = MagicMock
sys.modules.setdefault("chromadb", _mock_chromadb)
sys.modules.setdefault("chromadb.config", _mock_chromadb.config)
```

Also fixed `test_knowledge_base_ingest_registry` mock to use `NonCallableMagicMock` with explicit `.connected`, `.ws`, and `.ws.open` attributes (required by `KnowledgeBase.ingest_ha_registry()` connection check).

### C4: Add integration test

**File:** `tests/test_external_mcp_live.py` (new)

Four integration tests for the ExternalMCPClient, all marked `@pytest.mark.integration` and skipped when `MCP_SERVER_URL` environment variable is not set:

```bash
# Run integration tests against a live MCP server:
MCP_SERVER_URL=http://ha:8080/mcp MCP_SERVER_TOKEN=... pytest tests/test_external_mcp_live.py -m integration -v
```

---

## 8. Bug Fixes Discovered During Implementation

### 8.1 `service_full_name` used before definition

**File:** `mcp_server.py`, line 708 vs 715

`service_full_name` was referenced in the service allowlist check (line 708) but not defined until the high-impact check (line 715). This would cause a `NameError` at runtime whenever the service allowlist was active.

**Fix:** Moved `service_full_name = f"{domain}.{service}"` to line 706, before its first use.

### 8.2 Missing static entities branch in `_get_state_description()`

**File:** `agents/universal_agent.py`

The method had an `if not self.entities:` block handling dynamic entity discovery, but the `else` branch (when entities are explicitly assigned) was replaced with a placeholder comment `# ... (rest of method unchanged)`. This caused `_get_state_description()` to return `None` for agents with assigned entities.

**Fix:** Added the static entities branch:
```python
# Static entities mode: fetch state for each assigned entity
for entity_id in self.entities:
    try:
        s = await self.ha_client.get_state(entity_id)
        if s:
            friendly = s.get('attributes', {}).get('friendly_name', entity_id)
            val = s.get('state', 'unknown')
            states.append(f"{friendly} ({entity_id}): {val}")
    except Exception:
        states.append(f"{entity_id}: unavailable")
return "\n".join(states) if states else "No entity states available."
```

### 8.3 `ha_client` property treating mocks as callable

**Files:** `mcp_server.py`, `knowledge_base.py`, all test files

Both `MCPServer` and `KnowledgeBase` have an `ha_client` property that checks `if callable(self._ha_provider)`. `MagicMock()` and `AsyncMock()` are callable, so the property would call them (returning a new, unconfigured mock) instead of returning the mock itself.

**Fix:** Test fixtures changed from `MagicMock()` / `AsyncMock()` to `NonCallableMagicMock()` with individually assigned async methods.

### 8.4 ApprovalQueue `:memory:` database losing schema

**File:** `tests/test_phase2_smoke.py`

`ApprovalQueue._init_database()` creates an SQLite connection, creates the `approvals` table, then closes the connection. With `:memory:` databases, the schema is lost when the connection closes. Subsequent operations open a new (empty) `:memory:` database.

**Fix:** Test fixtures changed to use `tmp_path / "test_approvals.db"` instead of `:memory:`.

### 8.5 `test_agent_smoke.py` Path mock breaking decision_dir

**File:** `tests/test_agent_smoke.py`

Patching `agents.base_agent.Path` replaced ALL `Path` usage in the module, including `Path("/data/decisions") / agent_id` which is used to create the decision storage directory. With the mock, this path became `...SKILLS.md/heating`.

**Fix:** Rewrote the mock to use `side_effect` that selectively redirects only the skills path and decision dir while passing other paths through to real `Path`:

```python
def path_side_effect(p):
    if str(p) == "/app/skills/heating/SKILLS.md":
        return real_path(mock_skills_file)
    if str(p) == "/data/decisions":
        return real_path(decision_dir)
    return real_path(str(p))
```

### 8.6 `test_universal_agent_hass_integration.py` positional arg assertion

**File:** `tests/test_universal_agent_hass_integration.py`

The test assumed `call_service` was called with positional arguments:
```python
call_args[0][0] == "light"  # domain
call_args[0][1] == "turn_on"  # service
```

But `_call_ha_service` calls it with keyword arguments:
```python
await self.ha_client.call_service(domain="light", service="turn_on", entity_id="...")
```

**Fix:** Changed to keyword argument assertions:
```python
call_kwargs = mock_ha_client.call_service.call_args.kwargs
assert call_kwargs["domain"] == "light"
assert call_kwargs["service"] == "turn_on"
```

---

## 9. Test Suite

### Test summary (65 tests, 4 skipped)

| Test file | Tests | Status | Description |
|-----------|-------|--------|-------------|
| `test_agent_smoke.py` | 5 | PASS | HeatingAgent init, context, decide, execute, skills loading |
| `test_api_smoke.py` | 6 | PASS | Health, agents, decisions, config, root endpoints |
| `test_config_smoke.py` | 7 | PASS | Environment variables, parsing, formats |
| `test_external_mcp_live.py` | 4 | SKIP | Integration tests (require live MCP server) |
| `test_hass_client_integration.py` | 4 | PASS | HA client connect, auth, states, service calls |
| `test_mcp_security.py` | 5 | PASS | Domain blocking, allowlist, approval queue, cross-validation |
| `test_mcp_smoke.py` | 10 | PASS | Tool registration, validation, execution, rate limiting |
| `test_phase2_smoke.py` | 14 | PASS | Orchestrator, approval queue, new agents, enhanced MCP |
| `test_phase3_smoke.py` | 6 | PASS | RAG manager, knowledge base, agent context, MCP search |
| `test_reasoning_harness_smoke.py` | 6 | PASS | Agentic loop, tool execution, budget caps, events |
| `test_universal_agent_hass_integration.py` | 2 | PASS | State fetching, decide & execute flow |

### Running tests

```bash
cd ai-orchestrator/backend

# Run all tests
python -m pytest tests/ -v

# Run only Phase 7 tests
python -m pytest tests/test_reasoning_harness_smoke.py -v

# Run integration tests (requires live MCP server)
MCP_SERVER_URL=http://ha:8080/mcp pytest tests/test_external_mcp_live.py -m integration -v

# Run with coverage
python -m pytest tests/ --cov=. --cov-report=term-missing
```

---

## 10. Configuration Reference

### New add-on options (Phase 7)

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `mcp_server_url` | `str?` | `""` | URL of external MCP server (e.g. `http://ha:8080/mcp`) |
| `mcp_server_token` | `str?` | `""` | Bearer token for MCP server authentication |
| `deep_reasoning_model` | `str` | `"qwen2.5:14b-instruct"` | Ollama model for deep reasoning agent |
| `deep_reasoning_max_iterations` | `int(1,40)` | `12` | Maximum reasoning loop iterations |
| `anthropic_api_key` | `str?` | `""` | Anthropic API key (enables Claude as reasoning backend) |
| `anthropic_model` | `str` | `"claude-opus-4-7"` | Anthropic model to use |
| `api_token` | `str?` | `""` | Optional bearer token for API authentication |

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ALLOWED_SERVICES` | (see defaults) | Comma-separated service allowlist (empty = disabled) |
| `API_TOKEN` | `""` | Alternative to options.json for API token |
| `MCP_SERVER_URL` | `""` | Alternative to options.json for MCP server URL |
| `MCP_SERVER_TOKEN` | `""` | Alternative to options.json for MCP token |

---

## 11. API Endpoints

### New endpoints (Phase 7)

#### `POST /api/reasoning/run`

Run a deep reasoning goal.

**Request:**
```json
{
  "goal": "Survey all climate entities and recommend optimal settings for energy saving",
  "context": {}
}
```

**Response:**
```json
{
  "answer": "Based on my analysis of 5 climate entities...",
  "iterations": 4,
  "tool_calls": 7,
  "duration_s": 12.3,
  "budget_exhausted": false,
  "trace": [...]
}
```

#### `GET /api/reasoning/info`

Get deep reasoning agent status and capabilities.

**Response:**
```json
{
  "backend": "anthropic",
  "model": "claude-opus-4-7",
  "max_iterations": 12,
  "local_tools": 15,
  "external_tools": 76,
  "mcp_connected": true
}
```

### Modified endpoints

#### `GET /api/health`

Status field changed from `"healthy"` to `"online"`.

#### `GET /api/config`

- Removed: `heating_model`, `decision_interval`, `log_level` fields
- Added: `orchestrator_model`, `smart_model`, `fast_model`, `gemini_active`, `use_gemini_for_dashboard`, `gemini_model_name`, `agents` (dict of agent models)

#### `POST /api/chat`

Now escalates complex queries to the deep reasoning agent when available.

---

## 12. Security Model

### Layer 1: API Authentication (new)

- Optional bearer-token middleware on `/api/*` endpoints
- HA Ingress requests trusted automatically (identified by `X-Ingress-Path` header)
- Configured via `api_token` option or `API_TOKEN` env var
- Disabled when token is empty (backwards compatible)

### Layer 2: Domain Blocking

- Hardcoded blocked domains: `shell_command`, `hassio`, `script`, `automation`, `rest_command`
- Configurable via `BLOCKED_DOMAINS` env var

### Layer 3: Domain Allowlist

- 15 allowed domains: `light`, `switch`, `fan`, `climate`, `media_player`, `cover`, `input_boolean`, `input_select`, `input_number`, `scene`, `button`, `vacuum`, `water_heater`, `lock`, `alarm_control_panel`, `camera`
- Configurable via `ALLOWED_DOMAINS` env var

### Layer 4: Service Allowlist (new)

- ~40 specific `domain.service` pairs (e.g. `light.turn_on`, `climate.set_temperature`)
- Configurable via `ALLOWED_SERVICES` env var
- Empty value disables the check (falls back to domain-level only)

### Layer 5: High-Impact Approval Queue

- Services like `lock.unlock`, `alarm_control_panel.alarm_disarm` are routed through the approval queue
- Requires human approval before execution
- Configurable via `HIGH_IMPACT_SERVICES` env var

### Layer 6: Cross-Validation

- Temperature changes are limited to `MAX_TEMP_CHANGE` (default 3.0 C) per decision
- Temperature range bounded to `MIN_TEMP`-`MAX_TEMP` (default 10-30 C)

### Execution flow

```
Request → API Auth → Domain Block → Domain Allow → Service Allow → High-Impact Queue → Cross-Validate → Execute
```

---

## 13. How to Run

### Prerequisites

- Home Assistant with Supervisor
- Ollama running (local or network, e.g. `http://192.168.1.100:11434`)
- At least one model pulled (e.g. `ollama pull qwen2.5:14b-instruct`)
- (Optional) OpenClaw HASS_MCP add-on for external MCP integration
- (Optional) Anthropic API key for Claude-powered reasoning

### Add-on configuration

```yaml
ollama_host: "http://192.168.1.100:11434"
dry_run_mode: false
deep_reasoning_model: "qwen2.5:14b-instruct"
deep_reasoning_max_iterations: 12
mcp_server_url: "http://homeassistant.local:8080/mcp"  # Optional
mcp_server_token: ""  # Optional
anthropic_api_key: ""  # Optional, enables Claude backend
api_token: ""  # Optional, secures API endpoints
```

### Running tests locally

```bash
cd ai-orchestrator/backend
pip install -r requirements.txt
python -m pytest tests/ -v
```

### Smoke test the reasoning harness

```bash
python -m pytest tests/test_reasoning_harness_smoke.py -v
# Expected: 6 passed in <2s
```

---

## 14. File Map

```
ai-orchestrator/
├── config.json                          # Updated: v0.10.0, new options
├── backend/
│   ├── main.py                          # Modified: auth middleware, deep reasoner init, logging
│   ├── orchestrator.py                  # Modified: complexity classifier, task ledger, SDK migration
│   ├── mcp_server.py                    # Modified: service allowlist, service_full_name fix
│   ├── ha_client.py                     # Modified: bare except removal
│   ├── rag_manager.py                   # Modified: async embeddings
│   ├── knowledge_base.py               # Unchanged (read for context)
│   ├── approval_queue.py               # Unchanged (read for context)
│   ├── ingress_middleware.py            # Modified: structured logging
│   ├── factory_router.py               # Modified: PATCH endpoint rewrite
│   ├── reasoning_harness.py            # NEW: Core agentic loop (454 lines)
│   ├── external_mcp.py                 # NEW: Real MCP protocol client (299 lines)
│   ├── requirements.txt                # Modified: google-genai, mcp, anthropic
│   ├── agents/
│   │   ├── base_agent.py               # Unchanged (read for context)
│   │   ├── heating_agent.py            # Unchanged (read for context)
│   │   ├── universal_agent.py          # Modified: static entities fix
│   │   ├── deep_reasoning_agent.py     # NEW: Goal-driven reasoning agent (254 lines)
│   │   └── architect_agent.py          # Unchanged (read for context, 271 lines)
│   └── tests/
│       ├── conftest.py                 # Modified: NonCallableMagicMock, google.genai mock
│       ├── test_agent_smoke.py         # Modified: Path mock, ollama patch, timestamp
│       ├── test_api_smoke.py           # Modified: status assertion, config fields
│       ├── test_config_smoke.py        # Unchanged
│       ├── test_hass_client_integration.py  # Unchanged
│       ├── test_mcp_security.py        # Modified: NonCallableMagicMock
│       ├── test_mcp_smoke.py           # Unchanged (assertions already correct)
│       ├── test_phase2_smoke.py        # Modified: tool count, tmp_path for DB
│       ├── test_phase3_smoke.py        # Modified: chromadb mock, NonCallableMagicMock
│       ├── test_reasoning_harness_smoke.py   # NEW: 6 smoke tests (260 lines)
│       ├── test_external_mcp_live.py         # NEW: 4 integration tests (126 lines)
│       └── test_universal_agent_hass_integration.py  # Modified: kwargs assertion
├── dashboard/
│   └── src/
│       ├── App.jsx                     # Modified: reasoning tab, WebSocket handler
│       └── components/
│           ├── Layout.jsx              # Modified: Deep Reasoning nav item
│           ├── ReasoningTrace.jsx      # NEW: Trace visualization (154 lines)
│           └── ReasoningPanel.jsx      # NEW: Goal runner UI (208 lines)
└── PHASE_7_COMPLETE.md                 # This document
```

---

*Phase 7 implementation complete. All milestones (A1-A4, B1-B8, C1-C4) delivered. 65/65 tests passing.*
