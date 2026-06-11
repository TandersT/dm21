# DM2-9 — Full session detail and session filtering for read tools

Ticket: [DM2-9](https://linear.app/dm21/issue/DM2-9/add-full-session-detail-and-session-filtering-to-read-tools)
Size: Standard. Branch: `sta/dm2-9-read-tool-upgrades` (base: `sta/dm2-8-session-recap-tool`).

## Problem

The read primitives hide stored data. `get_sessions` truncates every summary
to 100 chars mid-sentence and drops all structured `SessionNote` fields
(`events`, `npcs_encountered`, `quest_updates`). `get_events` cannot filter by
session even though `AdventureEvent.session_number` exists and is persisted.
`get_npc` shows no met-state even though the NPC knowledge tracker records
per-NPC `PlayerInteraction`s (DM2-7's `record_npc_interaction` plus DM2-5's
event ingestion).

## Decisions (user-pinned)

1. **`get_sessions` gains `detail: Literal["summary", "full"] = "summary"`;
   `full` expands only the LATEST session** (untruncated summary + non-empty
   structured fields: `events`, `npcs_encountered`, `quest_updates`); older
   sessions keep the current one-line format. No `session_number` selector.
2. **`get_events` gains `session_number: int | None = None` on BOTH the tool
   and `storage.get_events`**; storage filters before sort/limit so it
   composes with `event_type` and `limit`; search results also get the
   session filter applied (in the tool, after `storage.search_events`).
   DM2-8's recap keeps its in-memory filter — no refactor.
3. **`get_npc` appends a continuity block after output filtering, for ALL
   callers** (DM and players): "First met: Session X / Last seen: Session Y /
   Interactions: N". Missing tracker (`storage.npc_knowledge_tracker is
   None`) → block omitted entirely; tracker present with zero interactions →
   "Not yet met". `OutputFilter` signatures untouched.

## Design

### 1. `get_sessions(detail)` (main.py)

```python
@mcp.tool
def get_sessions(
    detail: Literal["summary", "full"] = "summary",  # full → latest session expanded
) -> str
```

- `detail="summary"` (default): byte-for-byte current behaviour — sorted by
  `session_number` ascending, one header line + 100-char summary per session.
- `detail="full"`: latest session = max `session_number` over
  `storage.get_sessions()`. Every non-latest session renders exactly as
  today. The latest session renders expanded:
  - same `**Session N** (date): title` header line,
  - full untruncated `summary`,
  - then each structured field **only when non-empty** (pin 1):
    - `Events:` — one bullet per entry,
    - `NPCs encountered:` — comma-joined list,
    - `Quest updates:` — one `quest: progress` line per entry.
- Single session in the campaign → it is the latest → expanded.
- Read-only; no model or storage changes.

### 2. `get_events(session_number)` (main.py + storage.py)

Storage (`storage.py:1277`):

```python
def get_events(
    self,
    limit: int | None = None,
    event_type: str | None = None,
    session_number: int | None = None,
) -> list[AdventureEvent]
```

The session filter (`e.session_number == session_number`) runs alongside the
`event_type` filter, **before** the timestamp sort and `limit` slice, so
`limit=N` means "N most recent events *of that session*" (pin 2).
`session_number=None` → unchanged behaviour. Events with
`session_number=None` never match an explicit filter.

Tool (`main.py:2799`):

```python
@mcp.tool
def get_events(
    limit: int | None = None,
    event_type: Literal[...] | None = None,
    search: str | None = None,
    session_number: int | None = None,  # ge=1
) -> str
```

- Non-search path: pass `session_number` straight through to
  `storage.get_events`.
- Search path: `storage.search_events(search)` stays untouched; the tool
  filters the results by `session_number` when provided (pin 2). `search` +
  `session_number` therefore compose; `search` continues to ignore
  `limit`/`event_type` as today (existing behaviour, not widened).
- `get_session_recap` keeps its own in-memory session filter (pin 2 —
  explicitly no refactor).

### 3. `get_npc` continuity block (main.py)

`OutputFilter` is untouched (pin 3). The tool appends the block after
filtering, for every caller — met-state is public table knowledge, not
DM-only:

```python
result = output_filter.filter_npc_response(npc, player_id=player_id)
block = _npc_continuity_block(npc)
return result.content + block if block else result.content
```

Module-level helper next to the tool:

```python
def _npc_continuity_block(npc: NPC) -> str | None
```

- `storage.npc_knowledge_tracker is None` → `None` (block omitted entirely —
  graceful-degrade convention from DM2-5: tracker failure/absence never
  breaks or pollutes the read path).
- Otherwise `interactions = tracker.get_interactions(npc.id)` (interactions
  are keyed by NPC id — same key DM2-7's `record_npc_interaction` and
  DM2-5's `ingest_event` write under):
  - empty → `**Continuity:** Not yet met`
  - else → `**Continuity:** First met: Session X / Last seen: Session Y /
    Interactions: N` with X = min `session_number`, Y = max
    `session_number`, N = interaction count.

## Acceptance criteria → design trace

- `get_sessions(detail="full")` returns the latest `SessionNote` untruncated
  with its structured fields; older sessions stay one-line; `"summary"`
  remains the default → Design 1 (pin 1).
- `get_events(session_number=N)` returns only that session's events, tool +
  storage layer → Design 2 (pin 2).
- `get_npc` output includes a continuity block (first met / last seen /
  interaction count) from the tracker → Design 3 (pin 3).

## Out of scope

- A `session_number` selector on `get_sessions` (pin 1: latest only).
- Adding `session_number`/`limit`/`event_type` composition to
  `storage.search_events` (tool-side filter only).
- Refactoring `get_session_recap`'s in-memory event filter (pin 2).
- `OutputFilter` signature or formatter changes (pin 3).
- Adding `"social"` to `get_events`' `event_type` Literal (pre-existing gap,
  separate concern).

## Testing

New `tests/test_read_tool_upgrades.py` following the
`tests/test_session_recap_tool.py` conventions (tmp-path storage fixture +
module-storage swap, tools exercised via `.fn`):

`get_sessions`:

- Default/`"summary"`: long summary truncated at 100 chars with ellipsis
  (current behaviour preserved).
- `"full"`: latest session shows untruncated summary + events bullets +
  NPCs + quest updates; older session in same output stays truncated and
  shows no structured fields.
- `"full"` with empty structured fields on the latest session → those
  headings absent.
- No sessions → "No session notes recorded." for both detail levels.

`get_events` (storage layer, direct `storage.get_events` calls):

- `session_number=N` returns only that session's events.
- Composes with `event_type` and with `limit` (filter applied before the
  limit slice).
- `session_number=None` → unchanged full list; events without a session
  never match an explicit filter.

`get_events` (tool):

- `session_number=N` filters tool output.
- `search` + `session_number` compose.

`get_npc`:

- Tracker `None` → no continuity block in output.
- Tracker present, no interactions → "Not yet met".
- After `record_npc_interaction` in sessions 2 and 4 → "First met: Session 2
  / Last seen: Session 4 / Interactions: 2".
- Block present for player callers too (after output filtering).
