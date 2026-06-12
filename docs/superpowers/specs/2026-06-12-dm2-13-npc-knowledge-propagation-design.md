# DM2-13 — Activate NPC-to-NPC knowledge propagation

**Ticket:** [DM2-13](https://linear.app/dm21/issue/DM2-13/activate-npc-to-npc-knowledge-propagation)
**Date:** 2026-06-12
**Base:** sta/dm2-12-contradiction-check (fact graph v1 + timeline + contradiction check present)

## Problem

`NPCKnowledgeTracker.reveal_to_npc` / `propagate_knowledge`
(`claudmaster/consistency/npc_knowledge.py`) are unused. The tracker itself is
already alive — it rides the fact-graph lifecycle in `DnDStorage._load_fact_graph`
and records `PlayerInteraction`s via `record_npc_interaction` — but NPCs have no
modeled knowledge: nothing writes `KnowledgeEntry`s, and the DM cannot ask who
knows what or how a rumor spreads.

## Acceptance criteria

1. Facts can be revealed to an NPC with a source and confidence
   (1.0 certain → 0.5 rumor).
2. Knowledge propagates between linked NPCs with confidence decay.
3. DM can query which NPCs know a given fact, and what a given NPC knows.

## Approach (chosen)

**Three MCP tools over minimally-extended tracker primitives.** The third
instance of the DM2-11/DM2-12 activation shape — except the storage lifecycle
work is already done (v1), so activation is module surface + tools + persona.

- `reveal_fact_to_npc` writes a `KnowledgeEntry` with source and confidence (AC1).
- `propagate_npc_knowledge` transfers entries NPC→NPC, with received confidence
  = sender's confidence × decay (AC2). "Linked" means the DM names the two NPCs
  who talk — explicit, auditable, no hidden simulation.
- `npc_knowledge` answers both query directions (AC3).
- Fact references resolve dual-mode: an existing fact id, else the
  content-derived `pfact_` id pinned by `record_party_fact` — so a fact the
  party recorded and the rumor an NPC holds converge on the same fact node, and
  "which NPCs know fact X" connects across both write paths.

### Alternatives considered

- **Automatic propagation over `NPC.relationships`** — spread knowledge to
  "linked" NPCs on reveal or per session. Rejected: no propagation trigger
  exists, `relationships` is name-keyed prose (not a reliable graph), silent
  world-state mutation the DM cannot audit, and beyond the ACs.
- **Fold into existing tools** — grow `record_npc_interaction` /
  `party_knowledge` with knowledge parameters. Rejected: muddles
  interaction-vs-knowledge semantics and party-vs-NPC scope; persona guidance
  becomes ambiguous.

## Components

### 1. `claudmaster/consistency/npc_knowledge.py` — two signature extensions

- `reveal_to_npc(..., confidence: float = 1.0)`: pass-through to
  `add_knowledge`. Default preserves existing behavior.
- `propagate_knowledge(..., decay: float = 0.75) -> list[str]`:
  - received confidence = sender entry's confidence × decay (was: hardcoded
    1.0 — an NPC could transmit more certainty than they had).
  - returns the list of fact ids actually propagated (was: `None`) so the
    tool can report; existing callers (none) and tests ignore the return.
  - skip rules unchanged: sender must know the fact; receiver keeping an
    existing entry wins (no overwrite, no reinforcement).
- Decay default lives in the module — one hop takes certain (1.0) to
  secondhand (0.75), two hops to rumor-grade (0.56), matching the
  1.0-certain / 0.5-rumor scale on `KnowledgeEntry`.

### 2. `storage.py` — no changes

`NPCKnowledgeTracker` already joins the fact-graph lifecycle sharing the live
`FactDatabase` (v1), with the same all-or-nothing degradation envelope.

### 3. `main.py` — shared helper + three MCP tools

**Helper `_content_fact_id(content)`** — extracts the pinned
`pfact_<sha256[:12]>` formula currently inlined in `record_party_fact`; both
call sites use it so the dedup formula cannot drift (DM2-5 lesson: embedded
deterministic dedup keys).

