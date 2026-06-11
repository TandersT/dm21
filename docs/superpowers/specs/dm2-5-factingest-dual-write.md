# DM2-5 — FactIngest dual-write pipeline and sync_facts backfill

Ticket: [DM2-5](https://linear.app/dm21/issue/DM2-5/add-factingest-dual-write-pipeline-and-sync-facts-backfill)
Size: Standard. Branch: `sta/dm2-5-factingest-dual-write` (base: `main`).

## Problem

The fact graph (`FactDatabase`, `NPCKnowledgeTracker`) has zero production write
paths. The five play-loop MCP tools in `src/dm20_protocol/main.py` (`add_event`,
`create_npc`, `create_location`, `create_quest`, `update_quest`) write only to the
journal/entity stores, so every downstream consumer (recap, party knowledge)
returns nothing.

## Decisions (user-pinned)

1. **Dual-write hook lives at the tool layer** — the five MCP tools in `main.py`
   call FactIngest after each storage write. The bulk module-import path in
   `adventures/tools.py` stays OUT of the fact graph.
2. **`sync_facts` backfills journal events + a sweep of current-campaign
   NPCs/locations/quests**, converging on what dual-write would have produced.
   Deterministic ids keep it idempotent.
3. **Lifecycle: storage-held cached accessors** for
   FactDatabase/NPCKnowledgeTracker/PartyKnowledge, mirroring the
   DiscoveryTracker precedent (`storage.py:1354-1384`): loaded on campaign
   load/create, cleared on delete, failure degrades to None. Graph-store
   failures never break the journal write.
4. (Carried from brainstorm — single viable path) **Upsert is merge-preserve,
   not replace**: re-ingestion must not clobber `party_known` tags or
   `related_facts` links attached by other subsystems and not derivable from
   the journal.

## Design

### New module: `src/dm20_protocol/consistency/fact_ingest.py`

`FactIngest` — a stateless adapter constructed from the two graph stores:

```python
class FactIngest:
    def __init__(self, fact_db: FactDatabase, npc_tracker: NPCKnowledgeTracker | None = None): ...
    def ingest_event(self, event: AdventureEvent, npcs_by_name: Mapping[str, NPC] = {}, default_session: int = 1) -> str
    def ingest_npc(self, npc: NPC, session: int = 1) -> str
    def ingest_location(self, location: Location, session: int = 1) -> str
    def ingest_quest(self, quest: Quest, session: int = 1) -> str
    def save(self) -> None   # persists fact_db (+ npc_tracker) once
```

Ingest methods mutate in-memory only; callers invoke `save()` once per tool
invocation (`FactDatabase.add_fact` does not autosave — `fact_database.py:56`).
`sync_facts` calls many ingests, then one `save()`.

Module is NOT exported from `consistency/__init__.py` — importing it pulls in
the `claudmaster` package, which must stay lazy (existing convention: the
`party_knowledge` tool and `storage.get_claudmaster_config` both import
claudmaster inside functions).

### Fact mapping (per AC)

| Source | Fact id | Category | Content | Relevance | Session | Tags |
|---|---|---|---|---|---|---|
| `AdventureEvent` | `evt_<event.id>` | `quest`→QUEST, `world`→WORLD, else EVENT | `event.description` | `importance / 5` | `event.session_number` or `default_session` | `event.tags` |
| `NPC` | `<npc.id>` (entity id — required by `recap_generator.py:418` join) | NPC | `name` (+ ` — <description>` when set) | 1.0 (model default) | caller-supplied current session | none derived |
| `Location` | `loc_<location.id>` | LOCATION | `name (location_type): description` | 1.0 | current session | none derived |
| `Quest` | `quest_<quest.id>` | QUEST | `title: description` | 1.0 | current session | resolution tag from status: `completed`/`failed` |

`source` field: `"adventure_log"` for events, `"campaign"` for entities.

### Merge-preserve upsert semantics

`_upsert_fact(...)` — if the fact id already exists:

- **Overwrite** from derived values: `content`, `category`, `relevance_score`, `source`.
- **Preserve**: `related_facts` (never touched), `timestamp` and
  `session_number` (established-at metadata, set once at first ingestion —
  keeps live dual-write attribution stable across later `sync_facts` replays).
- **Tags**: `derived_tags + [t for t in existing.tags if t not in derived and t not in managed_tags]`.
  `managed_tags = {"completed", "failed"}` is passed only by quest ingestion, so
  a quest flipping back to `active` sheds its stale resolution tag while
  `party_known` and any foreign tags survive. `get_active_threads`
  (`recap_generator.py:247`) filters on exactly these resolution tags.

### Met-tracking (PlayerInteraction)

In `ingest_event`: each name in `event.characters_involved` is matched
case-insensitively against registered NPCs (`npcs_by_name`). On match, record a
`PlayerInteraction` on the tracker keyed by `npc.id`:

- `interaction_type`: `combat`→`"combat"`, `roleplay`/`social`→`"conversation"`, else `event_type.value`
- `summary`: `f"{event.title} [evt_<event.id>]"` — the embedded deterministic
  event id is the idempotency key: re-ingestion skips recording when an
  interaction with the same summary already exists for that NPC
  (`PlayerInteraction` has no id field and `record_interaction` appends blindly)
- `session_number`: `event.session_number or default_session`; `timestamp`:
  `event.timestamp` (deterministic); `player_characters`: involved names that
  did NOT match an NPC; `location`: `event.location or ""`

### Storage lifecycle (mirrors DiscoveryTracker)

In `DnDStorage`:

- `__init__`: `_fact_db = _npc_knowledge_tracker = _party_knowledge = None`
- read-only properties `fact_db`, `npc_knowledge_tracker`, `party_knowledge`
- `_load_fact_graph()`: SPLIT campaigns only; builds the three objects against
  `_split_backend._get_campaign_dir(...)` inside try/except — on failure logs a
  warning and leaves all three None. Claudmaster imports are function-local.
- Called from `load_campaign()` (covers startup auto-load, which routes through
  `load_campaign`) and `create_campaign()`; cleared in `delete_campaign()`'s
  active-campaign reset block.

### Tool-layer dual-write (main.py)

One best-effort helper:

```python
def _ingest_to_fact_graph(ingest_fn) -> None:
    """Dual-write to the fact graph; failures are logged, never raised."""
```

It no-ops when `storage.fact_db` is None, otherwise constructs
`FactIngest(storage.fact_db, storage.npc_knowledge_tracker)`, runs the closure,
saves once, and swallows+logs any exception — the primary journal write has
already succeeded and must not be broken.

Each of the five tools calls it after its storage write. `update_quest`
re-fetches the quest after mutation and re-ingests it (upsert refreshes the
resolution tags). Entity ingestion passes the current game-state session
(`max(1, game_state.current_session)`, fallback 1).

### `party_knowledge` tool rewiring

The tool currently re-instantiates `FactDatabase` + `PartyKnowledge` per call
(`main.py:4565`, named in the ticket Notes). It now uses `storage.fact_db` /
`storage.party_knowledge`; when the accessors are None (no split campaign
loaded / graph failed to load) it returns a clear "fact graph unavailable"
message.

### `sync_facts` MCP tool (main.py)

- Guards: campaign loaded; `storage.fact_db` available.
- Replays all journal events (chronological order) through
  `ingest_event(event, npcs_by_name, default_session=1)` — deterministic from
  the journal alone, so replay converges with what dual-write produced
  (merge-preserve keeps live-attributed session numbers).
- Sweeps current-campaign NPCs, locations, quests through the entity ingests
  with the current session as default.
- One `save()` at the end.
- Output: facts before/after, events replayed, entities swept, interactions
  recorded, plus the multi-campaign warning required by the AC: the adventure
  log is global (`data/events/adventure_log.json`, no campaign field), so
  events from other campaigns sharing the data directory may be attributed to
  the current campaign.

## Acceptance criteria → design trace

- Five tools emit Facts with the pinned mapping → tool-layer hooks + mapping table
- `characters_involved` NPC matches record `PlayerInteraction` → met-tracking section
- Quest status changes append resolution tags → managed-tags upsert
- Deterministic ids converge on re-ingestion → id scheme + merge-preserve upsert
- Graph failures never break journal writes → `_ingest_to_fact_graph` + None-degrading accessors
- `sync_facts` populates an empty fact DB from the journal and warns about
  multi-campaign attribution → sync_facts section (verified via fixture
  campaigns in tests; the global log has no campaign field)

## Out of scope

- `adventures/tools.py` bulk module import (pinned OUT)
- Read-path consumers (recap/party-knowledge activation — downstream tickets
  DM2-7..DM2-10 stack on this branch)
- Monolithic-format campaigns (fact graph is split-format only, like
  DiscoveryTracker)

## Testing

- `tests/test_fact_ingest.py` — adapter unit tests: category mapping, relevance,
  deterministic ids, idempotent re-ingest, merge-preserve (`party_known` +
  `related_facts` survive), quest resolution tag add/remove, met-tracking +
  interaction dedupe, save persistence.
- Storage lifecycle tests — accessors populated on create/load, None on
  delete/no-campaign.
- Tool-layer tests via the established `m.<tool>.fn(...)` + storage-swap
  pattern (`tests/test_tool_output_enrichment.py:135`) — dual-write on each of
  the five tools, failure isolation (graph store broken → tool still succeeds),
  `sync_facts` backfill on a populated journal + entity stores, idempotency of
  a second `sync_facts` run.
