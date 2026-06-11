# DM2-8 — Implementation plan

Spec: `docs/superpowers/specs/dm2-8-session-recap-tool.md`

## Task 1 — Generator: constants, `verbatim_events`, injected events

File: `src/dm20_protocol/claudmaster/continuity/recap_generator.py`.

- Module-level `RECAP_LENGTHS = ("brief", "standard", "detailed")` and
  `RECAP_STYLES = ("narrative", "bullet", "mixed")`; export via `__all__`.
- `TYPE_CHECKING` import of `AdventureEvent` from `dm20_protocol.models`.
- `SessionRecap.verbatim_events: list[AdventureEvent] =
  field(default_factory=list)` (documented in the docstring).
- `generate_recap(..., events: list[AdventureEvent] | None = None)`: sort
  `events or []` by (`-importance`, `timestamp`) and assign to
  `verbatim_events` on the returned recap.

## Task 2 — `get_session_recap` tool

File: `src/dm20_protocol/main.py`, after `sync_facts` / before the character
import section.

- Signature per spec: `session_number` (`ge=1`, default None), `length`,
  `style` with `Annotated[..., Field(...)]` descriptions.
- Guards in DM2-7 order: campaign → accessors (`storage.fact_db`,
  `storage.npc_knowledge_tracker`) → validate `length`/`style` against the
  imported `RECAP_LENGTHS`/`RECAP_STYLES` ("Invalid X. Valid: …").
- Session resolution per pin 1; session events filtered from
  `storage.get_events()`.
- Function-local import of `SessionRecapGenerator`, `RECAP_LENGTHS`,
  `RECAP_STYLES`; instantiate per call with the cached accessors,
  `timeline=None`; `generate_recap(..., events=session_events)`.
- Markdown rendering per spec flow step 7 (verbatim section always present,
  full descriptions).

## Task 3 — Tests

- `tests/claudmaster/test_recap_generator.py`: add a class covering
  `verbatim_events` default-empty and injected-sorted behaviour.
- New `tests/test_session_recap_tool.py`: fixtures copied from
  `tests/test_knowledge_write_tools.py`; cases per spec Testing section,
  E2E regression test (pin 4) first.

## Task 4 — Verification

- `uv run pytest tests/test_session_recap_tool.py tests/claudmaster/test_recap_generator.py tests/test_fact_dual_write.py tests/test_fact_ingest.py -q`
- Broader sanity: `uv run pytest tests/test_main.py -q` (tool registration).
