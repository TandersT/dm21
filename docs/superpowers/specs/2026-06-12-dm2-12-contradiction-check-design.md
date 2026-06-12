# DM2-12 — Expose contradiction detection as a DM-facing check

**Ticket:** [DM2-12](https://linear.app/dm21/issue/DM2-12/expose-contradiction-detection-as-a-dm-facing-check)
**Date:** 2026-06-12
**Base:** sta/dm2-11-timeline-tracker-wiring (fact graph v1 + timeline wiring present)

## Problem

`claudmaster/consistency/contradiction.py` (ContradictionDetector: keyword-heuristic
detection, severity classification, resolution suggestions, `contradictions.json`
persistence) is fully implemented but has zero production callers. The DM cannot ask
"does this statement conflict with established facts?" before narrating.

## Acceptance criteria

1. A check tool accepts a proposed statement and returns conflicts against the fact
   graph with severity + resolution suggestions.
2. Usable in the action loop pre-narration: fast, no side effects.
3. Detected contradictions can be persisted with their chosen resolution
   (retcon / explain / ignore / flag).

## Approach (chosen)

**Storage-held detector + in-memory pending buffer + two MCP tools.**

- The detector joins the fact-graph lifecycle in `DnDStorage._load_fact_graph`,
  **sharing the same `FactDatabase` instance** as the other graph views. This is
  load-bearing: a separately-constructed FactDatabase would hold a stale snapshot and
  never see facts dual-written during the session.
- `check_consistency` runs detection in a new non-registering mode: hits are returned
  and parked in an in-memory `pending` dict on the detector, never written to disk.
  The check is read-only with respect to all persisted state (AC2).
- `resolve_contradiction` moves a pending (or already-persisted) contradiction to the
  registered list with the DM's chosen `ResolutionStrategy`, then saves — so
  `contradictions.json` contains exactly the contradictions the DM acted on, never
  the raw false-positive stream from keyword heuristics (AC3).

### Alternatives considered

- **Register-and-save-everything** — no module changes; check appends to the
  detector's registered list and any later save persists all of it. Rejected: first
  save pollutes `contradictions.json` with every false positive ever checked; the
  check is not side-effect-free.
- **Stateless round-trip** — check returns a payload; a persist tool re-accepts every
  field (statement, fact ids, type, severity, strategy). Restart-proof but a heavy,
  error-prone tool signature where the LLM re-enters data the detector never
  validated. Rejected for v1; pending checks being session-scoped is acceptable
  (re-running the check is cheap and read-only).

## Components

### 1. `claudmaster/consistency/contradiction.py` — pending mode

- `ContradictionDetector.__init__`: add `self._pending: dict[str, Contradiction] = {}`
  (in-memory only; never serialized; dies with the instance, i.e. on campaign
  close/switch).
- `check_statement(..., register: bool = True)`: new keyword. With `register=False`,
  detected contradictions go into `self._pending` keyed by id instead of
  `self._contradictions`. Return value unchanged (list of detections).
- `resolve(contradiction_id, strategy, notes=None)`: extended to check `_pending`
  first — pop, mark `resolved=True` + strategy + notes, append to `_contradictions`,
  return True. Falls through to the existing registered-list lookup (so a previously
  persisted contradiction, e.g. one flagged earlier, can be re-resolved).
- `save()` / `load()` untouched: pending is never persisted.
- `check_npc_statement` is out of scope (NPC-knowledge plausibility is DM2-13's
  territory) and keeps its current registering behavior.

Accepted quirk: re-checking the same statement mints new `ctr_*` ids (uuid-based) and
grows pending with duplicates. In-memory, per-campaign, trivial size — not worth a
dedupe for v1.

### 2. `storage.py` — lifecycle

- New field `self._contradiction_detector = None` in `__init__` next to the other
  fact-graph fields.
- `_load_fact_graph`: after FactDatabase/NPCKnowledgeTracker/PartyKnowledge are
  built, construct `ContradictionDetector(fact_db, npc_tracker, campaign_dir)` inside
  the same try block — same all-or-nothing failure envelope, degrading to None
  (fact-graph problems must never break the journal/entity write path). Reset to None
  in the failure path and at every site that clears `_fact_db` (campaign close /
  switch / delete).
- New property `contradiction_detector` mirroring `fact_db` / `party_knowledge`.

### 3. `main.py` — two MCP tools

**`check_consistency(statement, category=None, tags=None)`**

- Guards (existing patterns from `record_party_fact` / `party_knowledge`): no active
  campaign; detector None → "(split-format campaigns only)" message; empty statement;
  invalid category → list valid `FactCategory` values.
- `tags` accepted as JSON list or comma-separated string via `_parse_json_list`.
- Session number from `_current_session_number()`.
- Calls `detector.check_statement(..., register=False)`.
- No hits → explicit all-clear ("no conflicts with established facts").
- Hits → per contradiction: id, severity, type, the proposed statement, each
  conflicting fact rendered as id + content via `fact_db.get_fact` (id-only
  fallback), and `suggest_resolution` output ranked by confidence (strategy,
  confidence, description, side effects). Footer explains: nothing persisted; use
  `resolve_contradiction(<id>, <strategy>)` to record a decision (ids valid for this
  session).

**`resolve_contradiction(contradiction_id, strategy, notes=None)`**

- Same campaign/detector guards.
- Strategy validated against `ResolutionStrategy` (`retcon`, `explain`, `ignore`,
  `flag_for_dm`); accepts `flag` as an alias for `flag_for_dm` to match the ticket's
  vocabulary.
- `detector.resolve(...)` → on success `detector.save()` and confirm persistence to
  `contradictions.json`; for `retcon`, remind that resolving records the decision
  only — the conflicting fact/journal must be updated via the existing tools.
- Unknown id → error noting pending checks are session-scoped; re-run
  `check_consistency`.

### 4. `.claude/dm-persona.md` — activation

One bullet in the Continuity Protocol (the DM2-10 activation precedent): before
narrating something that asserts canon — a returning NPC's fate, a location's state,
a fact the party pinned down — call `check_consistency` with the proposed statement
(read-only, fast); on conflicts, adjust the narration or record the decision with
`resolve_contradiction`.

## Performance / AC2

Detection is `query_facts(limit=100)` plus keyword set operations per fact — pure
in-memory work, microseconds at campaign scale. No disk writes on the check path.

## Error handling

Follows the established degradation story: detector unavailable (legacy-format
campaign, graph load failure) → tools return an explanatory message, never raise.
Tool input validation returns messages listing valid enum values, mirroring
`record_party_fact`.

## Testing

- **Module** (`tests/claudmaster/test_contradiction.py`, extend): `register=False`
  leaves the registered list untouched and parks hits in pending; `resolve` on a
  pending id moves it to registered with strategy+notes; `save` excludes pending;
  resolved-pending survives a save/load roundtrip.
- **Wiring** (`tests/test_contradiction_check_wiring.py`, new — mirrors
  `test_timeline_wiring.py`'s storage-swap fixture): check reports conflict with
  severity, conflicting fact content, and suggestions; clean statement → all-clear;
  check writes nothing to disk (`contradictions.json` absent after check);
  resolve persists the contradiction with its strategy (visible to a fresh detector
  load); `flag` alias maps to `flag_for_dm`; unknown id and invalid
  category/strategy messages; detector-None degradation; lifecycle — detector shares
  the live `fact_db` instance (sees a fact added after campaign load).

## Out of scope

- NPC-statement plausibility checks (`check_npc_statement`) — DM2-13 adjacency.
- A read tool for persisted contradictions (`get_unresolved` browsing) — YAGNI until
  a consumer exists; the check output already shows everything at decision time.
- Auto-applying retcons to the fact graph — resolution is bookkeeping; fact edits
  stay with the existing write tools.
