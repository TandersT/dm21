# Spike: In-game date model for GameTime compatibility (DM2-6)

**Linear:** https://linear.app/dm21/issue/DM2-6/investigate-in-game-date-model-for-gametime-compatibility
**Date:** 2026-06-12
**Status:** Recommendation
**Consumer:** DM2-11 (Wire TimelineTracker into the play loop)

## Current state

`GameState.current_date_in_game: str | None` (`src/dm20_protocol/models.py:505`) holds a
freeform prose date. It is written only by the LLM DM through the `update_game_state`
MCP tool (`src/dm20_protocol/main.py:1694`) and displayed verbatim by `get_game_state`
and the `/dm:save` session summary. Real values look like:

- `"Day 2, early morning"` — `example/dnd/shadowfen-campaign.json`
- `"Dawn — first morning in Barovia"` — the ticket's example

On the structured side, `GameTime`
(`src/dm20_protocol/claudmaster/consistency/timeline.py:37`) is a Pydantic model over a
simplified calendar — 12 months × 30 days, default year 1492 (Forgotten Realms), hour /
minute / combat-round fields — with ordering and arithmetic done in total minutes.
`TimelineTracker` persists `current_time` plus a chronological event list in a
per-campaign `timeline.json`, and offers `advance_time`, `get_events_at/between`, and
`validate_temporal_order`. It has **zero production writers** today (the reason for
DM2-11), and `location_state.py` also consumes `GameTime` for visit tracking and state
changes.

The two representations never meet: prose can't feed date math, so the timeline can't
be activated while dates are prose-only.

## Candidates considered

### 1. Structured calendar only — rejected

Replace `current_date_in_game` with a structured `GameTime` field; render display text
from the structure.

- Breaking change to the `GameState` schema, the `update_game_state` tool contract, and
  the DM prompts in one move.
- Loses narrative flavor: "Dawn — first morning in Barovia" carries campaign-arc
  meaning no struct holds, while `GameTime.to_string()` renders generic
  "Year 1492, Month 1, Day 2, 05:30".
- Forces a lossy up-front conversion of every existing campaign's saved prose date.

### 2. Hybrid: freeform display + structured clock — **chosen**

Keep `current_date_in_game` as the narration/display authority; make `GameTime` the
date-math authority. Detailed below. Note one deliberate deviation from the ticket's
literal candidate name ("structured **field**"): the structured value lives in
`TimelineTracker.current_time`, not in a new field on `GameState` — same hybrid family,
relocated to avoid a second persisted copy (rationale below).

### 3. Parse-on-write — rejected

Keep the single freeform field and have the server parse prose into `GameTime` on every
write.

- Fantasy prose has no absolute anchor: "Dawn — first morning in Barovia" contains no
  year, month, or day to parse. Deterministic parsing is impossible without an LLM.
- The caller already *is* an LLM. Putting NLP inside the engine inverts the
  architecture: dm20 is the deterministic state engine; the LLM layer does language.
- Silent mis-parses are worse than no data — wrong `GameTime` stamps poison
  `validate_temporal_order` and events-between queries with confidently wrong answers.

## Recommendation: hybrid model

### Authority split

| Concern | Authority | Never does |
|---|---|---|
| Date math (ordering, events-between, travel time) | `GameTime` / `TimelineTracker` | — |
| Narration & display | `current_date_in_game` (prose) | — |
| Engine | reads/writes `GameTime` only | parses prose |
| LLM DM | writes prose, calls time tools | date arithmetic |

### Where the structured clock lives

`TimelineTracker.current_time`, persisted in the campaign's existing `timeline.json`.
**No new `game_time` field on `GameState`.** A mirror field would create a second
persisted copy (`game_state.json` + `timeline.json`) and a permanent sync-bug surface;
one authority is cheaper than a sync discipline. DM2-11 should hold the
`TimelineTracker` on `DnDStorage` per campaign, mirroring the fact-graph /
DiscoveryTracker lifecycle from Continuity Graph v1 (loaded on campaign load/switch,
cleared on close, degrading to `None` on failure — see
`docs/project-continuity-graph-v1.md`).

### Tool-surface sketch for DM2-11 (informational — not built in this spike)

