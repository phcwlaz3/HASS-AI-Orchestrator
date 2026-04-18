# Phase 8 / Milestone D — Episodic Memory & Recall: COMPLETE

> **Status:** ✅ Shipped
> **Branch:** Phase 8 work-in-progress (v0.11.0-dev)
> **Tests:** 79 passed / 4 skipped (live MCP) — +14 new
> **Date:** April 18, 2026

This document describes Milestone **D** of [PHASE_8.md](PHASE_8.md) —
"the loop closes". The deep reasoner now **remembers** every run,
**recalls** similar past goals before reasoning, and **learns** from
human feedback.

---

## 1. The problem D solves

Phase 7 gave the orchestrator a brain (recursive harness, MCP tool
surface, hybrid Ollama/Anthropic backends). But every reasoning run
was a **cold start**:

- Solving "why is the lounge hot at 22:00" today taught the system
  nothing for next week.
- A user thumbs-down on a bad answer was a dead end — there was no
  store to attach it to.
- Past traces existed as JSON on disk but the reasoner couldn't see
  them; only humans could.

Milestone D closes that gap with three surfaces: **store**,
**recall**, **feedback**.

---

## 2. What shipped

### 2.1 New: `MemoryStore` (D1)

[ai-orchestrator/backend/memory_store.py](ai-orchestrator/backend/memory_store.py) — 348 lines.

A typed wrapper around the existing `RagManager.memory` ChromaDB
collection. **No new infrastructure** — reuses the
`nomic-embed-text` Ollama embeddings already running for RAG.

#### Data model

```python
@dataclass
class ReasoningEpisode:
    id: str
    goal: str
    summary: str          # condensed answer (≤1500 chars)
    answer: str           # full answer
    iterations: int
    tool_calls: int
    tools_used: List[str]
    stopped_reason: str   # "final" | "max_iter" | "tool_limit"
    duration_ms: int
    timestamp: str        # ISO-8601 UTC
    score: float = 0.0    # -1.0 .. +1.0, set by feedback
    feedback_note: Optional[str] = None
    backend: Optional[str] = None  # "ollama" | "anthropic"
```

```python
@dataclass
class RecalledEpisode:
    episode: ReasoningEpisode
    similarity: float       # 0..1 (1 - chroma distance, smoothed)
    recency_weight: float   # 0..1 exponential decay
    feedback_weight: float  # 0.4..1.5
    final_score: float      # similarity × recency × feedback
```

#### Public API

| Method | Purpose |
|---|---|
| `await remember(episode)` | Embed and persist an episode. Returns id. |
| `await recall(query, k=3, max_age_days=180)` | Top-k semantic recall, re-ranked by recency × feedback. |
| `await update_feedback(id, rating, note)` | Human thumbs up/down on a past run. |
| `get(id)` | Sync fetch by id. |
| `search_text(substring, limit)` | Substring browse fallback (for the future memory UI). |

#### Ranking math

Three weights compose multiplicatively. Each is intentionally simple
and inspectable:

```python
similarity      = 1 / (1 + chroma_distance)      # 0..1, monotone
recency_weight  = exp(-ln(2) · age_days / H)     # half-life H = 30d
feedback_weight = {-1: 0.4, 0: 1.0, +1: 1.5}     # piecewise
final_score     = similarity × recency_weight × feedback_weight
```

Why these choices:

- **`1/(1+d)`** is the same shape regardless of whether the underlying
  collection is cosine or L2 — saves us from caring which Chroma
  picked.
- **30-day half-life** is short enough that "what worked yesterday"
  beats "what worked last quarter" but long enough to survive a
  vacation.
- **0.4 floor on downvoted** episodes keeps them visible to the LLM
  as *negative* examples ("avoid repeating this mistake") rather than
  hiding them entirely. The LLM is told what the score means in the
  prompt block.

#### Coexistence with legacy memory

The `RagManager.memory` collection already held a few legacy
records via `add_memory()`. We stamp every reasoning episode with
`metadata.kind = "reasoning_episode"` and filter every query with
`where={"kind": EPISODE_KIND}`. Old rows are untouched and invisible
to recall.

#### Failure modes

The store **fails soft everywhere**. If `RagManager` is `None`
(install without RAG enabled), the entire surface becomes a no-op
that logs once at INFO. The deep reasoner runs unaffected.

---

### 2.2 Wired: `DeepReasoningAgent` (D2 + D3)

