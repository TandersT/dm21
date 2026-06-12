# DM2-14: Per-Campaign Adventure Log Scoping — Design

**Ticket:** DM2-14 — Refactor adventure log to per-campaign scoping
**Date:** 2026-06-12
**Status:** Approved (autonomous loop run wfl-2026-06-12-090330)

## Problem

The adventure log is global — one `events/adventure_log.json` shared by every
campaign in a data directory (`storage.py:_get_events_file`) — and
`AdventureEvent` has no campaign field. Backfill and queries cannot attribute
events when more than one campaign exists: `sync_facts` warns instead of
working cleanly, and a second campaign would pollute the first's fact graph
and recaps.

## Approaches considered

1. **Campaign-dir co-location with legacy fallback (chosen).** Split-format
   campaigns store their log at `campaigns/{name}/adventure_log.json`,
   mirroring how DiscoveryTracker, FactDatabase, NPCKnowledgeTracker, and
   TimelineTracker already co-locate per-campaign state in the campaign
   directory. The global `events/adventure_log.json` remains the fallback for
   monolithic campaigns and the no-campaign state.
2. **Per-campaign files under `events/`** (`events/{safe_name}.json`). Covers
   monolithic campaigns too, but splits campaign data across two directory
   trees, diverges from the established per-campaign pattern, and
   `delete_campaign` needs bespoke cleanup.
3. **Keep one global file, filter reads by a campaign field.** Least file
   churn, but the pollution fix then depends on every read site filtering;
   legacy unattributed events either keep polluting or silently vanish; write
   contention between campaigns remains.

Approach 1 wins: it follows the codebase's own convention for per-campaign
state, the ticket's stated impact (fact graph + recap pollution) is
split-format-only anyway (both degrade to `None` for monolithic campaigns),
and campaign deletion cleans up the log for free via the existing `rmtree`.

## Design

### Model (`models.py`)

`AdventureEvent` gains:

```python
campaign: str | None = None
```

Optional with a `None` default so events persisted before this field still
validate (Pydantic fills the default for missing keys).

### Storage (`storage.py`)

- **Stamping** — `DnDStorage.add_event` sets `event.campaign` to the current
  campaign's name (any format) when the field is unset. Stamping lives in the
  storage layer so every caller gets it.
- **Path resolution** — `_get_events_file()` returns
  `campaigns/{safe_name}/adventure_log.json` when a split campaign is loaded
  (via `_split_backend._get_campaign_dir`), otherwise the legacy global
  `events/adventure_log.json`.
- **Lifecycle** — `_load_events()` resets `self._events` to `[]` before
  loading, so it is safe to call on every campaign switch. `load_campaign`
  and `create_campaign` call it after the campaign/format state is set
  (same position as `_load_fact_graph` / `_load_timeline_tracker`).
  `delete_campaign` of the active campaign clears `_events` alongside the
  other per-campaign state.
- **Reads** — `get_events` / `search_events` are unchanged: for split
  campaigns the per-campaign file *is* the scope. No campaign filtering on
  the legacy file, so monolithic campaigns keep today's behavior and
  pre-field events never vanish from view.

### Migration (`storage.py`)

`_migrate_legacy_events()`, invoked from the events-load path when a split
campaign is loaded:

- Trigger: legacy global `events/adventure_log.json` exists **and**
  `list_campaigns()` returns exactly one campaign (the loaded one).
  With one campaign, attribution is unambiguous.
- Action: stamp `campaign` on legacy events that lack it, merge them into the
  per-campaign log (skipping ids already present, so re-runs are idempotent),
  save, and rename the legacy file to `adventure_log.json.migrated` — data is
  preserved but the legacy path is vacated.
- Multiple campaigns: do **not** guess. Leave the legacy file in place and
  log a warning. Those events are unattributed and excluded from every
  campaign's view — which is precisely the pollution fix; the user can
  attribute them manually if wanted.
- Failures are logged and swallowed: migration problems must never break
  campaign loading (same posture as the fact graph and timeline loaders).

### Tool layer (`main.py`)

- `sync_facts`: remove the unconditional "adventure log is global" warning.
  Append a conditional note when an unmigrated legacy log still exists
  ("N unattributed legacy events at events/adventure_log.json were not
  replayed").
- `add_event` tool and the DM2-11 `_stamp_timeline` hook are untouched —
  stamping runs in the tool layer after `storage.add_event` and does not
  depend on where the journal file lives.
- `get_events` / `get_session_recap` become campaign-scoped automatically
  through storage.

### Docs

- `docs/STORAGE_STRUCTURE.md`: events section + split campaign dir listing +
  summary table reflect the per-campaign location and the legacy fallback.
- `docs/project-continuity-graph-v1.md`: the "adventure log is global"
  limitation bullet (line 50) is updated to note it was resolved by DM2-14.

## Error handling

- Migration and event loading log-and-continue; they never raise into
  `load_campaign`.
- `_save_events` keeps its existing write mechanics (plain write, `json.dump`
  with `default=str`) — only the destination path changes.

## Testing

New `tests/test_event_campaign_scoping.py` (storage-level + tool-level, using
the `tmp_path`-backed `DnDStorage` and module-storage-swap fixtures from
`tests/test_fact_dual_write.py`):

1. Backward compat: an event dict without `campaign` validates, field is None.
2. `add_event` stamps the current campaign name; per-campaign file location.
3. Campaign switch isolation: events written in campaign A are not visible
   after loading campaign B; B's events don't leak into A.
4. Migration, single campaign: legacy log merges into the campaign log,
   events get stamped, legacy file renamed; idempotent on re-run.
5. Migration, multiple campaigns: legacy file untouched, events excluded
   from both campaigns' views.
6. `delete_campaign` of the active campaign clears in-memory events.
7. `sync_facts`: no global-log warning for a scoped campaign; conditional
   legacy note when an unmigrated global log exists.
8. No-campaign / monolithic fallback: events still read/write the global file.

Existing suites exercised by the diff (focused run): `test_storage.py`,
`test_split_storage.py`, `test_delete_campaign.py`, `test_fact_dual_write.py`,
`test_fact_ingest.py`, `test_session_recap_tool.py`,
`test_timeline_lifecycle.py`, `test_timeline_wiring.py`.

## Out of scope

- Campaign filtering on the legacy global file (would hide pre-field events
  for monolithic users).
- Atomic-write hardening of `_save_events` (pre-existing mechanics).
- Any interactive "assign these legacy events to campaign X" tooling.
