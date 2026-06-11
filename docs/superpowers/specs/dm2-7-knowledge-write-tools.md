# DM2-7 — record_party_fact and record_npc_interaction tools

Ticket: [DM2-7](https://linear.app/dm21/issue/DM2-7/add-record-party-fact-and-record-npc-interaction-tools)
Size: Standard. Branch: `sta/dm2-7-knowledge-write-tools` (base: `sta/dm2-5-factingest-dual-write`).

## Problem

The `party_knowledge` MCP tool is read-only — there is no write surface for
epistemic state ("the party learned X from Y") or for richer NPC interactions
than the auto-stamped ones from event ingestion. These are semantics the
auto-ingest layer cannot infer.

## Decisions (user-pinned)

1. **`record_party_fact` fact id is deterministic and content-derived**:
   `pfact_<sha256(content.lower())[:12]>`. Repeated identical calls converge on
   the same fact; `learn_fact`'s fact_id dedupe makes the tool idempotent
   end-to-end.
2. **`record_npc_interaction` uses strict NPC resolution plus an idempotent
   `ingest_npc` upsert in the same invocation.** The NPC must already exist
   (no auto-create); the merge-preserve upsert guarantees the NPC's fact
   exists in the graph with fact id == entity id — the recap id-join
   invariant — even for NPCs created before DM2-5's dual-write.
3. **Repeated `record_npc_interaction` dedupes on exact (npc, summary,
   session)** — a no-op with an "already recorded" response. Cross-session
   repeats stay recordable. `interaction_type` is intentionally not part of
   the key.

Carried-forward skeleton (from the DM2-5 stack):

- Two `@mcp.tool` functions in `main.py`, placed next to `party_knowledge` /
  `sync_facts`.
- DM2-5 storage-held accessors (`storage.fact_db`, `storage.party_knowledge`,
  `storage.npc_knowledge_tracker`) — never re-instantiated per call.
- Campaign + None guards returning the established "fact graph unavailable"
  message style.
- Writes surface errors directly — the fact write IS the primary write here,
  so no swallow-and-log (`_ingest_to_fact_graph` is for *dual*-writes only).
- Explicit `save()` per invocation (the graph stores do not autosave).
- Param shapes mirror `learn_fact` / `PlayerInteraction` fields with
  `Annotated`/`Field` conventions; enum validation matches the
  `party_knowledge` tool's error style (accept `str`, try the enum, return
  "Invalid X. Valid: …" on failure).
- `session` defaults to the current game-state session
  (`_current_session_number()`).

## Design

### `record_party_fact` (main.py)

```python
@mcp.tool
def record_party_fact(
    content: str,            # the fact the party learned (required, non-empty)
    category: str,           # FactCategory value: event/location/npc/item/quest/world
    source: str,             # who/what provided it (NPC name, book title, …)
    method: str,             # AcquisitionMethod value: told_by_npc/observed/…
    session: int | None = None,   # ge=1; defaults to current session
    location: str | None = None,  # where the party learned this
    notes: str | None = None,     # acquisition context
) -> str
```

Flow:

1. Campaign guard → "No active campaign…" (existing message).
2. `storage.fact_db` / `storage.party_knowledge` None → "fact graph
   unavailable (split-format campaigns only)" message.
3. Empty `content` (after strip) → error.
4. Validate `category` against `FactCategory`, `method` against
   `AcquisitionMethod` — `party_knowledge`-style error listing valid values.
5. `session = session or _current_session_number()`.
6. `fact_id = f"pfact_{sha256(content.lower())[:12]}"` (utf-8, hexdigest).
7. Upsert: if `fact_db.get_fact(fact_id)` is None, `add_fact(Fact(id=fact_id,
   category=…, content=content, session_number=session, source=source))`.
   If it exists the content is identical by construction (hash-derived id) —
   merge-preserve means leave it untouched.
8. `learned = pk.learn_fact(fact_id, source, method, session, location,
   notes)` — False means the party already knows it.
