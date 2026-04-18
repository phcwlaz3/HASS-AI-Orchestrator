"""
Episodic memory for the deep reasoning agent (Phase 8 / D1).

A typed wrapper around :class:`RagManager`'s ``memory`` collection that
stores, scores and recalls *reasoning episodes*. Each episode captures
a single deep-reasoning run: the goal, the condensed summary the
reasoner produced afterwards, which tools it used, the outcome, and
optional human feedback.

Design notes
------------
* The store is **rag-backed**: episodes are embedded with the existing
  ``nomic-embed-text`` Ollama model and stored in ChromaDB at
  ``/data/chroma``. No new infrastructure.
* Embeddings are produced via the *async* embedding helper so the loop
  that owns the deep reasoner is not blocked.
* Recall ranks by ``similarity * recency_decay(age_days) * score_bias``
  so a successful, recent episode beats an old or downvoted one even
  if both are semantically equally relevant.
* The store *fails soft*: if no ``RagManager`` is wired in, every
  method becomes a no-op so the rest of the orchestrator keeps working
  on installs that never enabled RAG.

This module deliberately knows nothing about the LLM or the harness —
the deep reasoning agent owns that orchestration.
"""
from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Marker used in episode metadata so we can distinguish reasoning
#: episodes from other rows in the shared ``memory`` collection (e.g.
#: the legacy ``RagManager.add_memory`` records).
EPISODE_KIND = "reasoning_episode"


@dataclass
class ReasoningEpisode:
    """Persisted summary of one deep-reasoning run."""

    id: str
    goal: str
    summary: str
    answer: str
    iterations: int
    tool_calls: int
    tools_used: List[str]
    stopped_reason: str
    duration_ms: int
    timestamp: str  # ISO-8601 UTC
    score: float = 0.0  # -1.0 .. +1.0, set by feedback
    feedback_note: Optional[str] = None
    backend: Optional[str] = None
    extras: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def to_metadata(self) -> Dict[str, Any]:
        """Flatten to a Chroma-friendly metadata dict (scalars only)."""
        return {
            "kind": EPISODE_KIND,
            "id": self.id,
            "goal": self.goal[:1000],
            "summary": self.summary[:4000],
            "answer": self.answer[:4000],
            "iterations": int(self.iterations),
            "tool_calls": int(self.tool_calls),
            # Chroma metadata values must be primitives, so join lists.
            "tools_used": ",".join(self.tools_used)[:1000],
            "stopped_reason": self.stopped_reason,
            "duration_ms": int(self.duration_ms),
            "timestamp": self.timestamp,
            "score": float(self.score),
            "feedback_note": (self.feedback_note or "")[:1000],
            "backend": self.backend or "",
        }

    @classmethod
    def from_metadata(cls, meta: Dict[str, Any]) -> "ReasoningEpisode":
        tools_raw = meta.get("tools_used") or ""
        tools = [t for t in tools_raw.split(",") if t] if isinstance(tools_raw, str) else list(tools_raw or [])
        return cls(
            id=meta.get("id") or str(uuid.uuid4()),
            goal=meta.get("goal", ""),
            summary=meta.get("summary", ""),
            answer=meta.get("answer", ""),
            iterations=int(meta.get("iterations") or 0),
            tool_calls=int(meta.get("tool_calls") or 0),
            tools_used=tools,
            stopped_reason=meta.get("stopped_reason") or "",
            duration_ms=int(meta.get("duration_ms") or 0),
            timestamp=meta.get("timestamp") or _now_iso(),
            score=float(meta.get("score") or 0.0),
            feedback_note=(meta.get("feedback_note") or None) or None,
            backend=meta.get("backend") or None,
        )