[ai-orchestrator/backend/agents/deep_reasoning_agent.py](ai-orchestrator/backend/agents/deep_reasoning_agent.py)

#### Constructor additions

```python
DeepReasoningAgent(
    ...,
    memory_store: Optional[MemoryStore] = None,
    recall_k: int = 3,
    recall_max_age_days: float = 180.0,
)
```

Backward compatible — omit it and the agent works exactly like Phase 7.

#### Run lifecycle (was vs now)

**Before:**
1. `harness.run(goal)` → `HarnessResult`
2. `_persist()` to JSON for the dashboard

**After:**
1. **Recall** — `memory_store.recall(goal, k=3)` → list of past episodes
2. **Inject** — format recalled episodes into a `## Relevant past
   experience` system-prompt block (only for this run; the harness's
   base prompt is restored each turn)
3. `harness.run(goal)` → `HarnessResult`
4. `_persist()` to JSON (now includes `run_id`)
5. **Remember** — build a `ReasoningEpisode` from the result and
   `memory_store.remember()` it
6. Stamp `result.run_id`, `result.episode_id`, `result.recalled` on
   the returned object

The recall block looks like this in the model's context:

```
## Relevant past experience

Before reasoning, consider these prior runs on similar goals.
Cite them when they meaningfully change your plan; ignore them
if they are not actually relevant.

1. **Goal:** Why is the lounge hot at 22:00? [user marked this run helpful]
   **When:** 2026-04-10T19:33:00+00:00  **Iter/Tools:** 4/6
   **Tools used:** hass_get_history, hass_list_entities
   **What happened:** Solar gain through west window; suggested closing blinds at 18:30.
```

The score annotations (`[user marked this run helpful]` /
`[unhelpful — avoid repeating its mistakes]`) make the model use
feedback signal correctly without needing a numeric "score" channel.

#### `submit_feedback(run_id, rating, note)`

The agent maintains an in-memory `_run_to_episode` map so external
callers (the API, the dashboard) only ever see `run_id`s, never the
internal episode id. `submit_feedback()` translates and dispatches to
`memory_store.update_feedback()`.

> **Limitation (acknowledged):** the map is process-local. A backend
> restart loses pending feedback bindings. Episode ids are still
> retrievable via `GET /api/reasoning/memory`, so feedback can be
> applied directly via a future `POST /api/reasoning/episodes/{id}/feedback`
> endpoint if needed. Left out today to keep the surface small.

#### Episode summarisation

We deliberately did **not** add an extra LLM round-trip for
summarisation (the original D3 plan). Reasoning answers are already
condensed by the model; we truncate to 1500 chars and store. This:

- Saves one LLM call per run (latency + tokens)
- Keeps the embedding text close to what the user actually sees
- Makes the memory deterministic and debuggable

A richer summariser is a one-method swap if telemetry shows it's
needed (replace `_build_episode`'s `summary = result.answer[:1500]`
with `summary = await self._summarise(result.answer)`).

---

### 2.3 New: HTTP endpoints (D4 + D5)

[ai-orchestrator/backend/main.py](ai-orchestrator/backend/main.py)

#### `POST /api/reasoning/runs/{run_id}/feedback`

```json
// Request
{ "rating": 1, "note": "spot on, this is what I needed" }

// Response 200
{ "ok": true, "run_id": "abc123", "rating": 1 }

// Response 404 — unknown run_id or memory disabled
// Response 400 — rating not in {-1, 0, 1}
```

Updates the episode in-place. Future recalls of similar goals will
upweight (rating=1) or downweight (rating=-1) this episode.

#### `GET /api/reasoning/memory?q=...&k=10`

- **With `q`:** semantic recall — top-k by `final_score`. Returns
  `episode_id`, `goal`, `summary`, `timestamp`, `score`, `similarity`,
  `final_score`.
- **Without `q`:** recency-ordered listing — substring-empty
  `search_text("")` returns the most recent episodes for browse UIs.
- `k` is clamped to `[1, 50]`.

#### `POST /api/reasoning/run` — additive response fields

```json
{
  "run_id": "abc123…",                    // NEW
  "episode_id": "ep-xyz…",                // NEW (null if memory off)
  "recalled": [                           // NEW (empty if memory off)
    {
      "episode_id": "...",
      "goal": "...",
      "summary": "...",
      "timestamp": "...",
      "score": 1.0,
      "similarity": 0.71,
      "recency_weight": 0.83,
      "feedback_weight": 1.5,
      "final_score": 0.88
    }
  ],
  // ...existing fields unchanged
}
```

