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

> **Personal note:** I'm studying this project to learn how agentic loops work in a home-automation context. My fork may include small experiments in the reasoning harness — nothing meant for upstream.

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
+------------------+    +----------------------+    +----
```