- **Journal-write stamping rule (the decision DM2-11 defers to this spike):** journal
  writes stamp their `TimelineEvent` with `TimelineTracker.current_time` *at the moment
  of the write*, engine-side — the LLM never supplies a per-event `GameTime`.
  `TimelineEvent.real_session` comes from `game_state.current_session`.
- `update_game_state` gains optional structured time parameters (set semantics), and/or
  a new `advance_time(amount, unit)` tool maps to `TimelineTracker.advance_time`.
- When structured time changes and no prose is supplied, derive a serviceable display
  from the `GameTime` value plus the `TIME_OF_DAY` table (e.g. "Day 2, dawn") so the
  prose never silently goes stale. The existing `to_string()` formats all include
  year/month, so DM2-11 needs a small day-relative formatter (days since epoch + time
  of day). An explicit prose argument always overrides the derived text.
- Prose-only updates (today's behavior) keep working, but the tool response states that
  the timeline clock did not advance — the LLM sees the gap instead of assuming the
  engine inferred time from prose.

### Anchor convention

- Campaign epoch = `GameTime()` defaults: year 1492, month 1, day 1, 08:00.
- Epoch ≙ the campaign's "Day 1"; prose matching the common `Day N` pattern maps to
  *the epoch advanced by (N−1) days* — not `day = N`, which would fail `GameTime`'s
  `day ≤ 30` validation for month-plus campaigns (Day 45 → month 2, day 15).
- The year stays at the Forgotten Realms default unless the DM explicitly sets one;
  it only matters for relative math, not lore accuracy.

## Migration sketch (existing campaigns)

Follows the Continuity Graph v1 self-heal precedent (conditional `sync_facts` on
resume):

1. **No schema migration.** Old campaigns simply have no `timeline.json` yet;
   `TimelineTracker.load()` already tolerates absence and starts fresh.
2. **One-time anchoring at resume.** In `/dm:start`, when the timeline is unanchored
   but `current_date_in_game` is set, the DM (LLM) anchors the clock: `Day N` prose
   auto-offsets from the epoch; anything else the DM estimates from session notes or
   asks the player, then writes via the time tool. Two ordering rules DM2-11 must
   enforce: **anchoring runs before any timeline writes in the resume flow** (an event
   stamped pre-anchor carries a wrong epoch time that anchoring can't repair), and
   **"unanchored" is an explicit persisted marker** (e.g. an `anchored` flag in
   `timeline.json`, or the file's absence) — not "default time + zero events", which an
   early stamped write or a genuine Day-1 anchor would falsely flip. Idempotent: once
   anchored, the step is skipped.
3. **No retroactive event stamps.** Historical journal events get no backfilled
   `GameTime` — there is no reliable source, and false precision corrupts
   temporal-order validation. Timeline coverage starts at the anchor point; pre-anchor
   history degrades gracefully.
4. **Legacy monolithic campaigns** degrade exactly as the fact graph does: the timeline
   is available for split-format campaigns; legacy-format campaigns report it
   unavailable.

## Known limitations (out of scope here)

- The simplified 12×30 calendar stays. `TimelineTracker._calendar` config exists but
  `GameTime` hardcodes the 12×30 math; setting-specific calendars (Harptos festival
  days, etc.) live in the prose layer.
- One clock per campaign. Multi-hero / multi-thread play keeps per-hero clocks external
  to dm20 (e.g. the shared-world-gm overlay).
- `GameTime.round` is carried but sits outside the day-math path.

## Acceptance hooks for DM2-11

- **Journal stamping** — a journal write produces a `TimelineEvent` carrying the
  tracker's `current_time` at write time (engine-testable).
- **Anchoring idempotence** — running the resume anchoring twice leaves
  `current_time` unchanged (engine-testable for the marker/tool path; the trigger
  itself is prompt-level).
- **Prose-only nudge** — a prose-only `update_game_state` call returns a response
  noting the timeline clock did not advance (engine-testable).
- **`Day N` mapping** — `"Day 2, early morning"` anchors to epoch advanced by 1 day
  (`day = 2`), with the time-of-day component left to the DM's structured input rather
  than parsed (the mapping convention is prompt-level; the resulting write is
  engine-testable).
