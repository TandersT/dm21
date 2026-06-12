# DM2-11: Wire TimelineTracker into the play loop — design

**Linear:** https://linear.app/dm21/issue/DM2-11/wire-timelinetracker-into-the-play-loop
**Date:** 2026-06-12
**Upstream contract:** `docs/spike-dm2-6-date-model.md` (hybrid date model — pinned, not re-litigated here)

## Goal

Activate the dormant `TimelineTracker` (`claudmaster/consistency/timeline.py`) so the
play loop produces a temporal record: journal writes stamp `TimelineEvent`s with
engine-side `GameTime`, the DM can move and query the clock, and temporal-order
validation reaches the DM flow.

## Pinned by the spike (upstream decisions, restated for reference)

- Hybrid model: `current_date_in_game` prose stays the display authority; the clock of
  record is `TimelineTracker.current_time` in `timeline.json`. No new `GameState` field.
- Journal writes stamp with the tracker's `current_time` *at write time*, engine-side;
  the LLM never supplies a per-event `GameTime`. `real_session` comes from the journal
  event / game state.
- Campaign epoch = `GameTime()` defaults (year 1492, month 1, day 1, 08:00); "Day N" ≙
  epoch advanced by N−1 days.
- "Unanchored" is an explicit persisted marker; anchoring runs before any timeline
  writes in the resume flow; anchoring is idempotent; no retroactive event stamps.
- Prose-only `update_game_state` calls keep working but the response notes the clock
  did not advance.
- Legacy (monolithic) campaigns degrade to "timeline unavailable", like the fact graph.

## Design decisions made here (the choice points the spike left open)

1. **Tool surface: dedicated time tools** (not extra params on `update_game_state`).
   Explicit set/advance semantics the prompts can name; `update_game_state` keeps its
   shape and gains only the nudge.
2. **All calendar math is engine-side.** `set_game_time` takes campaign-relative
   `day` (Day N), so the LLM never converts Day 45 → month 2 day 15.
3. **One query tool** (`get_timeline`) with day-granularity at/between semantics,
   doubling as the clock/anchor-status readout for the resume flow.
4. **Validation is automatic on stamping**: `add_event` runs `validate_temporal_order`
   and surfaces conflicts in its response — no separate check tool to forget to call.
5. **Stamping is skipped while unanchored** (response says so) — the engine-side
   enforcement of the spike's ordering rule.
6. **New split campaigns are born anchored at epoch** (a fresh campaign's Day 1 is a
   genuine anchor); legacy campaigns load unanchored until the resume anchoring step.
7. **Stamping hook lives at the tool layer** in `add_event`, mirroring
   `_ingest_to_fact_graph` (v1 precedent: storage-layer hooks would flood from bulk
   module imports; `main.py:add_event` is the only `storage.add_event` caller).

## Components

### 1. `claudmaster/consistency/timeline.py`

- `TimelineTracker` gains a persisted `anchored: bool` (default `False`; absent file or
  absent key → `False`). Exposed as a read/write `anchored` property; written by
  `save()` / read by `load()`. Callers (storage on create, the set tool) set it
  explicitly — `set_time` itself stays semantics-free.
- Module-level day-relative formatter:
  - `time_of_day(hour: int) -> str` — extracted lookup over `TIME_OF_DAY` (reused by
    `TimelineTracker.get_time_of_day`).
  - `format_day_relative(time: GameTime, epoch: GameTime | None = None) -> str` —
    `"Day N, <time of day> (HH:MM)"`, N = whole days since epoch + 1.
  - `day_number_to_game_time(day: int, hour: int, minute: int) -> GameTime` — epoch
    advanced by (day−1) days, then hour/minute applied. Inverse of the formatter's day
    math; rolls into months/years via existing `GameTime.advance`.

### 2. `storage.py` — lifecycle (mirrors DiscoveryTracker / fact graph exactly)

- `_timeline_tracker: TimelineTracker | None` field; `timeline_tracker` property.
- `_load_timeline_tracker()` — split campaigns only; degrade to `None` on failure.
- Called from `load_campaign` and `create_campaign`; on create, mark anchored and save
  (born anchored at epoch). Cleared in `delete_campaign`'s active-campaign reset.

### 3. `main.py` — MCP tools

