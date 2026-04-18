"""Smoke tests for Phase 8 / Milestone F (proactive triggers)."""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

from triggers import (
    CronExpr,
    TriggerFireRecord,
    TriggerRegistry,
    TriggerSpec,
    TriggerStore,
    _render_goal,
    _state_matches,
)


# ---------------------------------------------------------------------------
# Cron parser
# ---------------------------------------------------------------------------
class TestCronExpr:
    def test_every_minute_matches_anything(self):
        e = CronExpr.parse("* * * * *")
        assert e.matches(datetime(2026, 4, 18, 12, 34))

    def test_at_minute_zero_only(self):
        e = CronExpr.parse("0 * * * *")
        assert e.matches(datetime(2026, 4, 18, 12, 0))
        assert not e.matches(datetime(2026, 4, 18, 12, 5))

    def test_hour_range(self):
        e = CronExpr.parse("0 9-17 * * *")
        assert e.matches(datetime(2026, 4, 18, 9, 0))
        assert e.matches(datetime(2026, 4, 18, 17, 0))
        assert not e.matches(datetime(2026, 4, 18, 8, 0))
        assert not e.matches(datetime(2026, 4, 18, 18, 0))

    def test_step(self):
        e = CronExpr.parse("*/15 * * * *")
        for m in (0, 15, 30, 45):
            assert e.matches(datetime(2026, 4, 18, 12, m))
        for m in (5, 14, 29, 31):
            assert not e.matches(datetime(2026, 4, 18, 12, m))

    def test_list(self):
        e = CronExpr.parse("0 6,12,18 * * *")
        assert e.matches(datetime(2026, 4, 18, 6, 0))
        assert e.matches(datetime(2026, 4, 18, 12, 0))
        assert e.matches(datetime(2026, 4, 18, 18, 0))
        assert not e.matches(datetime(2026, 4, 18, 7, 0))

    def test_alias_nightly(self):
        e = CronExpr.parse("@nightly")
        assert e.matches(datetime(2026, 4, 18, 22, 0))
        assert not e.matches(datetime(2026, 4, 18, 21, 0))

    def test_dow_sunday(self):
        # Cron Sunday=0; April 19 2026 is a Sunday.
        e = CronExpr.parse("0 9 * * 0")
        assert e.matches(datetime(2026, 4, 19, 9, 0))
        assert not e.matches(datetime(2026, 4, 18, 9, 0))  # Saturday

    def test_dow_or_dom_when_both_restricted(self):
        # Standard cron: when both DoM and DoW are restricted, match
        # on either. April 18 2026 is the 18th (matches DoM) but a
        # Saturday (DoW=6, not in {0}). Should still match.
        e = CronExpr.parse("0 9 18 * 0")
        assert e.matches(datetime(2026, 4, 18, 9, 0))   # DoM hit
        assert e.matches(datetime(2026, 4, 19, 9, 0))   # DoW hit (Sunday)
        assert not e.matches(datetime(2026, 4, 17, 9, 0))

    def test_invalid_field_count(self):
        with pytest.raises(ValueError):
            CronExpr.parse("0 0 0")

    def test_invalid_value_out_of_range(self):
        with pytest.raises(ValueError):
            CronExpr.parse("60 * * * *")

    def test_next_fire_after(self):
        e = CronExpr.parse("0 0 * * *")  # every midnight
        nxt = e.next_fire_after(datetime(2026, 4, 18, 23, 59))
        assert nxt == datetime(2026, 4, 19, 0, 0)


# ---------------------------------------------------------------------------
# State pattern matching
# ---------------------------------------------------------------------------
class TestStateMatching:
    def test_none_pattern_matches_anything(self):
        assert _state_matches("on", None) is True
        assert _state_matches(None, None) is True

    def test_exact_match_case_insensitive(self):
        assert _state_matches("ON", "on") is True
        assert _state_matches("off", "on") is False

    def test_regex_pattern(self):
        assert _state_matches("unavailable", "~^un") is True
        assert _state_matches("unknown", "~^un") is True
        assert _state_matches("on", "~^un") is False

    def test_invalid_regex_returns_false(self):
        assert _state_matches("on", "~[invalid") is False


# ---------------------------------------------------------------------------
# Goal templating
# ---------------------------------------------------------------------------
class TestGoalRendering:
    def test_substitutes_known_keys(self):
        out = _render_goal("Investigate {entity_id} ({reason})",
                           {"entity_id": "binary_sensor.front_door", "reason": "open 5min"})
        assert out == "Investigate binary_sensor.front_door (open 5min)"

    def test_leaves_unknown_keys_intact(self):
        out = _render_goal("hello {missing}", {"other": "value"})
        assert out == "hello {missing}"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
@pytest.fixture
def store(tmp_path):
    return TriggerStore(db_path=str(tmp_path / "triggers.db"))


def _cron_spec(name="t1", cron="0 22 * * *", **kw) -> TriggerSpec:
    return TriggerSpec(
        id=name, name=name, type="cron", goal_template="do {trigger_name}",
        cron=cron, **kw,
    )


