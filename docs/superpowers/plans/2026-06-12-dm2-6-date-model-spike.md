# DM2-6 Date-Model Spike Recommendation Doc Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Write the committed recommendation document `docs/spike-dm2-6-date-model.md` answering DM2-6 (chosen in-game date model + migration sketch).

**Architecture:** Doc-only spike. The recommendation follows the approved spec at `docs/superpowers/specs/2026-06-12-dm2-6-date-model-design.md`: hybrid model — freeform `current_date_in_game` stays the narration/display authority; structured `GameTime` owned by `TimelineTracker` becomes the date-math authority; migration via one-time LLM-assisted anchoring at resume (Continuity Graph v1 self-heal precedent), no retroactive event stamps.

**Tech Stack:** Markdown only. No product code, no tests.

---

### Task 1: Write the recommendation document

**Files:**
- Create: `docs/spike-dm2-6-date-model.md`

- [ ] **Step 1: Write the document**

Write `docs/spike-dm2-6-date-model.md` with these sections, in this order, carrying the content decided in the spec (do not thin it out):

1. **Header block** — title `# Spike: In-game date model for GameTime compatibility (DM2-6)`, Linear link `https://linear.app/dm21/issue/DM2-6/investigate-in-game-date-model-for-gametime-compatibility`, date 2026-06-12, status "Recommendation", consumer "DM2-11 (Wire TimelineTracker into the play loop)".
2. **Current state** — `GameState.current_date_in_game: str | None` (`src/dm20_protocol/models.py:505`), written only by the LLM DM through `update_game_state` (`src/dm20_protocol/main.py:1694`), displayed verbatim in `get_game_state` and the `/dm:save` summary; real values: `"Day 2, early morning"` (`example/dnd/shadowfen-campaign.json`), ticket example `"Dawn — first morning in Barovia"`. `GameTime` (`src/dm20_protocol/claudmaster/consistency/timeline.py:37`): 12×30 simplified calendar, default year 1492, minute-based comparisons; `TimelineTracker` persists `current_time` + events in per-campaign `timeline.json`; zero production writers; `location_state.py` also consumes `GameTime`.
3. **Candidates considered** — the ticket's three models with trade-offs and verdicts: structured-only (rejected: breaking, loses narrative flavor, lossy up-front conversion), hybrid (chosen), parse-on-write (rejected: fantasy prose has no absolute anchor; deterministic parsing impossible without an LLM; the caller already is the LLM; silent mis-parses poison temporal validation).
4. **Recommendation: hybrid** — authority split (GameTime canonical for math, prose canonical for narration; engine never parses prose, LLM never does date math); clock ownership (`TimelineTracker.current_time` in `timeline.json`; explicitly NO new `game_time` field on `GameState`, to avoid a second persisted copy and its drift surface; tracker held on `DnDStorage` per campaign mirroring the fact-graph/DiscoveryTracker lifecycle with degrade-to-`None`).
5. **Tool-surface sketch for DM2-11** (informational, not built here) — structured time params on `update_game_state` and/or an `advance_time(amount, unit)` tool mapping to `TimelineTracker.advance_time`; auto-derived display from `GameTime.to_string()` + `TIME_OF_DAY` when prose omitted; prose-only updates keep working but the tool response notes the clock did not advance.
6. **Anchor convention** — epoch `GameTime()` defaults (Y1492 M1 D1 08:00) ≙ campaign "Day 1"; `Day N` prose maps to `day = N` offset.
7. **Migration sketch** — four numbered points from the spec: no schema migration (`TimelineTracker.load()` tolerates absence); one-time idempotent LLM anchoring at `/dm:start` when the timeline is fresh (default `current_time`, zero events) but a prose date exists; no retroactive `GameTime` stamps on historical journal events (no reliable source; false precision corrupts temporal-order validation); legacy monolithic campaigns degrade exactly like the fact graph.
8. **Known limitations / out of scope** — 12×30 calendar stays (`_calendar` config exists but `GameTime` hardcodes the math; setting flavor like Harptos lives in prose); one clock per campaign (per-hero clocks stay external, e.g. the shared-world-gm overlay); `GameTime.round` carried but outside day-math.
9. **Acceptance hooks for DM2-11** — anchoring idempotence, prose-only nudge in tool response, `Day N` offset mapping.

- [ ] **Step 2: Verify every code anchor in the doc**

Run:
```bash
cd /home/sta-aurocon/source/repos/dm20-protocol/worktrees/sta-dm2-6-date-model-spike
sed -n '505p' src/dm20_protocol/models.py        # expect: current_date_in_game: str | None = None
sed -n '1694p' src/dm20_protocol/main.py          # expect: current_date_in_game: Annotated[...]
sed -n '37p' src/dm20_protocol/claudmaster/consistency/timeline.py  # expect: class GameTime(BaseModel):
grep -n "Day 2, early morning" example/dnd/shadowfen-campaign.json  # expect: one hit
```
Expected: each line matches the doc's claims. Fix the doc if any anchor drifted.

- [ ] **Step 3: Commit**

```bash
git add docs/spike-dm2-6-date-model.md
git commit -m "docs(DM2-6): date-model spike recommendation — hybrid prose display + TimelineTracker GameTime

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
