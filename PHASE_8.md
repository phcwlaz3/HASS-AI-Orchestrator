# Phase 8 — "The Loop Closes"

> **Status:** in progress (D1–D3 landing in this branch).
> **Target version:** 0.11.0
> **Vision:** Phase 7 gave the orchestrator a brain. Phase 8 gives it
> **memory**, **a conscience**, and **a heartbeat** — turning a
> reactive reasoner into a genuinely autonomous, learning operator
> that you can trust to run with `dry_run_mode: false`.

---

## 1. Why these four milestones

Phase 7 closed the *capability* gap (the reasoner can think and act).
Phase 8 closes the *accountability* gap. Looking at the system today:

| Today | Problem |
|---|---|
| Each reasoning run is a cold start | No learning across runs |
| One-shot execution | No way to preview a plan before high-impact actions |
| Reactive only (chat / cadence) | Not actually autonomous |
| `/api/reasoning/run` returns one blob after N seconds | Bad UX for long runs |
| 14 MCP prompt workflows from OpenClaw sit unused | Free product surface |

Each milestone targets one of these.

---

## 2. Milestones

### Milestone D — Episodic memory & recall

The deep reasoner remembers what it did, what worked, and what the
human said about it.

| # | Task | Files |
|---|---|---|
| **D1** | `MemoryStore` typed wrapper around `RagManager.memory` for *reasoning episodes* (goal, answer, tools used, outcome, score, embedding) | new `memory_store.py` |
| **D2** | Pre-flight recall — `DeepReasoningAgent.run()` retrieves top-k similar past episodes (recency × score weighted) and injects them as a `## Relevant past experience` block in the system prompt | `agents/deep_reasoning_agent.py` |
| **D3** | Post-run persistence — every `HarnessResult` is summarised by a small LLM call to ~200 tokens and stored as an episode | `agents/deep_reasoning_agent.py`, `memory_store.py` |
| **D4** | Feedback API — `POST /api/reasoning/runs/{id}/feedback {rating, note}` updates the episode score | `main.py`, `memory_store.py` |
| **D5** | `GET /api/reasoning/memory?q=...` — search past episodes (powers the future memory browser UI) | `main.py` |

**Acceptance:** A reasoning run that solved "why is the lounge hot at
22:00" should, when asked again a week later, recall the prior
investigation and short-circuit (or at minimum cite the prior
finding).

> **Personal note:** For my setup I'm planning to keep `top_k` at 3 (not 5)
> for the pre-flight recall in D2 — my home assistant instance is on a
> Raspberry Pi 4 and the extra context noticeably slows the first LLM call.
> Will revisit if recall quality suffers.
>
> Also setting the episode summary target to ~150 tokens instead of ~200 —
> shorter summaries seem to work fine for my use cases and keep memory
> retrieval snappier on low-RAM hardware.

---

### Milestone E — Plan → Approve → Execute (PAE)

High-stakes goals run dry-first, surface a full execution plan, wait
for human approval, then replay deterministically.

| # | Task | Files |
|---|---|---|
| **E1** | Add `mode: "plan" \| "execute" \| "auto"` to `POST /api/reasoning/run` (default `auto`) | `main.py`, `agents/deep_reasoning_agent.py` |
| **E2** | `DryRunInterceptor` wraps every mutating tool executor in `ToolRegistry`; returns synthetic success and records the intent. Re