- `_stamp_timeline(event: AdventureEvent) -> str | None` helper, mirroring
  `_ingest_to_fact_graph`: best-effort, failures logged and swallowed. When the tracker
  is `None` → no-op (`None`). When unanchored → no stamp, returns the "clock unanchored"
  note. Otherwise builds `TimelineEvent(id=f"tl_{event.id}", game_time=current_time,
  real_session=event.session_number or current session, description, location,
  characters_involved, fact_ids=[f"evt_{event.id}"])`, runs `validate_temporal_order`
  (conflict → warning string appended; event still stamped — the journal write already
  happened, the timeline mirrors it), `add_event` + `save`. Returns a short suffix for
  the tool response (stamp info / warning).
- `add_event` appends the helper's suffix to its response.
- `set_game_time(day, hour=8, minute=0, date_display=None)` — set semantics; maps via
  `day_number_to_game_time`; `tracker.set_time` + anchored ← True + save; writes
  `current_date_in_game` ← `date_display` or `format_day_relative(...)` via
  `storage.update_game_state`; returns new clock. Idempotent for repeated same-args calls.
- `advance_game_time(amount, unit, date_display=None)` — refuses with guidance when
  unanchored or tracker `None`; otherwise `tracker.advance_time` + save + display
  derivation as above; returns old → new clock.
- `get_timeline(from_day=None, to_day=None, limit=10)` — header: current clock
  (day-relative), anchored status, event count. No range → most recent `limit` events.
  Range → events in [from_day 00:00, to_day 23:59] (`to_day` defaults to `from_day`,
  giving "events at day N"); events rendered with day-relative stamp, description,
  location, characters.
- `update_game_state` — when `current_date_in_game` is supplied and the tracker exists,
  append the nudge: prose updated but the timeline clock did not advance; point at
  `advance_game_time` / `set_game_time`.
- `get_game_state` — append a `Timeline Clock` line (day-relative + anchored status)
  when the tracker exists, so the resume flow reads anchor state without extra calls.

### 4. Prompts

- `.claude/commands/dm/start.md` — resume flow gains an anchoring step (before any
  journal writes): if the clock shows unanchored and `current_date_in_game` is set,
  derive Day N (Day-N prose maps directly; otherwise estimate from session notes or ask
  the player) and call `set_game_time`; if no date either, `set_game_time(day=1)`. Skip
  when already anchored. New tools added to `allowed-tools`.
- `.claude/dm-persona.md` — PERSIST step gains `advance_game_time` (rests, travel,
  scene transitions move the clock; prose date stays narrative) and a note to heed
  temporal-conflict warnings from `add_event`.

## Error handling

- Tracker `None` (legacy/monolithic, load failure): stamping no-ops; time/query tools
  return "timeline unavailable" guidance; nudge suppressed. Never breaks primary writes.
- All stamping failures are best-effort logged warnings (fact-graph precedent).
- `validate_temporal_order` conflicts warn, never block.

## Testing

- `tests/claudmaster/test_timeline.py` (extend): anchored persistence round-trip,
  default-false on absent file/key, `format_day_relative` (epoch → "Day 1, morning";
  dawn next day → "Day 2, dawn"), `day_number_to_game_time` Day 45 → month 2 day 15.
- `tests/test_timeline_lifecycle.py` (mirrors `test_fact_graph_lifecycle.py`): create →
  tracker present + anchored; load round-trip; legacy → `None`; delete clears.
- `tests/test_timeline_wiring.py` (mirrors `test_fact_dual_write.py`, `.fn` +
  storage-swap pattern), covering the spike's acceptance hooks:
  - journal stamping: `add_event` → `TimelineEvent` with tracker `current_time`,
    `real_session`, `fact_ids=["evt_<id>"]` (AC1)
  - unanchored: no stamp + response note (ordering rule)
  - `set_game_time(day=2)` → epoch+1 day; marks anchored; derived display written;
    explicit `date_display` overrides (Day-N hook)
  - anchoring idempotence: same-args `set_game_time` twice → `current_time` unchanged
  - `advance_game_time`: advances + display; refuses unanchored
  - prose-only `update_game_state` → nudge (hook 3)
  - `get_timeline` range/at queries return stamped events (AC2)
  - temporal conflict (same character, two locations, same time) → warning in
    `add_event` response (AC3)
  - tracker `None` → all tools degrade with guidance

## Out of scope

- Recap integration (`get_session_recap` keeps `timeline=None`) — not in this ticket's AC.
- Retroactive stamping/backfill — explicitly excluded by the spike.
- Calendar configurability, travel-time tools, location_state wiring.
