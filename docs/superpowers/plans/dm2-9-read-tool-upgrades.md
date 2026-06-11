# DM2-9 — Implementation plan

Spec: `docs/superpowers/specs/dm2-9-read-tool-upgrades.md`

## Task 1 — `storage.get_events` session filter

File: `src/dm20_protocol/storage.py` (`get_events`, ~line 1277).

- Add `session_number: int | None = None` parameter.
- Filter `e.session_number == session_number` when provided, alongside the
  `event_type` filter, before the timestamp sort and `limit` slice.

## Task 2 — `get_events` tool

File: `src/dm20_protocol/main.py` (`get_events`, ~line 2799).

- Add `session_number` (`ge=1`, default None) with `Annotated[..., Field(...)]`
  description.
- Non-search path: pass through to `storage.get_events`.
- Search path: filter `storage.search_events(search)` results by
  `session_number` in the tool when provided.

## Task 3 — `get_sessions` tool

File: `src/dm20_protocol/main.py` (`get_sessions`, ~line 2745).

- Add `detail: Literal["summary", "full"] = "summary"`.
- `full`: latest = max `session_number`; render it with untruncated summary
  plus non-empty `events` (bullets), `npcs_encountered` (comma-joined),
  `quest_updates` (`quest: progress` lines). All other sessions keep the
  existing one-line format.

## Task 4 — `get_npc` continuity block

File: `src/dm20_protocol/main.py` (`get_npc`, ~line 1437).

- Module-level `_npc_continuity_block(npc) -> str | None` helper: tracker
  None → None; no interactions → "Not yet met"; else first met (min
  session) / last seen (max session) / interaction count via
  `tracker.get_interactions(npc.id)`.
- Tool appends the block to `result.content` after
  `output_filter.filter_npc_response`, for all callers.

## Task 5 — Tests

- New `tests/test_read_tool_upgrades.py`: fixtures copied from
  `tests/test_session_recap_tool.py` (tmp-path storage + module-storage
  swap, `.fn` calls); cases per spec Testing section across the three tools
  plus storage-level `get_events` composition tests.

## Task 6 — Verification

- `uv run pytest tests/test_read_tool_upgrades.py tests/test_storage.py tests/test_output_filter.py tests/test_knowledge_write_tools.py -q`
- Broader sanity: `uv run pytest tests/test_main.py -q` (tool registration).
