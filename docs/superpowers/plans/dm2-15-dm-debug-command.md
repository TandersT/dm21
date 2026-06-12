# Plan: /dm:debug command (DM2-15)

Spec: `docs/superpowers/specs/dm2-15-dm-debug-command.md`

Single deliverable — one new markdown slash-command file. No Python code,
no schema changes, no tests affected.

## Task 1 — Create `.claude/commands/dm/debug.md`

Follow the sibling command conventions (`action.md`, `save.md`):

- **Frontmatter:** `description`, `argument-hint`, `allowed-tools` limited to
  exactly what the command needs:
  `mcp__dm20-protocol__get_game_state`, `mcp__dm20-protocol__list_characters`,
  `mcp__linear-dm21__save_issue`.
- **No DM-persona include** — this is an out-of-character utility; it never
  narrates, so injecting the persona file would waste context.
- **Sections:** Usage (one-shot + single-question fallback), Instructions
  (capture → gather context → file → confirm/resume), ticket body template,
  Error Handling.
- Ticket body template includes Issue + Play context sections and a
  "Filed via `/dm:debug`" footer; a no-session variant swaps the
  context list for "No active session at time of filing."

## Verification

- Frontmatter parses as YAML; tool names in `allowed-tools` match the real
  MCP tool names available in this environment.
- No Python test scope matches the diff (markdown-only); confirmed nothing
  under `tests/` references `.claude/commands/`.
