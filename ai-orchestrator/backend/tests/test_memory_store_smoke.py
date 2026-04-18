"""Smoke tests for the Phase 8 episodic memory store + deep-reasoner integration.

The MemoryStore is RAG-backed but we don't want to run real ChromaDB
or Ollama in unit tests. We build a tiny in-memory fake that exposes
the same surface used by ``MemoryStore`` (``add``, ``query``, ``get``,
``update``) plus ``RagManager._generate_embedding_async``.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

from memory_store import (
    EPISODE_KIND,
    MemoryStore,
    ReasoningEpisode,
    _distance_to_similarity,
    _feedback_weight,
    _recency_weight,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class _FakeCollection:
    """Minimal stand-in for a Chroma collection."""

    def __init__(self) -> None:
        self.rows: Dict[str, Dict[str, Any]] = {}

    def add(self, *, documents, embeddings, metadatas, ids):
        for doc, emb, meta, _id in zip(documents, embeddings, metadatas, ids):
            self.rows[_id] = {"doc": doc, "embedding": list(emb), "meta": dict(meta)}

    def get(self, *, ids=None, where=None, include=None, limit=None):
        items = list(self.rows.items())
        if ids:
            items = [(i, r) for i, r in items if i in ids]
        if where:
            items = [(i, r) for i, r in items if all(r["meta"].get(k) == v for k, v in where.items())]
        if limit:
            items = items[:limit]
        return {
            "ids": [i for i, _ in items],
            "metadatas": [r["meta"] for _, r in items],
            "documents": [r["doc"] for _, r in items],
        }

    def update(self, *, ids, metadatas):
        for _id, meta in zip(ids, metadatas):
            if _id in self.rows:
                self.rows[_id]["meta"].update(meta)

    def query(self, *, query_embeddings, n_results=5, where=None):
        target = query_embeddings[0]
        scored = []
        for _id, r in self.rows.items():
            if where and not all(r["meta"].get(k) == v for k, v in where.items()):
                continue
            # Euclidean-ish distance.
            dist = sum((a - b) ** 2 for a, b in zip(r["embedding"], target)) ** 0.5
            scored.append((_id, dist, r))
        scored.sort(key=lambda x: x[1])
        scored = scored[:n_results]
        return {
            "ids": [[s[0] for s in scored]],
            "distances": [[s[1] for s in scored]],
            "metadatas": [[s[2]["meta"] for s in scored]],
            "documents": [[s[2]["doc"] for s in scored]],
        }


class _FakeRag:
    """Stand-in for ``RagManager`` exposing only what MemoryStore uses."""

    def __init__(self) -> None:
        self.memory = _FakeCollection()
        # Tiny deterministic embedder: 16-dim hash-based vector.
        # Texts that share words land close together.
        self._dim = 16

    async def _generate_embedding_async(self, text: str) -> List[float]:
        return _toy_embed(text, self._dim)


def _toy_embed(text: str, dim: int) -> List[float]:
    vec = [0.0] * dim
    for token in text.lower().split():
        h = hash(token) % dim
        vec[h] += 1.0
    # L2 normalise so distances are comparable.
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


def _make_episode(goal: str, *, summary: str = "", score: float = 0.0,
                  age_days: float = 0.0, tools: Optional[List[str]] = None,
                  ep_id: Optional[str] = None) -> ReasoningEpisode:
    ts = datetime.now(timezone.utc) - timedelta(days=age_days)
    return ReasoningEpisode(
        id=ep_id or f"ep-{goal[:20]}-{age_days}",
        goal=goal,
        summary=summary or f"resolved: {goal}",
        answer=summary or goal,
        iterations=2,
        tool_calls=3,
        tools_used=tools or ["hass_list_entities", "hass_get_state"],
        stopped_reason="final",
        duration_ms=1234,
        timestamp=ts.isoformat(),
        score=score,
    )


# ---------------------------------------------------------------------------
# Disabled store
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_memory_store_disabled_when_rag_is_none():
    store = MemoryStore(None)
    assert store.enabled is False
    assert await store.remember(_make_episode("anything")) is None
    assert await store.recall("anything") == []
    assert await store.update_feedback("any-id", 1) is False
    assert store.get("any-id") is None
    assert store.search_text("anything") == []


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_remember_then_recall_returns_episode():
    store = MemoryStore(_FakeRag(), min_similarity=0.0)
    ep = _make_episode("Why is the lounge hot at 22:00?",
                       summary="Solar gain through west window; suggested closing blinds.",
                       tools=["hass_get_history", "hass_list_entities"])
    await store.remember(ep)

    recalled = await store.recall("Lounge is hot in the evening", k=3)

    assert len(recalled) == 1
    r = recalled[0]
    assert r.episode.id == ep.id
    assert r.episode.summary.startswith("Solar gain")
    assert 0.0 < r.similarity <= 1.0
    assert r.final_score > 0.0


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_recall_ranks_recent_and_upvoted_higher():
    store = MemoryStore(_FakeRag(), min_similarity=0.0, recency_half_life_days=10.0)

    old_unrated = _make_episode("kitchen lights energy audit", age_days=120, score=0.0,
                                ep_id="old-neutral")
    recent_upvoted = _make_episode("kitchen lights energy audit", age_days=2, score=1.0,
                                   ep_id="recent-up")
    recent_downvoted = _make_episode("kitchen lights energy audit", age_days=2, score=-1.0,
                                     ep_id="recent-down")
    for ep in (old_unrated, recent_upvoted, recent_downvoted):
        await store.remember(ep)

    recalled = await store.recall("kitchen lights energy audit", k=3, max_age_days=None)
    ids_in_order = [r.episode.id for r in recalled]

    # Upvoted recent must beat both other candidates.
    assert ids_in_order[0] == "recent-up"
    # Old episode (120d) decays past even a downvoted recent one,
    # so it should land at the bottom.
    assert ids_in_order[-1] == "old-neutral"
    # Sanity: a downvoted recent episode is still ranked above a
    # century-old neutral one (recency dominates feedback here).
    assert ids_in_order.index("recent-down") < ids_in_order.index("old-neutral")


@pytest.mark.asyncio
async def test_recall_respects_max_age_cutoff():
    store = MemoryStore(_FakeRag(), min_similarity=0.0)
    fresh = _make_episode("garage door investigation", age_days=1, ep_id="fresh")
    stale = _make_episode("garage door investigation", age_days=400, ep_id="stale")
    await store.remember(fresh)
    await store.remember(stale)

    recalled = await store.recall("garage door investigation", k=5, max_age_days=90)

    assert [r.episode.id for r in recalled] == ["fresh"]


@pytest.mark.asyncio
async def test_recall_respects_min_similarity():
    # Force the min-similarity threshold high enough to drop everything.
    store = MemoryStore(_FakeRag(), min_similarity=0.999)
    await store.remember(_make_episode("totally unrelated topic"))

    recalled = await store.recall("a query that shares no tokens at all", k=3)
    assert recalled == []


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_update_feedback_changes_score_and_note():
    rag = _FakeRag()
    store = MemoryStore(rag, min_similarity=0.0)
    ep = _make_episode("front door alert investigation", ep_id="ep-front-door")
    await store.remember(ep)

    ok = await store.update_feedback(ep.id, rating=1, note="exactly what I wanted")
    assert ok is True

    fetched = store.get(ep.id)
    assert fetched is not None
    assert fetched.score == 1.0
    assert fetched.feedback_note == "exactly what I wanted"


@pytest.mark.asyncio
async def test_update_feedback_returns_false_for_unknown_id():
    store = MemoryStore(_FakeRag())
    assert await store.update_feedback("does-not-exist", rating=1) is False


@pytest.mark.asyncio
async def test_update_feedback_rejects_invalid_rating():
    store = MemoryStore(_FakeRag())
    with pytest.raises(ValueError):
        await store.update_feedback("any-id", rating=2)


# ---------------------------------------------------------------------------
# Search-text browser path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_search_text_filters_by_substring_and_orders_by_recency():
    store = MemoryStore(_FakeRag())
    a = _make_episode("kitchen lights audit", age_days=1, ep_id="a")
    b = _make_episode("garage door investigation", age_days=2, ep_id="b")
    c = _make_episode("kitchen lights deep dive", age_days=3, ep_id="c")
    for ep in (a, b, c):
        await store.remember(ep)

    out = store.search_text("kitchen")
    assert [e.id for e in out] == ["a", "c"]

    out_all = store.search_text("")
    # Most recent first across everything (a, b, c by age).
    assert [e.id for e in out_all] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Helper math
# ---------------------------------------------------------------------------
def test_distance_to_similarity_is_monotone_decreasing():
    assert _distance_to_similarity(0) == 1.0
    assert _distance_to_similarity(0.5) > _distance_to_similarity(1.0)
    assert _distance_to_similarity(10.0) > 0.0
    assert _distance_to_similarity("not a number") == 0.0


def test_recency_weight_halves_at_half_life():
    assert _recency_weight(0, 30) == pytest.approx(1.0)
    assert _recency_weight(30, 30) == pytest.approx(0.5)
    assert _recency_weight(60, 30) == pytest.approx(0.25)


def test_feedback_weight_boosts_and_suppresses():
    assert _feedback_weight(0.0) == pytest.approx(1.0)
    assert _feedback_weight(1.0) == pytest.approx(1.5)
    assert _feedback_weight(-1.0) == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# Integration with the deep reasoning agent
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_deep_reasoner_persists_episode_and_accepts_feedback(monkeypatch):
    """End-to-end: run a fake harness, ensure an episode is persisted,
    then submit feedback and confirm the score is updated."""
    import agents.deep_reasoning_agent as dra

    # Stub LLM so DeepReasoningAgent.__init__ doesn't reach for ollama/anthropic.
    class _StubLLM:
        name = "stub"
        async def chat(self, messages, tools):
            from reasoning_harness import LLMResponse
            return LLMResponse(content="done")

    monkeypatch.setattr(dra, "OllamaToolBackend", lambda **kw: _StubLLM())

    # Stub local MCP with no tools and a pass-through executor.
    class _StubMCP:
        tools: Dict[str, Any] = {}
        async def execute_tool(self, **kw):
            return {"ok": True}

    rag = _FakeRag()
    store = MemoryStore(rag, min_similarity=0.0)

    agent = dra.DeepReasoningAgent(
        local_mcp=_StubMCP(),
        external_mcp=None,
        ollama_model="ignored",
        memory_store=store,
        recall_k=3,
    )

    result = await agent.run("Investigate the upstairs hallway lights")

    # The harness immediately returned a final answer (no tool calls),
    # but the agent should still have persisted an episode.
    run_id = getattr(result, "run_id", None)
    episode_id = getattr(result, "episode_id", None)
    assert run_id is not None
    assert episode_id is not None
    assert result.answer == "done"

    # Apply feedback through the agent's API.
    ok = await agent.submit_feedback(run_id, rating=1, note="great")
    assert ok is True

    fetched = store.get(episode_id)
    assert fetched is not None
    assert fetched.score == 1.0
    assert fetched.feedback_note == "great"


@pytest.mark.asyncio
async def test_deep_reasoner_injects_recall_into_system_prompt(monkeypatch):
    """When prior episodes exist, the next run's system prompt must
    contain a 'Relevant past experience' section that mentions one of them."""
    import agents.deep_reasoning_agent as dra
    from reasoning_harness import LLMResponse

    seen_system_prompts: List[str] = []

    class _SpyLLM:
        name = "spy"
        async def chat(self, messages, tools):
            for m in messages:
                if m.get("role") == "system":
                    seen_system_prompts.append(m["content"])
            return LLMResponse(content="ack")

    monkeypatch.setattr(dra, "OllamaToolBackend", lambda **kw: _SpyLLM())

    class _StubMCP:
        tools: Dict[str, Any] = {}
        async def execute_tool(self, **kw):
            return {"ok": True}

    rag = _FakeRag()
    store = MemoryStore(rag, min_similarity=0.0)

    # Seed the store with a relevant prior episode.
    await store.remember(_make_episode(
        "Investigate why the kitchen lights are on at 03:00",
        summary="Found a stuck motion sensor in the pantry; suggested replacement.",
        tools=["hass_get_history"],
        ep_id="seed-1",
    ))

    agent = dra.DeepReasoningAgent(
        local_mcp=_StubMCP(),
        external_mcp=None,
        ollama_model="ignored",
        memory_store=store,
        recall_k=3,
    )

    await agent.run("Investigate kitchen lights coming on overnight")

    assert seen_system_prompts, "no system prompt was sent"
    full_prompt = seen_system_prompts[0]
    assert "Relevant past experience" in full_prompt
    assert "kitchen lights" in full_prompt.lower()
    assert "stuck motion sensor" in full_prompt.lower()