def _state_spec(name="s1", entity_id="binary_sensor.front_door",
                pattern="on", sustained=0, **kw) -> TriggerSpec:
    return TriggerSpec(
        id=name, name=name, type="state",
        goal_template="check {entity_id}", entity_id=entity_id,
        state_pattern=pattern, sustained_seconds=sustained, **kw,
    )


class TestTriggerStore:
    def test_save_get_list_delete(self, store):
        spec = _cron_spec("nightly", cron="@nightly")
        store.save(spec)
        out = store.get("nightly")
        assert out is not None
        assert out.cron == "@nightly"
        assert store.list() and store.list()[0].id == "nightly"

        assert store.delete("nightly") is True
        assert store.get("nightly") is None
        assert store.delete("nightly") is False

    def test_enabled_only_filter(self, store):
        a = _cron_spec("a", cron="* * * * *")
        b = _cron_spec("b", cron="* * * * *", enabled=False)
        store.save(a)
        store.save(b)
        assert {s.id for s in store.list(enabled_only=True)} == {"a"}
        assert {s.id for s in store.list()} == {"a", "b"}

    def test_record_and_list_fires(self, store):
        store.save(_cron_spec("nightly", cron="@nightly"))
        for i in range(3):
            store.record_fire(TriggerFireRecord(
                id=f"f{i}", trigger_id="nightly",
                timestamp=datetime(2026, 4, 18, i, 0).isoformat(),
                goal="g", status="completed",
            ))
        fires = store.list_fires(trigger_id="nightly")
        assert len(fires) == 3
        assert fires[0].id == "f2"  # newest first


# ---------------------------------------------------------------------------
# Registry — fire path
# ---------------------------------------------------------------------------
class _StubReasonerResult:
    def __init__(self, run_id="r1", plan=None):
        self.run_id = run_id
        self.plan = plan


@pytest.mark.asyncio
async def test_registry_fire_records_fire_with_plan_metadata(store):
    captured: List[Dict[str, Any]] = []
    async def reasoner(goal, ctx):
        captured.append({"goal": goal, "ctx": ctx})
        return _StubReasonerResult(plan={"id": "p1", "requires_approval": True})

    reg = TriggerRegistry(store=store, reasoner_callback=reasoner)
    spec = _state_spec("s1", entity_id="binary_sensor.front_door")
    store.save(spec)

    await reg._fire(spec, reason="manual test")

    assert len(captured) == 1
    assert captured[0]["goal"] == "check binary_sensor.front_door"
    assert captured[0]["ctx"]["trigger_reason"] == "manual test"

    fires = store.list_fires(trigger_id="s1")
    assert len(fires) == 1
    assert fires[0].run_id == "r1"
    assert fires[0].plan_id == "p1"
    assert fires[0].status == "awaiting_approval"


@pytest.mark.asyncio
async def test_registry_fire_marks_executed_when_plan_executed(store):
    async def reasoner(goal, ctx):
        return _StubReasonerResult(plan={"id": "p1", "status": "executed", "requires_approval": False})

    reg = TriggerRegistry(store=store, reasoner_callback=reasoner)
    spec = _state_spec("s2", entity_id="light.kitchen", pattern="on")
    store.save(spec)
    await reg._fire(spec, reason="state")
    fires = store.list_fires(trigger_id="s2")
    assert fires[0].status == "executed"


@pytest.mark.asyncio
async def test_registry_fire_marks_error_on_reasoner_exception(store):
    async def reasoner(goal, ctx):
        raise RuntimeError("kaboom")

    reg = TriggerRegistry(store=store, reasoner_callback=reasoner)
    spec = _cron_spec("c1", cron="* * * * *")
    store.save(spec)
    await reg._fire(spec, reason="cron")
    fires = store.list_fires(trigger_id="c1")
    assert fires[0].status == "error"
    assert "RuntimeError" in (fires[0].note or "")


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cooldown_blocks_back_to_back_fires(store):
    fired: List[str] = []
    async def reasoner(goal, ctx):
        fired.append(goal)
        return _StubReasonerResult()

    reg = TriggerRegistry(store=store, reasoner_callback=reasoner)
    spec = _state_spec("s3", entity_id="x.y", cooldown_seconds=60)
    store.save(spec)

    # First fire ok.
    assert reg._cooldown_ok(spec) is True
    await reg._fire(spec, reason="t1")
    # Second fire should be blocked by in-process cooldown.
    assert reg._cooldown_ok(spec) is False
    assert len(fired) == 1


@pytest.mark.asyncio
async def test_cooldown_zero_means_no_throttle(store):
    fired: List[str] = []
    async def reasoner(goal, ctx):
        fired.append("x")
        return _StubReasonerResult()

    reg = TriggerRegistry(store=store, reasoner_callback=reasoner)
    spec = _state_spec("s4", cooldown_seconds=0)
    store.save(spec)
    await reg._fire(spec, reason="a")
    assert reg._cooldown_ok(spec) is True