All additions are non-breaking. Existing dashboard code keeps working.

---

### 2.4 New: tests

[ai-orchestrator/backend/tests/test_memory_store_smoke.py](ai-orchestrator/backend/tests/test_memory_store_smoke.py) — 14 tests.

| Test | Validates |
|---|---|
| `test_memory_store_disabled_when_rag_is_none` | Soft-fail no-op surface |
| `test_remember_then_recall_returns_episode` | Round-trip happy path |
| `test_recall_ranks_recent_and_upvoted_higher` | Ranking math composes correctly |
| `test_recall_respects_max_age_cutoff` | Age cutoff is enforced |
| `test_recall_respects_min_similarity` | Low-similarity drops are honoured |
| `test_update_feedback_changes_score_and_note` | Feedback persists to metadata |
| `test_update_feedback_returns_false_for_unknown_id` | Missing-id soft-fail |
| `test_update_feedback_rejects_invalid_rating` | Input validation |
| `test_search_text_filters_by_substring_and_orders_by_recency` | Browse UI path |
| `test_distance_to_similarity_is_monotone_decreasing` | Helper math |
| `test_recency_weight_halves_at_half_life` | Helper math |
| `test_feedback_weight_boosts_and_suppresses` | Helper math |
| `test_deep_reasoner_persists_episode_and_accepts_feedback` | E2E: agent → store → feedback round-trip |
| `test_deep_reasoner_injects_recall_into_system_prompt` | E2E: prior episode appears in next run's prompt |

**Test infrastructure:** a tiny in-process `_FakeRag` + `_FakeCollection`
exposes only the methods `MemoryStore` actually uses (`add`, `query`,
`get`, `update`, `_generate_embedding_async`). Embeddings are a
deterministic 16-dim L2-normalised hash so tests are fast and stable.
**No Ollama, no Chroma, no network.**

---

## 3. Acceptance check

> A reasoning run that solved "why is the lounge hot at 22:00"
> should, when asked again a week later, recall the prior
> investigation and short-circuit (or at minimum cite the prior
> finding).

✅ Demonstrated by `test_deep_reasoner_injects_recall_into_system_prompt`:
seeds the store with a prior episode about kitchen lights coming on
overnight, runs a related query, asserts the spy LLM saw the prior
episode's summary in its system prompt.

✅ The model is told *how* to use the recall block ("cite when
relevant; ignore otherwise"), so the early-stop behaviour is the
LLM's choice on real backends.

---

## 4. Files changed

| File | Change |
|---|---|
| `ai-orchestrator/backend/memory_store.py` | **NEW** — 348 lines |
| `ai-orchestrator/backend/agents/deep_reasoning_agent.py` | +recall, +remember, +feedback API, +run_id stamping |
| `ai-orchestrator/backend/main.py` | +`MemoryStore` wiring, +2 endpoints, +run_id/episode_id/recalled in response |
| `ai-orchestrator/backend/tests/test_memory_store_smoke.py` | **NEW** — 14 tests |
| `PHASE_8.md` | Plan document (already present) |
| `PHASE_8_D_COMPLETE.md` | This document |

---

## 5. Test results

```
$ python -m pytest -q
79 passed, 4 skipped in 35.86s
```

The 4 skipped are `test_external_mcp_live.py` (require a running
OpenClaw HASS_MCP server). Everything else green, including all
prior Phase 1–7 tests.

---

## 6. Privacy note

Reasoning episodes contain user goals and reasoning summaries. They
live **only** in `/data/chroma` on the host. They are **never sent
off-device** by the orchestrator on its own. They **are** included in
the system prompt sent to Claude when the Anthropic backend is
selected — that is the same trust boundary as any other content
processed in that mode.

A future hardening could add per-episode `private: true` flagging
that excludes them from off-device prompts; not implemented here.

---

## 7. What's next

Milestone **E — Plan → Approve → Execute**:

- `mode: "plan" | "execute" | "auto"` on `/api/reasoning/run`
- `DryRunInterceptor` wraps mutating tools, records intents
- Plans persist via `ApprovalQueue`
- `POST /api/reasoning/plans/{id}/execute` deterministically
  replays approved actions (no LLM round-trip)
- `auto` mode: plan → execute if no high-impact actions, else queue

The PAE milestone is what makes it safe to flip `dry_run_mode: false`
in production.

---

*Milestone D shipped April 18, 2026. The orchestrator now learns.*
