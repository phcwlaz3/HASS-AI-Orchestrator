# Phase 7 — Deep Reasoning Harness & MCP Integration

> **Status:** implemented and unit-tested in v0.10.0 (April 2026).
> **Author:** GitHub Copilot (Claude Opus 4.7) working session.
> **Scope:** This document is the complete record of the architectural
> review, the changes shipped, and the plan going forward.

---

## 1. Executive summary

`HASS-AI-Orchestrator` (Phase 6, v0.9.47) is a working multi-agent
Home Assistant add-on, but its agents are **single-shot reasoners**:
each LLM call emits one batch of actions, and there is no way for an
agent to observe a tool result and decide what to do next. There is
also no real Model Context Protocol (MCP) integration — `mcp_server.py`
is a misnamed in-process tool registry — so the system has no path to
the rich entity / area / device / history surface that modern HA MCP
servers expose.

Phase 7 adds:

1. **A real MCP client** (`external_mcp.py`) that connects over
   Streamable HTTP to your separate
   [HASS_MCP_OpenClaw](https://github.com/ITSpecialist111/HASS_MCP_OpenClaw)
   add-on (76 tools, 11 resources, 14 prompts).
2. **A proper agentic harness** (`reasoning_harness.py`) — a recursive
   tool-use loop with provider-agnostic LLM backend, parallel tool
   execution, budget caps, and a transparent trace.
3. **A Deep Reasoning Agent** (`agents/deep_reasoning_agent.py`) — the
   "brain" — that runs on demand and combines the existing local
   safety-checked tools with the external MCP tools under one
   registry.
4. Two REST endpoints (`GET /api/reasoning/info`,
   `POST /api/reasoning/run`) so the dashboard and the orchestrator
   can drive it.

The fast deterministic specialists (heating / cooling / lighting /
security / universal) are **untouched** — they keep their fixed-cadence
loops as you wanted.

> **Personal note (my fork):** I'm primarily using this for the deep
> reasoning agent — the specialist agents are disabled in my setup.
> See `options.json` for my local config.
>
> I've also bumped the default `max_tool_rounds` from 10 to 15 in
> `reasoning_harness.py` — I found 10 wasn't enough for complex
> multi-room automations that chain several MCP lookups before acting.

---

## 2. What we found in the existing codebase

### 2.1 Architecture (as of v0.9.47)

```
┌──────────── Home Assistant Add-on (host_network) ─────────────┐
│                                                                │
│  React dashboard (Vite) ─┐                                     │
│                          │                                     │
│  FastAPI :8999 ──────────┼── REST + /ws WebSocket              │
│   ├── IngressMiddleware  │                                     │
│   ├── /api/chat          │                                     │
│   ├── /api/agents        │                                     │
│   ├── /api/decisions     │                                     │
│   ├── /api/approvals     │                                     │
│   ├── /api/factory/*     │  (no-code agent factory)            │
│   └── /api/dashboard/*   │  (Gemini-generated visual)          │
│                                                                │
│  Singletons (globals):                                         │
│   ├
```