# ---------------------------------------------------------------------------
# CRUD via registry
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_add_assigns_id_and_persists(store):
    async def reasoner(goal, ctx):
        return _StubReasonerResult()

    reg = TriggerRegistry(store=store, reasoner_callback=reasoner)
    spec = TriggerSpec(
        id="", name="n", type="cron", goal_template="g",
        cron="@daily",
    )
    out = await reg.add(spec)
    assert out.id  # auto-generated
    assert store.get(out.id) is not None


@pytest.mark.asyncio
async def test_add_rejects_invalid_cron(store):
    async def reasoner(goal, ctx):
        return _StubReasonerResult()
    reg = TriggerRegistry(store=store, reasoner_callback=reasoner)
    spec = TriggerSpec(id="", name="n", type="cron", goal_template="g", cron="not-a-cron")
    with pytest.raises(ValueError):
        await reg.add(spec)


@pytest.mark.asyncio
async def test_add_rejects_state_without_entity_id(store):
    async def reasoner(goal, ctx):
        return _StubReasonerResult()
    reg = TriggerRegistry(store=store, reasoner_callback=reasoner)
    spec = TriggerSpec(id="", name="n", type="state", goal_template="g", entity_id=None)
    with pytest.raises(ValueError):
        await reg.add(spec)


@pytest.mark.asyncio
async def test_delete_removes_from_store(store):
    async def reasoner(goal, ctx):
        return _StubReasonerResult()
    reg = TriggerRegistry(store=store, reasoner_callback=reasoner)
    spec = await reg.add(_cron_spec("d1", cron="@daily"))
    assert await reg.delete(spec.id) is True
    assert store.get(spec.id) is None
    assert await reg.delete(spec.id) is False


# ---------------------------------------------------------------------------
# State event handling
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_handle_state_event_fires_immediately_when_no_sustain(store):
    fired: List[str] = []
    async def reasoner(goal, ctx):
        fired.append(goal)
        return _StubReasonerResult()

    reg = TriggerRegistry(store=store, reasoner_callback=reasoner)
    spec = _state_spec("door", entity_id="binary_sensor.front_door", pattern="on")
    store.save(spec)

    await reg._handle_state_event({
        "data": {
            "entity_id": "binary_sensor.front_door",
            "new_state": {"state": "on"},
        }
    })
    # Give the dispatched task a tick to run.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert fired == ["check binary_sensor.front_door"]


@pytest.mark.asyncio
async def test_handle_state_event_ignores_non_matching_state(store):
    fired: List[str] = []
    async def reasoner(goal, ctx):
        fired.append(goal)
        return _StubReasonerResult()
    reg = TriggerRegistry(store=store, reasoner_callback=reasoner)
    spec = _state_spec("door", entity_id="binary_sensor.front_door", pattern="on")
    store.save(spec)

    await reg._handle_state_event({
        "data": {"entity_id": "binary_sensor.front_door", "new_state": {"state": "off"}}
    })
    await asyncio.sleep(0)
    assert fired == []


@pytest.mark.asyncio
async def test_handle_state_event_with_sustain_then_state_changes_back(store):
    """When sustain is set and the state moves back before the
    debounce expires, the trigger must NOT fire."""
    fired: List[str] = []
    async def reasoner(goal, ctx):
        fired.append(goal)
        return _StubReasonerResult()

    reg = TriggerRegistry(store=store, reasoner_callback=reasoner)
    spec = _state_spec("door", entity_id="binary_sensor.front_door",
                       pattern="on", sustained=60)
    store.save(spec)

    await reg._handle_state_event({
        "data": {"entity_id": "binary_sensor.front_door", "new_state": {"state": "on"}}
    })
    # State moves back to off before the 60s sustain elapses.
    await reg._handle_state_event({
        "data": {"entity_id": "binary_sensor.front_door", "new_state": {"state": "off"}}
    })
    # Wait briefly for any cancellation propagation.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert fired == []


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_start_stop_lifecycle(store):
    async def reasoner(goal, ctx):
        return _StubReasonerResult()
    reg = TriggerRegistry(store=store, reasoner_callback=reasoner,
                          cron_tick_seconds=0.05)
    await reg.start()
    assert reg.running is True
    # Let the cron loop tick once.
    await asyncio.sleep(0.15)
    await reg.stop()
    assert reg.running is False


# ---------------------------------------------------------------------------
# End-to-end cron evaluation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_evaluate_cron_fires_matching_trigger(store):
    fired: List[str] = []
    async def reasoner(goal, ctx):
        fired.append(goal)
        return _StubReasonerResult()
    reg = TriggerRegistry(store=store, reasoner_callback=reasoner)

    # A trigger that matches "every minute".
    spec = _cron_spec("every", cron="* * * * *")
    store.save(spec)
    # Disabled trigger at the same time \u2014 must not fire.
    store.save(_cron_spec("off", cron="* * * * *", enabled=False))

    await reg._evaluate_cron(datetime(2026, 4, 18, 12, 0))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert fired == ["do every"]
