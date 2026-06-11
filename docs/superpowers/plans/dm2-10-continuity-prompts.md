# DM2-10 — Implementation plan

Spec: `docs/superpowers/specs/dm2-10-continuity-prompts.md`

## Task 1 — `start.md` resume flow

File: `.claude/commands/dm/start.md`.

- Frontmatter `allowed-tools`: append `mcp__dm20-protocol__get_session_recap`,
  `mcp__dm20-protocol__party_knowledge`, `mcp__dm20-protocol__sync_facts`,
  `mcp__dm20-protocol__get_events`.
- Step 4 "If resuming" list: recap-centric rewrite per spec Design 1 —
  `get_session_recap()` + `party_knowledge()` upfront, conditional
  `sync_facts()` retry, `get_sessions(detail="full")` +
  `get_events(session_number=<last>)` fallback, then the existing
  location/character/recap/scene steps with the recap-as-canon note.

## Task 2 — `dm-persona.md` Continuity Protocol

File: `.claude/dm-persona.md`.

- New `## Continuity Protocol` section after Tool Usage Patterns (two
  guidance-framed triggers, negative space, idempotency note).
- PERSIST step: add `record_party_fact` / `record_npc_interaction` bullets
  referencing the Continuity Protocol.
- Social pattern: insert the continuity writes between `add_event` and
  narrate.
- Resume Session subsection: rewrite to recap-centric flow per spec
  Design 2.

## Task 3 — `save.md` pre-save sweep

File: `.claude/commands/dm/save.md`.

- Frontmatter `allowed-tools`: append `mcp__dm20-protocol__record_party_fact`,
  `mcp__dm20-protocol__record_npc_interaction`.
- Insert step "3. Continuity Sweep" per spec Design 3; renumber steps 3–7
  to 4–8.

## Task 4 — Verification

- `uv run pytest -q` (full suite, regression guard).
- Grep `src/dm20_protocol/main.py` for every tool name + parameter the
  edited prompts reference; confirm exact signatures.
