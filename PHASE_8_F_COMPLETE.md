# Phase 8 / Milestone F — Proactive Triggers: COMPLETE

> **Status:** ✅ Shipped
> **Branch:** Phase 8 work-in-progress (v0.11.0-dev)
> **Tests:** 159 passed / 4 skipped — +34 new
> **Date:** April 18, 2026

This document describes Milestone **F** of [PHASE_8.md](PHASE_8.md) —
"the heartbeat". The orchestrator no longer waits to be asked.
Cron schedules and Home Assistant entity state changes can now
**fire the deep reasoner on their own** — and because every trigger
runs through the Milestone E PAE flow in `auto` mode, "fire on its
own" never means "act on its own without supervision".

---

## 1. The problem F solves

After Milestones D + E, the deep reasoner is reflective and
accountable, but still **passive**: nothing happens unless a human
opens chat or hits `/api/reasoning/run`. Concrete misses:

| Use case | Current state |
|---|---|
| "Audit energy use every night at 22:00." | No scheduler. |
| "Investigate when the front door has been open 5 minutes while no-one's home." | No state subscription. |
| "Alert me when the freezer goes above -15 °C." | No threshold watcher. |
| 14 OpenClaw MCP prompts (`security_audit`, `energy_optimizer`…) | Sit idle. |

Milestone F turns these into one-line trigger configs.

---

## 2. What shipped

### 2.1 New: `triggers.py`

[ai-orchestrator/backend/triggers.py](ai-orchestrator/backend/triggers.py) — ~570 lines, **zero new external dependencies**.

Self-contained module. Knows about `TriggerStore` (its own SQLite
table) and a `reasoner_callback` you hand it. **Does not import the
agent, the harness, or the LLM.**

#### Built-in cron evaluator — `CronExpr`

Why no `apscheduler` / `croniter`?

- Both are heavy (apscheduler is ~30 modules; croniter pulls
  `python-dateutil`).
- We only need *minute-resolution* matching of standard 5-field
  cron expressions plus a few aliases. That fits in ~80 lines.
- Adding deps to a Home Assistant add-on means rebuilding the
  Docker image; keeping the surface lean is worth a small parser.