@dataclass
class RecalledEpisode:
    """An episode plus the score the recall ranker assigned to it."""

    episode: ReasoningEpisode
    similarity: float          # 0..1, higher = closer (1 - chroma distance)
    recency_weight: float      # 0..1
    feedback_weight: float     # 0..2 (1.0 neutral, >1 boosted, <1 suppressed)
    final_score: float         # similarity * recency * feedback


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
class MemoryStore:
    """Typed access to reasoning episodes stored in ``RagManager.memory``.

    Parameters
    ----------
    rag_manager:
        The shared RAG manager. ``None`` is allowed and turns every
        method into a no-op (logged once).
    recency_half_life_days:
        Recency weighting half-life. After this many days the
        ``recency_weight`` is 0.5.
    min_similarity:
        Episodes whose Chroma distance maps to a similarity below this
        threshold are dropped from recall. Set to 0 to never drop on
        similarity alone.
    """

    def __init__(
        self,
        rag_manager: Any,
        *,
        recency_half_life_days: float = 30.0,
        min_similarity: float = 0.25,
    ) -> None:
        self.rag = rag_manager
        self.recency_half_life_days = recency_half_life_days
        self.min_similarity = min_similarity
        self._warned_disabled = False

    # ------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self.rag is not None

    def _disabled(self) -> bool:
        if self.enabled:
            return False
        if not self._warned_disabled:
            logger.info("MemoryStore disabled (no RagManager configured)")
            self._warned_disabled = True
        return True

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    async def remember(self, episode: ReasoningEpisode) -> Optional[str]:
        """Embed and persist a new episode. Returns the document id."""
        if self._disabled():
            return None

        text = _episode_to_text(episode)
        try:
            embedding = await self.rag._generate_embedding_async(text)
        except Exception as exc:
            logger.warning("MemoryStore.remember embedding failed: %s", exc)
            return None

        try:
            self.rag.memory.add(
                documents=[text],
                embeddings=[embedding],
                metadatas=[episode.to_metadata()],
                ids=[episode.id],
            )
            logger.debug("MemoryStore stored episode %s (goal=%r)", episode.id, episode.goal[:60])
            return episode.id
        except Exception as exc:
            logger.warning("MemoryStore.remember add failed: %s", exc)
            return None

    async def update_feedback(
        self,
        episode_id: str,
        rating: int,
        note: Optional[str] = None,
    ) -> bool:
        """Apply user feedback to an existing episode.

        ``rating`` is mapped to ``score``: -1, 0, +1.
        Returns True on success, False if the episode could not be found
        or the store is disabled.
        """
        if self._disabled():
            return False
        if rating not in (-1, 0, 1):
            raise ValueError("rating must be -1, 0, or 1")

        try:
            existing = self.rag.memory.get(ids=[episode_id], include=["metadatas", "documents"])
        except Exception as exc:
            logger.warning("MemoryStore.update_feedback get failed: %s", exc)
            return False

        metas = existing.get("metadatas") or []
        if not metas:
            return False
        meta = dict(metas[0] or {})
        meta["score"] = float(rating)
        if note is not None:
            meta["feedback_note"] = note[:1000]

        try:
            self.rag.memory.update(ids=[episode_id], metadatas=[meta])
            logger.debug("MemoryStore feedback applied to %s rating=%d", episode_id, rating)
            return True
        except Exception as exc:
            logger.warning("MemoryStore.update_feedback update failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    async def recall(
        self,
        query: str,
        *,
        k: int = 3,
        max_age_days: Optional[float] = 180.0,
    ) -> List[RecalledEpisode]:
        """Return the top-``k`` past episodes most relevant to ``query``.

        Ranking = ``similarity * recency_weight * feedback_weight``.

        Episodes older than ``max_age_days`` are excluded entirely
        (set to ``None`` to disable the cutoff).
        """
        if self._disabled() or not query.strip():
            return []

        try:
            embedding = await self.rag._generate_embedding_async(query)
        except Exception as exc:
            logger.warning("MemoryStore.recall embedding failed: %s", exc)
            return []

        # Pull a wider candidate pool than k so we can re-rank with
        # recency/feedback weighting; Chroma orders by raw distance.
        try:
            res = self.rag.memory.query(
                query_embeddings=[embedding],
                n_results=max(k * 4, 8),
                where={"kind": EPISODE_KIND},
            )
        except Exception as exc:
            logger.warning("MemoryStore.recall query failed: %s", exc)
            return []

        metas_batch = (res.get("metadatas") or [[]])[0]
        dists_batch = (res.get("distances") or [[]])[0]
        if not metas_batch:
            return []

        now = time.time()
        ranked: List[RecalledEpisode] = []
        for meta, dist in zip(metas_batch, dists_batch):
            if not meta:
                continue
            similarity = _distance_to_similarity(dist)
            if similarity < self.min_similarity:
                continue
            episode = ReasoningEpisode.from_metadata(meta)
            age_days = _episode_age_days(episode.timestamp, now)
            if max_age_days is not None and age_days > max_age_days:
                continue
            recency = _recency_weight(age_days, self.recency_half_life_days)
            feedback = _feedback_weight(episode.score)
            final = similarity * recency * feedback
            ranked.append(RecalledEpisode(
                episode=episode,
                similarity=similarity,
                recency_weight=recency,
                feedback_weight=feedback,
                final_score=final,
            ))

        ranked.sort(key=lambda r: r.final_score, reverse=True)
        return ranked[:k]

    # ------------------------------------------------------------------
    def get(self, episode_id: str) -> Optional[ReasoningEpisode]:
        """Fetch a single episode by id (sync — small lookup)."""
        if self._disabled():
            return None
        try:
            res = self.rag.memory.get(ids=[episode_id], include=["metadatas"])
        except Exception as exc:
            logger.warning("MemoryStore.get failed: %s", exc)
            return None
        metas = res.get("metadatas") or []
        if not metas:
            return None
        return ReasoningEpisode.from_metadata(metas[0] or {})

    def search_text(self, substring: str, limit: int = 20) -> List[ReasoningEpisode]:
        """Naive substring search over goals — for the memory browser
        UI when no semantic query is supplied. O(N) in episode count."""
        if self._disabled():
            return []
        try:
            res = self.rag.memory.get(
                where={"kind": EPISODE_KIND},
                include=["metadatas"],
                limit=1000,  # bound the scan
            )
        except Exception as exc:
            logger.warning("MemoryStore.search_text failed: %s", exc)
            return []
        needle = substring.lower().strip()
        out: List[ReasoningEpisode] = []
        for meta in res.get("metadatas") or []:
            if not meta:
                continue
            if needle and needle not in (meta.get("goal", "") or "").lower():
                continue
            out.append(ReasoningEpisode.from_metadata(meta))
            if len(out) >= limit:
                break
        # Most recent first.
        out.sort(key=lambda e: e.timestamp, reverse=True)
        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _episode_to_text(ep: ReasoningEpisode) -> str:
    """The text we embed. Designed so semantically similar *goals*
    cluster together while keeping enough outcome context that the
    similarity also reflects what the episode produced."""
    tools = ", ".join(ep.tools_used) if ep.tools_used else "(none)"
    return (
        f"GOAL: {ep.goal}\n"
        f"SUMMARY: {ep.summary}\n"
        f"TOOLS: {tools}\n"
        f"OUTCOME: {ep.stopped_reason} after {ep.iterations} iter, {ep.tool_calls} tool calls"
    )