**`reveal_fact_to_npc(npc, fact, source="told_by_player", source_entity=None, confidence=1.0, session=None, category="world")`**

- Guards (pattern: `record_npc_interaction`): no campaign; tracker/fact_db
  None → "(split-format campaigns only)"; strict NPC resolution via
  `_resolve_npc` ("create it with create_npc first"); empty fact; invalid
  `source` → list valid `KnowledgeSource` values; confidence bounded 0–1 via
  Field constraints.
- Fact resolution: existing id → use it; else content → `_content_fact_id`,
  minting the `Fact` (with `category`, default `world` — only used when
  minting) if absent.
- Already-known → no-op message (entry keeps its original
  confidence/source; re-reveals do not upgrade).
- Writes via `reveal_to_npc` when source is `told_by_player`
  (`source_entity` is the revealing PC), else `add_knowledge` directly.
- Persists: `fact_db.save()` when a fact was minted; `tracker.save()`.
  Save errors propagate (established test contract).

**`propagate_npc_knowledge(from_npc, to_npc, facts=None, decay=None, session=None)`**

- Same campaign/tracker guards; both NPCs strictly resolved.
- `facts`: JSON list or comma-separated fact ids/content (dual resolution,
  no minting — the sender must already know them). Omitted → everything the
  sender knows ("the innkeeper tells the captain everything").
- `decay` validated in (0, 1]; omitted → module default 0.75.
- Reports per fact: propagated (with received confidence), skipped because
  the sender doesn't know it, or skipped because the receiver already knows.
- `tracker.save()` when anything propagated.

**`npc_knowledge(npc=None, fact=None)`** — read-only, exactly one argument

- `npc` mode: what the NPC knows — per entry the resolved fact content,
  source, confidence, session, `source_entity`; plus interaction count
  (via `get_knowledge_context`).
- `fact` mode: who knows it — `query_npcs_who_know`, NPC ids resolved back to
  names, each knower's confidence and source shown.
- Output style mirrors the `party_knowledge` tool's markdown.

### 4. `.claude/dm-persona.md` — activation

Continuity Protocol bullets (the DM2-10/11/12 precedent):

- An NPC learns something they could act on or repeat → `reveal_fact_to_npc`
  with source and confidence (1.0 certain → 0.5 rumor).
- NPCs talk offscreen / word spreads → `propagate_npc_knowledge`; confidence
  decays per hop, so retellings arrive as rumors.
- Before roleplaying an NPC referencing established events →
  `npc_knowledge(npc=...)`; NPCs only reference knowledge they hold.

Plus one PERSIST-list line for `reveal_fact_to_npc` next to the existing
`record_npc_interaction` line.

## Error handling

Established degradation story: tracker unavailable (legacy campaign, graph
load failure) → explanatory message, never raise. Enum/range validation
returns messages listing valid values, mirroring `record_party_fact`.

## Testing

- **Module** (`tests/claudmaster/test_npc_knowledge.py`, extend):
  `reveal_to_npc` confidence pass-through (default 1.0 intact); propagation
  decay math (1.0 → 0.75 → 0.5625 over two hops); custom decay; return value
  lists exactly the propagated ids; unknown-to-sender and already-known skips
  excluded from the return.
- **Wiring** (`tests/test_npc_knowledge_wiring.py`, new — storage-swap
  fixture from `test_knowledge_write_tools.py`): reveal by NPC name and id;
  reveal minting a fact vs converging on `record_party_fact`'s node (pinned
  formula); invalid source / empty fact / unknown NPC messages; already-known
  no-op; propagate with decay visible in receiver confidence; facts=None
  propagates all; unknown fact reported as skipped; query both modes incl.
  empty results; persistence across a fresh `DnDStorage` load; degradation
  with `_fact_db=None`; no-campaign guard.

## Out of scope

- Automatic rumor spread (relationship-graph or time-based) — needs a trigger
  and an auditable model; revisit when a consumer exists.
- Confidence reinforcement/upgrade on repeated reveals — first entry wins;
  documented in tool output.
- `check_npc_statement` plausibility checks (contradiction-detector
  adjacency) — not in the ACs.
- `share_with_party` wiring — the party side already has `record_party_fact`.