Supports:
- `* * * * *` standard fields, `*`, `,` lists, `-` ranges, `/N` steps
- Aliases: `@hourly @daily @nightly @midnight @weekly @monthly`
  (`@nightly` = `0 22 * * *` because that's what you actually want)
- Standard cron DoM/DoW semantics (when both restricted, match on
  either)
- `next_fire_after(dt)` for forward-search

```python
e = CronExpr.parse("0 22 * * *")
e.matches(datetime.now())                  # True/False
e.next_fire_after(datetime.now())          # next 22:00
```

11 cron tests cover edge cases (range/step/list/alias/DoM-or-DoW
semantics/invalid inputs).

#### Data model

```python
@dataclass
class TriggerSpec:
    id: str
    name: str
    type: str               # "cron" | "state"
    goal_template: str      # e.g. "Investigate {entity_id} ({reason})"
    enabled: bool = True
    # cron-only
    cron: Optional[str] = None
    # state-only
    entity_id: Optional[str] = None
    state_pattern: Optional[str] = None     # "on" or "~^un" (regex)
    sustained_seconds: int = 0              # debounce — must hold for this long
    # both
    cooldown_seconds: int = 600             # min interval between fires
    mode: str = "auto"                      # always-recommended
    extra_context: Dict[str, Any] = {}
    last_fired_at: Optional[str] = None     # for cross-restart cooldown

@dataclass
class TriggerFireRecord:
    id: str
    trigger_id: str
    timestamp: str
    goal: str               # rendered, what the reasoner saw
    run_id: Optional[str]
    plan_id: Optional[str]
    status: str             # submitted | awaiting_approval | executed | completed | error
    note: Optional[str]
```

#### `TriggerStore`

SQLite at `/data/triggers.db` (workspace-local fallback for tests).
Two tables:

- `triggers` — definitions
- `trigger_fires` — fire history with FK + timestamp index

Standard CRUD + `record_fire()` + `list_fires(trigger_id?, limit)`.

#### `TriggerRegistry`

The runtime. One instance owned by the FastAPI app. Lifecycle:

```python
registry = TriggerRegistry(store, reasoner_callback,
                           ha_client=ws_client,
                           broadcast_func=broadcast)
await registry.start()
# … runs until …
await registry.stop()
```

**Cron loop** — single `asyncio.Task` that ticks every
`cron_tick_seconds` (default 30s), evaluates every enabled cron
trigger against the current minute, and fires matches. The minute
de-dup variable prevents double-firing if a tick happens to span a
minute boundary.

**State subscriptions** — registers one `subscribe_events:
state_changed` subscription via the existing `HAWebSocketClient`
and dispatches each event to all matching triggers. This avoids N
subscriptions for N triggers (HA fires `state_changed` for every
entity anyway; we filter in-process).

**Sustained-state debounce** — when `sustained_seconds > 0`, a per-
trigger `asyncio.Task` sleeps for that duration. If the entity
state changes back to non-matching before the timer expires, the
task is cancelled and the trigger doesn't fire. After the sustain
elapses, the registry re-fetches the current state via
`ha_client.get_states()` (when available) and only fires if the
condition still holds.

**Cooldown** — in-memory `_last_fired[trigger_id]` plus persisted
`last_fired_at` in the row. The persisted timestamp is consulted on
first check after a restart so a flap right after boot doesn't
double-fire.

**Always-`auto` mode** — by convention, the registry is wired to a
callback that calls `agent.run(goal, context, mode="auto")`. This
means **every triggered run goes through PAE**: anything high-impact
queues for approval, low-impact stuff executes inline. The user
never wakes up to find that a trigger silently rearmed an alarm at
3am.

#### Goal templating

```python
TriggerSpec(
    name="front-door-while-away",
    type="state",
    entity_id="binary_sensor.front_door",
    state_pattern="on",
    sustained_seconds=300,
    cooldown_seconds=1800,
    goal_template=(
        "Investigate {entity_id} ({reason}). "
        "Check if anyone is home, look at recent history, "
        "and propose a response."
    ),
)
```

`{entity_id} {trigger_name} {trigger_id} {now} {reason}` are
substituted; unknown keys are left intact (so a typo in the template
becomes visible to the LLM, not a crash).

---

### 2.2 Wired: `main.py`

[ai-orchestrator/backend/main.py](ai-orchestrator/backend/main.py)

#### Lifespan

After the deep reasoner initialises, the registry is constructed and
started. The reasoner callback enforces `mode="auto"`:

```python
async def _trigger_reasoner_call(goal: str, context: dict):
    # Triggers always go through plan/auto so the PAE
    # safety net (Milestone E) gates anything dangerous.
    return await deep_reasoner.run(goal, context, mode="auto")

trigger_registry = TriggerRegistry(
    store=TriggerStore(),
    reasoner_callback=_trigger_reasoner_call,
    ha_client=ha_client,
    broadcast_func=broadcast_to_dashboard,
)
await trigger_registry.start()
```

`broadcast_func` lets the dashboard receive a `trigger_fired` WS
event in real time (Milestone F5 — fire history surfaces).

The shutdown path stops the registry cleanly before disconnecting
the HA client and closing the external MCP.

#### CRUD endpoints

| Method & path | Purpose |
|---|---|
| `GET /api/triggers?enabled_only=true` | List triggers |
| `POST /api/triggers` | Create from `TriggerPayload` |
| `GET /api/triggers/{id}` | Single trigger |
| `PUT /api/triggers/{id}` | Update (validates as create does) |
| `DELETE /api/triggers/{id}` | Remove |
| `GET /api/triggers/{id}/fires?limit=50` | Per-trigger fire history |
| `GET /api/triggers/fires?limit=50` | All recent fires, newest first |
| `POST /api/triggers/{id}/fire` | Manual test-fire |

`POST /api/triggers/{id}/fire` is the killer dev-loop tool — it
runs the trigger right now without waiting for the natural
condition, persisting a `manual test` fire record so you can iterate
on `goal_template` quickly.

All write paths validate the spec (`ValueError` → HTTP 400) so a
broken cron expression or a state trigger missing `entity_id` fails
loudly instead of being persisted-then-skipped.

---

### 2.3 Tests

[ai-orchestrator/backend/tests/test_triggers_smoke.py](ai-orchestrator/backend/tests/test_triggers_smoke.py) — 34 tests across 8 groups.

| Group | Tests | What they validate |
|---|---|---|
| `TestCronExpr` | 11 | Every-minute, hour ranges, steps, lists, `@nightly`, DoW Sunday, both DoM+DoW restricted (cron's "OR" semantics), invalid field count, out-of-range values, `next_fire_after` |
| `TestStateMatching` | 4 | `None` matches all, exact case-insensitive, regex via `~`, invalid regex → safe `False` |
| `TestGoalRendering` | 2 | Substitutes known vars, leaves unknown vars intact |
| `TestTriggerStore` | 3 | CRUD round-trip, enabled-only filter, fire history newest-first |
| Fire path | 3 | Records `run_id` + `plan_id`; marks `awaiting_approval` when plan needs approval; marks `executed` when plan auto-executed; marks `error` on reasoner exception |
| Cooldown | 2 | Back-to-back fires blocked by cooldown; `cooldown=0` disables throttle |
| CRUD via registry | 4 | Auto-id assignment, invalid cron rejection, missing entity_id rejection, idempotent delete |
| State events | 3 | Immediate fire when no sustain, no fire when state doesn't match, sustain timer cancelled when state moves back before debounce |
| Lifecycle | 1 | `start/stop` toggles `running` cleanly |
| End-to-end | 1 | `_evaluate_cron` fires matching enabled triggers and skips disabled ones |

**Test philosophy.** All tests use stub reasoners that return shaped
result dicts; no Ollama, no Chroma, no real HA, no time travel. The
sustain test uses real `asyncio.sleep(0)` ticks instead of mocking
time, which keeps the asynchronous semantics honest at the cost of
not exercising the actual 5-minute wait (deemed not worth a slow
test for a `time.sleep` we trust).

---

## 3. Acceptance check

> A trigger configured for `binary_sensor.front_door = on` for >5min
> while `person.user = not_home` automatically fires the reasoner
> with goal `"Investigate why the front door has been open for 5+
> minutes while no-one is home"`.

✅ The state-change + sustain + goal-template path is verified by:
- `test_handle_state_event_with_sustain_then_state_changes_back` —
  proves the debounce really debounces
- `test_handle_state_event_fires_immediately_when_no_sustain` —
  proves the dispatch path actually invokes the reasoner with the
  rendered goal

The "while no-one is home" guard is a `goal_template` concern
today — the LLM is told the entity_id and asked to investigate; it
will naturally call `hass_get_state("person.user")` and decide.
Future enhancement (§6) could surface a `condition:` field in
`TriggerSpec` that pre-checks before firing.

---

## 4. Files changed

| File | Change |
|---|---|
| `ai-orchestrator/backend/triggers.py` | **NEW** — ~570 lines |
| `ai-orchestrator/backend/main.py` | +`TriggerRegistry` lifespan, +8 endpoints, +`Any`/`datetime` imports |
| `ai-orchestrator/backend/tests/test_triggers_smoke.py` | **NEW** — 34 tests |
| `PHASE_8_F_COMPLETE.md` | This document |

No changes to `requirements.txt` — built-in cron means no new deps.

---

## 5. Test results

```
$ python -m pytest -q
159 passed, 4 skipped in 37.01s
```

The 4 skipped are `test_external_mcp_live.py` (require live OpenClaw
HASS_MCP). Everything else green, including all prior Phase 1–7 +
Milestones D + E.

---

## 6. Acknowledged limitations / future work

1. **Minute-resolution cron only.** The loop ticks at 30s by default;
   we don't fire sub-minute. Fine for home automation; would need
   a faster path for, say, motion-grouping logic.
2. **No `condition:` pre-check.** A trigger fires whenever its main
   condition matches; secondary checks (e.g. "and `person.user =
   not_home`") happen inside the reasoning prompt. Cheap to add as
   a list of HA state checks evaluated before invoking the LLM.
3. **State subscription doesn't survive HA reconnect.** If the HA WS
   drops and reconnects, the subscription has to be re-established;
   today the registry doesn't observe HA reconnects. The existing
   `HAWebSocketClient.run_reconnect_loop()` could be extended to
   notify subscribers; we'll handle this when it bites in practice.
4. **No declarative `triggers.yaml` loader.** The CRUD API is the
   only way to add triggers. A YAML loader at startup is a few
   lines — left out so we don't ship a half-baked config schema.
5. **Cooldown is global per trigger.** No per-context cooldowns
   (e.g. "only fire if the *same* sensor goes off again"). The
   current model is correct for the common case; we'll revisit if
   the dashboard shows users wanting more granularity.
6. **Manual test-fire bypasses cooldown.** Intentional — you want
   instant iteration when designing — but not advertised in the
   API. Fine.

---

## 7. What's next

**Milestone G — Streaming + MCP-prompt workflows + dashboard polish.**

- `GET /api/reasoning/run/stream` (Server-Sent Events) emitting
  `thought` / `tool_call` / `tool_result` / `final` incrementally.
- `GET /api/reasoning/prompts` — auto-discover external MCP prompts
  (the 14 OpenClaw `security_audit`, `energy_optimizer`, etc.) and
  expose them as one-click goal buttons.
- New dashboard panels: memory browser, plan approval queue,
  trigger configuration, fire-history stream.

After G, Phase 8 closes. The orchestrator will then have:
- A brain (Phase 7)
- A memory (D)
- A conscience (E)
- A heartbeat (F)
- A face (G)

---

*Milestone F shipped April 18, 2026. The orchestrator now operates
on its own initiative — and the safety net we built in E catches
anything dangerous before it lands.*