def _distance_to_similarity(distance: Any) -> float:
    """Map Chroma's L2-ish distance to a 0..1 similarity. Chroma's
    cosine-distance collections produce values in [0, 2]; L2
    collections can produce larger numbers. We use a robust mapping
    that's monotone decreasing in distance and bounded in [0, 1].
    """
    try:
        d = float(distance)
    except (TypeError, ValueError):
        return 0.0
    if d <= 0:
        return 1.0
    # 1 / (1 + d) is a smooth, well-behaved mapping that handles
    # cosine and L2 distances without needing to know which one it is.
    return 1.0 / (1.0 + d)


def _episode_age_days(timestamp_iso: str, now_epoch: float) -> float:
    try:
        ts = datetime.fromisoformat(timestamp_iso)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0.0, (now_epoch - ts.timestamp()) / 86400.0)
    except (TypeError, ValueError):
        return 0.0


def _recency_weight(age_days: float, half_life_days: float) -> float:
    if half_life_days <= 0:
        return 1.0
    return math.exp(-math.log(2) * age_days / half_life_days)


def _feedback_weight(score: float) -> float:
    """Map a -1..+1 feedback score to a multiplicative weight.

    +1 -> 1.5 (boosted)
     0 -> 1.0 (neutral)
    -1 -> 0.4 (suppressed but not eliminated, so the model can still
                see *what not to do* in extreme cases)
    """
    s = max(-1.0, min(1.0, float(score)))
    if s >= 0:
        return 1.0 + 0.5 * s
    return 1.0 + 0.6 * s  # -1 -> 0.4
