# DM2-8 — get_session_recap MCP tool

Ticket: [DM2-8](https://linear.app/dm21/issue/DM2-8/add-get-session-recap-mcp-tool)
Size: Standard. Branch: `sta/dm2-8-session-recap-tool` (base: `sta/dm2-7-knowledge-write-tools`).

## Problem

On resume the DM sees only a 100-char session summary and a one-paragraph
game-state note. `SessionRecapGenerator`
(`claudmaster/continuity/recap_generator.py`) can assemble a full recap but is
never instantiated in production. One call should give the resuming DM
everything: previously-on narrative, key events, active quests, unresolved
threads, NPC reminders, suggested hooks — plus the verbatim journal events so
established details can't be contradicted (generator output is summaries; the
DM needs exact established detail).

## Decisions (user-pinned)

1. **`session_number=None` resolves to the latest session with recorded
   journal data**: max `session_number` over journal events (events with
   `session_number=None` don't count), falling back to
   `game_state.current_session` (via `_current_session_number()`) when the
   journal has no session-attributed events.
2. **Verbatim AdventureEvents merge inside the recap object, not appended by
   the tool.** `SessionRecap` gains a `verbatim_events` field and
   `SessionRecapGenerator.generate_recap` accepts the session's events as an
   injected parameter. The TOOL fetches them from storage and passes them in —
   the generator does not import storage; dependency injection keeps the
   claudmaster layer storage-free while the dataclass remains the single
   assembly point. (User explicitly overrode the tool-side append
   alternative.)
3. **All events for the session qualify for the verbatim section**, sorted
   importance descending then timestamp ascending, with full untruncated
   descriptions. No top-N cap, no importance floor.
4. **E2E regression test seeds at the storage layer** (`storage.add_event`,
   no dual-write — fact graph empty at seed time), runs `sync_facts` (real
   backfill), then asserts the recap surfaces both seeded facts ("visited the
   church", "met Donavich"). This encodes the bug we lived through.

Carried-forward skeleton (uncontested, from the DM2-5/DM2-7 stack):

- `get_session_recap(session_number=None, length="standard",
  style="narrative")` as `@mcp.tool` in `main.py`, placed after `sync_facts`
  (the fact-graph tool region), following DM2-7 conventions: campaign guard →
  `storage.fact_db` / `storage.npc_knowledge_tracker` accessor guard →
  validate `length`/`style` against the generator's accepted values, listing
  valid ones on error.
- `SessionRecapGenerator` instantiated per call wired with the cached
  accessors (`storage.fact_db`, `storage.npc_knowledge_tracker`); `timeline`
  stays `None`.
- Markdown-rendered output sections: previously-on, key events, active
  quests, unresolved threads, current situation, party status, NPC reminders,
  suggested hooks — plus the verbatim events (pin 2).
- E2E test in `tests/test_session_recap_tool.py` using the `.fn` +
  storage-swap fixture pattern from `tests/test_knowledge_write_tools.py`.

## Design

### Generator changes (`claudmaster/continuity/recap_generator.py`)

Accepted values become module-level constants (single source of truth for the
tool's validation):

```python
RECAP_LENGTHS = ("brief", "standard", "detailed")
RECAP_STYLES = ("narrative", "bullet", "mixed")
```

`SessionRecap` gains:

```python
verbatim_events: list[AdventureEvent] = field(default_factory=list)
```

`AdventureEvent` is imported under `TYPE_CHECKING` only (the module already
has `from __future__ import annotations`; runtime access is duck-typed
attribute reads), so claudmaster takes no runtime dependency on core models.

`generate_recap` gains an injected parameter:

```python
def generate_recap(
    self,
    session_number: int,
    length: str = "standard",
    style: str = "narrative",
    events: list[AdventureEvent] | None = None,
) -> SessionRecap
```

The generator sorts the injected events by (importance desc, timestamp asc)
and stores them on `SessionRecap.verbatim_events` — assembly happens in the
generator/dataclass (pin 2), not in the tool. `events=None` (existing
callers/tests) yields an empty list; behaviour is otherwise unchanged.

### `get_session_recap` (main.py)

```python
@mcp.tool
def get_session_recap(
    session_number: int | None = None,  # ge=1; None → latest session with journal data (pin 1)
    length: str = "standard",           # brief / standard / detailed
    style: str = "narrative",           # narrative / bullet / mixed
) -> str
```

Flow:

1. Campaign guard → "No active campaign…" (existing message).
2. `storage.fact_db` / `storage.npc_knowledge_tracker` None → established
   "fact graph could not be loaded (split-format campaigns only)" message.
3. Validate `length` against `RECAP_LENGTHS` and `style` against
   `RECAP_STYLES` — `party_knowledge`-style "Invalid X. Valid: …" errors.
   (The generator silently coerces unknown values; the tool rejects them.)
4. Resolve the session (pin 1): explicit `session_number` if given, else
   `max(e.session_number for e in storage.get_events() if e.session_number)`,
   else `_current_session_number()`.
5. Fetch the session's journal events (pin 3): all of
   `storage.get_events()` with `session_number == resolved session`.
6. `SessionRecapGenerator(fact_db, npc_tracker, timeline=None)` per call with
   the cached accessors; `generate_recap(session, length, style,
   events=session_events)`.
7. Render the returned `SessionRecap` as markdown:
   - `# Session Recap — Session N` header.
   - Always-present sections: **Previously On** (`previously_on`),
     **Current Situation**, **Party Status** (the generator always returns
     content for these).
   - List sections rendered only when non-empty: **Key Events** (bullets),
     **Active Quests** (name/status/objectives/progress), **Unresolved
     Threads**, **NPC Reminders**, **Suggested Hooks** (bullets).
   - **Verbatim Journal Events** section always rendered (pin 3 order, full
     descriptions; title, type, importance, location, characters per event).
     Empty session → explicit "no journal events recorded for this session"
     line — absence is stated, not silent, since this section exists to stop
     contradictions of established detail.

Imports: `SessionRecapGenerator` (and the constants) are function-local
(lazy-claudmaster convention, same as `Fact`/`FactIngest` in the sibling
tools).

The tool is read-only: no writes, no `save()`.

## Acceptance criteria → design trace

- `get_session_recap(session_number=None→latest, length, style)` returns the
  assembled `SessionRecap` content, wired with the per-campaign fact DB and
  NPC tracker → flow steps 4 (pin 1 resolution), 6 (cached accessors), 7.
- Output includes verbatim `AdventureEvent`s for the session → pins 2+3:
  `verbatim_events` on the dataclass, injected by the tool, all events
  importance-desc with full descriptions.
- E2E regression test: seeded journal → `sync_facts` → recap surfaces both
  facts → pin 4, Testing below.

## Out of scope

- TimelineTracker wiring (stays `None`).
- Caching the generator on storage (cheap per-call object over two cached
  accessors).
- Monolithic-format campaigns (fact graph is split-format only).
- Truncation/length control of the verbatim section (pin 3: everything,
  untruncated).

## Testing

Additions to `tests/claudmaster/test_recap_generator.py` (assembly-point
behaviour at the generator level):

- `generate_recap` without `events` → `verbatim_events == []`.
- Injected events land on `verbatim_events` sorted importance desc,
  timestamp asc.

New `tests/test_session_recap_tool.py` following the
`tests/test_knowledge_write_tools.py` conventions (tmp-path storage fixture +
module-storage swap, tools exercised via `.fn`):

- **E2E regression (pin 4)**: `storage.add_event` two session-1 events
  ("visited the church", "met Donavich"); assert fact graph empty at seed
  time; `m.sync_facts.fn()`; `m.get_session_recap.fn()` surfaces both facts.
- Latest-session resolution: events in sessions 1 and 3 → recap targets
  session 3; verbatim section contains only session-3 events.
- Fallback: empty journal + `current_session=2` → recap targets session 2.
- Events without `session_number` don't drive resolution.
- Explicit `session_number` is honoured.
- Verbatim ordering: importance desc, timestamp asc tiebreak.
- Verbatim descriptions are full/untruncated (long description appears
  verbatim).
- Invalid `length` / invalid `style` errors list the valid values.
- Campaign guard; fact-graph-unavailable guard.