9. `fact_db.save()` and `pk.save()` — explicit, errors propagate.
   (`learn_fact` mutates the fact's tags, so `fact_db.save()` runs after it.)
10. Response: newly learned → confirmation with fact id, method, source,
    session; already known → "already knows" no-op message. Both paths save
    (covers re-creating a fact the records file knows but the graph lost).

Imports: `Fact`, `FactCategory` from `claudmaster.consistency.models` are
function-local (lazy-claudmaster convention); `AcquisitionMethod` is already
module-level in main.py.

### `record_npc_interaction` (main.py)

```python
@mcp.tool
def record_npc_interaction(
    npc: str,                  # NPC name or id — must already exist
    interaction_type: str,     # conversation/combat/trade/observed/… (free-form, model field is str)
    summary: str,              # what happened (required, non-empty)
    session: int | None = None,        # ge=1; defaults to current session
    player_characters: str | None = None,  # list or JSON array string (add_event convention)
    location: str | None = None,
) -> str
```

Flow:

1. Campaign guard.
2. `storage.fact_db` / `storage.npc_knowledge_tracker` None → "fact graph
   unavailable" message.
3. Empty `summary` → error.
4. **Strict NPC resolution**: exact name via `storage.get_npc`, then
   case-insensitive name, then entity id over `list_npcs_detailed()`.
   Not found → "NPC '<npc>' not found. Create the NPC first with
   create_npc." — no auto-create.
5. `session = session or _current_session_number()`.
6. **Dedupe (pin 3)**: `tracker.get_interactions(npc.id, session=session)`
   already contains an interaction with this exact `summary` → skip the
   record, respond "already recorded".
7. **Fact-exists invariant (pin 2)**: `FactIngest(fact_db,
   tracker).ingest_npc(npc, session=session)` — idempotent merge-preserve
   upsert, runs on every invocation (including the dedupe path) so the
   invariant holds even for pre-DM2-5 NPCs.
8. Not a dupe → `tracker.record_interaction(npc.id,
   PlayerInteraction(session_number=session, interaction_type=…, summary=…,
   player_characters=_parse_json_list(...), location=location or ""))`.
9. `ingest.save()` — persists fact_db + tracker, errors propagate.
10. Response: recorded → confirmation with NPC name, type, session;
    dupe → "already recorded" no-op message.

`PlayerInteraction` and `FactIngest` imports are function-local.

## Acceptance criteria → design trace

- `record_party_fact(content, category, source, method, …)` persists via
  `PartyKnowledge.learn_fact` and is queryable via `party_knowledge` →
  flow steps 7–9; the learn_fact tagging makes it visible to every existing
  query path.
- `record_npc_interaction(npc, interaction_type, summary)` persists a
  `PlayerInteraction` via `NPCKnowledgeTracker.record_interaction` → flow
  step 8.
- Both tools save to disk per invocation → explicit `save()` calls; errors
  surface to the caller instead of being swallowed.

## Out of scope

- Auto-creating NPCs from `record_npc_interaction` (pinned: strict).
- A `forget_fact` / unlearn write path.
- Monolithic-format campaigns (fact graph is split-format only).

## Testing

New `tests/test_knowledge_write_tools.py` following the
`tests/test_fact_dual_write.py` conventions (storage fixture, module-storage
swap, `m.<tool>.fn(...)`):

- record_party_fact: persists + queryable via `party_knowledge` tool;
  deterministic id (`pfact_` + 12 hex); identical repeat converges (no new
  fact, "already knows" response); case-insensitive content converges;
  invalid category/method errors list valid values; session defaults to
  current; persists to disk across a fresh storage load; unavailable-graph
  guard; empty content rejected.
- record_npc_interaction: persists a PlayerInteraction; unknown NPC errors
  without recording; pre-DM2-5 NPC gets its fact upserted (id == entity id);
  exact (npc, summary, session) repeat no-ops with count unchanged;
  cross-session repeat records again; resolution by entity id; session
  defaults to current; persists to disk; unavailable-graph guard.
