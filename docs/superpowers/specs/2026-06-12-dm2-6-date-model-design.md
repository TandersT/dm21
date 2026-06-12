# DM2-6 — In-game date model for GameTime compatibility (spike design)

**Ticket:** [DM2-6](https://linear.app/dm21/issue/DM2-6/investigate-in-game-date-model-for-gametime-compatibility)
**Type:** Research spike (timebox 1 day). Deliverable is a recommendation document, not product code.
**Blocks:** DM2-11 (Wire TimelineTracker into the play loop).

## Problem

`GameState.current_date_in_game` (`src/dm20_protocol/models.py:505`) is a freeform prose
string written by the LLM DM via `update_game_state` (`src/dm20_protocol/main.py:1694`),
e.g. `"Day 2, early morning"` or `"Dawn — first morning in Barovia"`. `TimelineTracker`
(`src/dm20_protocol/claudmaster/consistency/timeline.py`) needs structured `GameTime`
values for temporal-order validation and events-at/between queries. Prose dates cannot
feed date math, so the timeline cannot be activated.

## Decision to make

Choose one of three candidate models (from the ticket) and sketch the migration for
existing campaigns' freeform dates.

## Candidates evaluated

1. **Structured calendar only** — replace the prose field with a structured `GameTime`.
   Rejected: breaking schema/tool/prompt change; loses narrative flavor the prose
   carries; forces lossy up-front conversion of existing saves.
2. **Hybrid: freeform display + structured clock** — keep prose for narration, make
   `GameTime` the math authority. **Chosen.**
3. **Parse-on-write** — server parses prose into `GameTime` on every write. Rejected:
   fantasy prose has no absolute anchor and cannot be parsed deterministically without
   an LLM; the caller already *is* the LLM, so server-side NLP inverts the architecture
   and silent mis-parses would poison temporal validation.

## Chosen design (hybrid)

### Authority split

- `GameTime` is canonical for all date math (ordering, events-between, travel time).
- `current_date_in_game` is canonical for narration/display only. Engine code never
  parses it; the LLM never does date math.

### Where the structured clock lives

`TimelineTracker.current_time`, persisted in the campaign's `timeline.json` — the
persistence that already exists. **No new field on `GameState`.** A `game_time` mirror
on `GameState` would create a second persisted copy (`game_state.json` +
`timeline.json`) and a permanent sync-bug surface. DM2-11 holds the tracker on
`DnDStorage` per campaign, mirroring the fact-graph/DiscoveryTracker lifecycle
(loaded on campaign load/switch, cleared on close, degrade-to-`None` on failure).

### Tool surface (sketch for DM2-11 — not built in this spike)

- `update_game_state` gains optional structured time parameters (set semantics), and/or
  a new `advance_time(amount, unit)` tool maps to `TimelineTracker.advance_time`.
- When structured time changes and no prose is supplied, derive a serviceable display
  from `GameTime.to_string()` + the `TIME_OF_DAY` table so the prose never silently
  goes stale; an explicit prose argument always overrides.
- Prose-only updates (today's behavior) keep working, but the tool response states that
  the timeline clock did not advance, so the LLM sees the gap.

### Anchor convention

Campaign epoch = `GameTime()` defaults (year 1492, month 1, day 1, 08:00) ≙ "Day 1" of
the campaign. A prose date matching the common `Day N` pattern maps to `day = N` offset
from the epoch. The year stays at the Forgotten Realms default unless the DM sets one.

### Migration sketch (existing campaigns)

Follows the Continuity Graph v1 self-heal precedent (`sync_facts` conditional on
resume; see `docs/project-continuity-graph-v1.md`):

1. **No schema migration.** `timeline.json` simply doesn't exist yet for old campaigns;
   `TimelineTracker.load()` already tolerates absence and starts fresh.
2. **One-time anchoring at resume.** In `/dm:start`, when the timeline is fresh
   (default `current_time`, zero events) but `current_date_in_game` is set, the DM
   (LLM) anchors the clock: `Day N` prose auto-offsets from the epoch; anything else
   the DM estimates from session notes or asks the player, then writes via the time
   tool. Idempotent: once the timeline is non-fresh, the step is skipped.
3. **No retroactive event stamps.** Historical journal events get no backfilled
   `GameTime` — there is no reliable source, and false precision corrupts
   temporal-order validation. Timeline coverage starts at the anchor point;
   pre-anchor history degrades gracefully (same posture as legacy-format campaigns
   degrade for the fact graph).
4. **Legacy monolithic campaigns** degrade exactly as the fact graph does: timeline
   available for split-format campaigns; legacy reports unavailable.

### Known limitations (explicitly out of scope)

- The simplified 12×30 calendar stays; `TimelineTracker._calendar` config exists but
  `GameTime` hardcodes the math. Setting-specific calendars (e.g. Harptos festival
  days) live in the prose layer.
- One clock per campaign. Multi-hero/multi-thread play (the shared-world-gm overlay's
  per-hero clocks) stays external to dm20.
- `GameTime.round` is carried but not part of the day-math path.

## Deliverable

A recommendation document at `docs/spike-dm2-6-date-model.md` covering: current state,
the three candidates with trade-offs, the chosen hybrid model (authority split, clock
ownership, tool-surface sketch, anchor convention), the migration sketch, and known
limitations — written so DM2-11 can consume it directly.

## Testing

No product code ships in this spike, so no new tests. The recommendation doc gives
DM2-11 concrete acceptance hooks (anchoring idempotence, prose-only nudge, `Day N`
offset mapping).
