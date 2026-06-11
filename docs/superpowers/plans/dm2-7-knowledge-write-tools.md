# DM2-7 — Implementation plan

Spec: `docs/superpowers/specs/dm2-7-knowledge-write-tools.md`

## Task 1 — `record_party_fact` tool

File: `src/dm20_protocol/main.py`, after the `party_knowledge` tool /
before `sync_facts`.

- Signature per spec: `content`, `category`, `source`, `method` required;
  `session` (`ge=1`), `location`, `notes` optional. `Annotated[..., Field(...)]`
  descriptions mirroring `KnowledgeRecord` / `learn_fact` docs.
- Guards: campaign → accessors (`storage.fact_db`, `storage.party_knowledge`)
  → non-empty content → enum validation (`FactCategory`, `AcquisitionMethod`)
  with `party_knowledge`-style "Invalid X. Valid: …" messages.
- `fact_id = f"pfact_{hashlib.sha256(content.lower().encode('utf-8')).hexdigest()[:12]}"`.
- Create the Fact only if absent; `pk.learn_fact(...)`; `fact_db.save()` +
  `pk.save()` after learn_fact (it mutates fact tags). No try/except around
  the writes.
- Function-local import of `Fact`, `FactCategory` (claudmaster stays lazy).
  `hashlib` import at module top (stdlib).

## Task 2 — `record_npc_interaction` tool

Same region of `main.py`.

- Signature per spec: `npc`, `interaction_type`, `summary` required;
  `session` (`ge=1`), `player_characters` (JSON list string,
  `_parse_json_list`), `location` optional.
- Guards: campaign → accessors (`storage.fact_db`,
  `storage.npc_knowledge_tracker`) → non-empty summary → strict NPC
  resolution (exact name, case-insensitive name, entity id).
- Dedupe on exact (npc.id, summary, session) via
  `tracker.get_interactions(npc.id, session=session)`.
- Always run `FactIngest(...).ingest_npc(npc, session=session)`; record the
  interaction only when not a dupe; `ingest.save()` always.
- Function-local imports of `FactIngest`, `PlayerInteraction`.

## Task 3 — Tests

New file `tests/test_knowledge_write_tools.py`, copying the fixtures from
`tests/test_fact_dual_write.py` (tmp-path storage + module-storage swap).
Cases per spec Testing section, grouped in two test classes.

## Task 4 — Verification

- `uv run pytest tests/test_knowledge_write_tools.py tests/test_fact_dual_write.py tests/test_fact_ingest.py tests/test_party_knowledge.py -q`
- Broader sanity: `uv run pytest tests/test_main.py -q` (tool registration).
