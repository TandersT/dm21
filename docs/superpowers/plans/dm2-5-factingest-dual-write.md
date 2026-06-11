# Plan — DM2-5 FactIngest dual-write + sync_facts

Spec: `docs/superpowers/specs/dm2-5-factingest-dual-write.md`

## Task 1 — FactIngest adapter (TDD)

Files: `src/dm20_protocol/consistency/fact_ingest.py` (new),
`tests/test_fact_ingest.py` (new)

1. Write failing tests for:
   - `ingest_event`: EVENT/QUEST/WORLD category mapping, `evt_<id>` fact id,
     relevance = importance/5, tags + session carried, content = description,
     source = "adventure_log", session fallback to `default_session`
   - `ingest_npc` / `ingest_location` / `ingest_quest`: deterministic ids
     (`<npc.id>`, `loc_<id>`, `quest_<id>`), content formats, quest resolution
     tags for completed/failed, no resolution tag for active/on_hold
   - Idempotency: double ingest → same fact count, converged fields
   - Merge-preserve: pre-attach `party_known` tag + `related_facts` link +
     session/timestamp, re-ingest, all preserved; quest active→completed→active
     sheds only the managed resolution tag
   - Met-tracking: characters_involved matching registered NPC (case-insensitive)
     records PlayerInteraction keyed by npc.id with mapped interaction_type;
     non-NPC names land in player_characters; re-ingest does not duplicate
     interactions; no tracker → no crash
   - `save()` persists both stores (reload from disk and assert)
2. Implement `FactIngest` until green.

## Task 2 — Storage-held fact graph lifecycle (TDD)

Files: `src/dm20_protocol/storage.py`, `tests/test_fact_graph_lifecycle.py` (new)

1. Failing tests: properties None before campaign; populated after
   `create_campaign` / `load_campaign` (split); shared FactDatabase instance
   between tracker/party-knowledge; cleared after `delete_campaign` of active
   campaign; load failure degrades to None (corrupt dir / exception path).
2. Implement `_fact_db`/`_npc_knowledge_tracker`/`_party_knowledge` fields,
   properties, `_load_fact_graph()`, call sites in `create_campaign`,
   `load_campaign`, clear in `delete_campaign`.

## Task 3 — Tool-layer dual-write + sync_facts + party_knowledge rewiring (TDD)

Files: `src/dm20_protocol/main.py`, `tests/test_fact_dual_write.py` (new)

1. Failing tests (storage-swap + `.fn()` pattern):
   - each of the five tools produces its fact (and met-tracking interactions
     for add_event) after the call, persisted to fact_database.json
   - update_quest to completed appends `completed` tag on `quest_<id>` fact
   - graph failure isolation: break `storage.fact_db` (monkeypatch property /
     poison save) → tool returns success string, journal/entity store written
   - `sync_facts` on empty fact DB: journal events + entity sweep ingested,
     counts in output, multi-campaign warning string present
   - second `sync_facts` run converges (fact count unchanged, no interaction
     duplicates)
   - `party_knowledge` tool uses storage accessors (no per-call instantiation)
     and reports unavailability when accessors are None
2. Implement `_ingest_to_fact_graph` helper, `_current_session_number` helper,
   hook the five tools, add `sync_facts` tool, rewire `party_knowledge`.

## Task 4 — Full verification

- `uv run pytest tests/test_fact_ingest.py tests/test_fact_graph_lifecycle.py tests/test_fact_dual_write.py -q`
- Adjacent scopes: `uv run pytest tests/test_party_knowledge.py tests/claudmaster/test_fact_database.py tests/claudmaster/test_npc_knowledge.py tests/claudmaster/test_recap_generator.py tests/test_storage.py tests/test_storage_integration.py tests/test_split_storage.py tests/test_delete_campaign.py -q`
