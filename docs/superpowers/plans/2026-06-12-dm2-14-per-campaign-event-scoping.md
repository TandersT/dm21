# DM2-14: Per-Campaign Adventure Log Scoping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scope the adventure log per campaign — `campaign` field on `AdventureEvent`, per-campaign log file for split campaigns, one-shot migration of the legacy global log, and removal of the now-obsolete `sync_facts` cross-campaign warning.

**Architecture:** Split-format campaigns store their journal at `campaigns/{name}/adventure_log.json`, following the existing per-campaign pattern (DiscoveryTracker / FactDatabase / TimelineTracker all live in the campaign dir). The legacy global `events/adventure_log.json` stays as the fallback for monolithic campaigns and the no-campaign state. Migration runs on the events-load path when attribution is unambiguous (exactly one campaign exists). Spec: `docs/superpowers/specs/2026-06-12-dm2-14-per-campaign-event-scoping-design.md`.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest. Run tests via `uv run pytest` from the repo root.

---

### Task 1: `campaign` field on `AdventureEvent` + storage stamping

**Files:**
- Modify: `src/dm20_protocol/models.py:563-574` (AdventureEvent)
- Modify: `src/dm20_protocol/storage.py:1284-1289` (add_event)
- Test: `tests/test_event_campaign_scoping.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_event_campaign_scoping.py`:

```python
"""
Tests for per-campaign adventure log scoping (DM2-14).

Split-format campaigns keep their own adventure_log.json in the campaign
directory; the legacy global events/adventure_log.json remains the fallback
for monolithic campaigns and the no-campaign state. Storage-level tests use a
tmp_path-backed DnDStorage; tool-level tests swap the module-level storage,
following tests/test_fact_dual_write.py.
"""

import json
from pathlib import Path

import pytest

from dm20_protocol.models import AdventureEvent, EventType
from dm20_protocol.storage import DnDStorage


def _make_event(title: str = "Something happened", **kwargs) -> AdventureEvent:
    return AdventureEvent(
        event_type=EventType.WORLD,
        title=title,
        description=f"{title} description",
        **kwargs,
    )


# ── Campaign field ──────────────────────────────────────────────────


class TestCampaignField:
    def test_event_without_campaign_field_validates(self):
        """Events persisted before the field existed must still load."""
        event = AdventureEvent.model_validate(
            {
                "id": "abc12345",
                "event_type": "world",
                "title": "Old event",
                "description": "Persisted before the campaign field existed",
                "timestamp": "2026-01-01T10:00:00",
            }
        )
        assert event.campaign is None

    def test_add_event_stamps_current_campaign(self, tmp_path: Path):
        s = DnDStorage(data_dir=tmp_path / "data")
        s.create_campaign(name="Barovia", description="d")
        s.add_event(_make_event())
        assert s.get_events()[0].campaign == "Barovia"

    def test_add_event_preserves_explicit_campaign(self, tmp_path: Path):
        s = DnDStorage(data_dir=tmp_path / "data")
        s.create_campaign(name="Barovia", description="d")
        s.add_event(_make_event(campaign="Elsewhere"))
        assert s.get_events()[0].campaign == "Elsewhere"

    def test_add_event_without_campaign_leaves_none(self, tmp_path: Path):
        s = DnDStorage(data_dir=tmp_path / "data")
        s.add_event(_make_event())
        assert s.get_events()[0].campaign is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_event_campaign_scoping.py -v`
Expected: `test_event_without_campaign_field_validates` and the stamping tests FAIL (`AdventureEvent` has no field `campaign` — Pydantic raises on the explicit kwarg; the stamped assertion gets `AttributeError`/`None`).

- [ ] **Step 3: Add the model field**

In `src/dm20_protocol/models.py`, add one line to `AdventureEvent` (after `importance`, line 574):

```python
class AdventureEvent(BaseModel):
    """Individual event in the adventure log."""
    id: str = Field(default_factory=lambda: random(length=8))
    event_type: EventType
    title: str
    description: str
    timestamp: datetime = Field(default_factory=datetime.now)
    session_number: int | None = None
    characters_involved: list[str] = Field(default_factory=list)
    location: str | None = None
    tags: list[str] = Field(default_factory=list)
    importance: int = Field(ge=1, le=5, default=3)  # 1=minor, 5=major
    campaign: str | None = None  # owning campaign; None = unattributed (pre-scoping)
```

- [ ] **Step 4: Stamp in `DnDStorage.add_event`**

In `src/dm20_protocol/storage.py:1284`, replace:

```python
    def add_event(self, event: AdventureEvent) -> None:
        """Add an event to the adventure log."""
        logger.info(f"➕ Adding event: '{event.title}' ({event.event_type})")
        self._events.append(event)
        self._save_events()
        logger.debug("✅ Event added and log saved.")
```

with:

```python
    def add_event(self, event: AdventureEvent) -> None:
        """Add an event to the adventure log.

        Stamps the current campaign on the event (when not already set) so
        every caller gets campaign attribution.
        """
        logger.info(f"➕ Adding event: '{event.title}' ({event.event_type})")
        if event.campaign is None and self._current_campaign:
            event.campaign = self._current_campaign.name
        self._events.append(event)
        self._save_events()
        logger.debug("✅ Event added and log saved.")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_event_campaign_scoping.py -v`
Expected: 4 PASS

- [ ] **Step 6: Commit**

```bash
git add src/dm20_protocol/models.py src/dm20_protocol/storage.py tests/test_event_campaign_scoping.py
git commit -m "feat(DM2-14): add campaign field to AdventureEvent with storage-layer stamping"
```

---

### Task 2: Per-campaign events file + lifecycle reload

**Files:**
- Modify: `src/dm20_protocol/storage.py:216-218` (_get_events_file), `:438-452` (_load_events), `:547-596` (load_campaign), `:455-520` (create_campaign), `:598-660` (delete_campaign)
- Test: `tests/test_event_campaign_scoping.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_event_campaign_scoping.py`:

```python
# ── Per-campaign file + lifecycle ───────────────────────────────────


class TestPerCampaignStorage:
    def test_split_campaign_events_live_in_campaign_dir(self, tmp_path: Path):
        data = tmp_path / "data"
        s = DnDStorage(data_dir=data)
        s.create_campaign(name="Barovia", description="d")
        s.add_event(_make_event())
        assert (data / "campaigns" / "Barovia" / "adventure_log.json").exists()
        assert not (data / "events" / "adventure_log.json").exists()

    def test_campaign_switch_isolates_events(self, tmp_path: Path):
        s = DnDStorage(data_dir=tmp_path / "data")
        s.create_campaign(name="Alpha", description="d")
        s.add_event(_make_event(title="Alpha event"))

        s.create_campaign(name="Beta", description="d")
        assert s.get_events() == []
        s.add_event(_make_event(title="Beta event"))

        s.load_campaign("Alpha")
        assert [e.title for e in s.get_events()] == ["Alpha event"]

        s.load_campaign("Beta")
        assert [e.title for e in s.get_events()] == ["Beta event"]

    def test_events_survive_storage_reinit(self, tmp_path: Path):
        data = tmp_path / "data"
        s = DnDStorage(data_dir=data)
        s.create_campaign(name="Barovia", description="d")
        s.add_event(_make_event(title="Persisted"))

        s2 = DnDStorage(data_dir=data)  # init loads most recent campaign
        assert [e.title for e in s2.get_events()] == ["Persisted"]

    def test_delete_active_campaign_clears_events(self, tmp_path: Path):
        s = DnDStorage(data_dir=tmp_path / "data")
        s.create_campaign(name="Barovia", description="d")
        s.add_event(_make_event())
        s.delete_campaign("Barovia")
        assert s.get_events() == []

    def test_no_campaign_falls_back_to_global_log(self, tmp_path: Path):
        data = tmp_path / "data"
        s = DnDStorage(data_dir=data)
        s.add_event(_make_event(title="Campaignless"))
        assert (data / "events" / "adventure_log.json").exists()
        assert [e.title for e in s.get_events()] == ["Campaignless"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_event_campaign_scoping.py -v`
Expected: `TestPerCampaignStorage` tests FAIL (events still write to the global file; switches don't reload). `test_no_campaign_falls_back_to_global_log` may already pass — that's the unchanged legacy path.

- [ ] **Step 3: Implement path resolution**

In `src/dm20_protocol/storage.py:216`, replace:

```python
    def _get_events_file(self) -> Path:
        """Get the file path for adventure events."""
        return self.data_dir / "events" / "adventure_log.json"
```

with:

```python
    def _get_events_file(self) -> Path:
        """Get the file path for adventure events.

        Split-format campaigns keep their own log in the campaign directory
        (like the discovery/fact-graph/timeline state). Monolithic campaigns
        and the no-campaign state fall back to the legacy global log.
        """
        if self._current_format == StorageFormat.SPLIT and self._current_campaign:
            campaign_dir = self._split_backend._get_campaign_dir(self._current_campaign.name)
            return campaign_dir / "adventure_log.json"
        return self._get_legacy_events_file()

    def _get_legacy_events_file(self) -> Path:
        """Get the file path of the legacy global adventure log."""
        return self.data_dir / "events" / "adventure_log.json"
```

- [ ] **Step 4: Make `_load_events` reset state so it is switch-safe**

In `src/dm20_protocol/storage.py:438`, replace:

```python
    def _load_events(self):
        """Load adventure events from disk."""
        logger.debug("📂 Attempting to load adventure events...")
        events_file = self._get_events_file()
        if not events_file.exists():
            logger.debug("❌ Adventure log file does not exist. No events loaded.")
            return
```

with:

```python
    def _load_events(self):
        """Load adventure events for the current campaign from disk.

        Resets in-memory events first, so calling this on every campaign
        switch is safe.
        """
        logger.debug("📂 Attempting to load adventure events...")
        self._events = []
        events_file = self._get_events_file()
        if not events_file.exists():
            logger.debug("❌ Adventure log file does not exist. No events loaded.")
            return
```

(the `try/except` body below stays unchanged)

- [ ] **Step 5: Reload events on campaign lifecycle transitions**

In `load_campaign` (`storage.py:593`), after `self._load_timeline_tracker()`, add:

```python
        # Load the campaign's adventure log (split campaigns have their own;
        # monolithic campaigns fall back to the legacy global log)
        self._load_events()
```

In `create_campaign` (`storage.py:517`), after the timeline-anchoring block (`self._timeline_tracker.save()`), add:

```python
        # Load the campaign's adventure log (runs legacy migration when this
        # is the only campaign in the data directory)
        self._load_events()
```

In `delete_campaign` (`storage.py:655`), inside the active-campaign clear block, after `self._contradiction_detector = None`, add:

```python
            self._events = []
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_event_campaign_scoping.py -v`
Expected: all PASS

- [ ] **Step 7: Run neighbouring storage suites**

Run: `uv run pytest tests/test_storage.py tests/test_split_storage.py tests/test_delete_campaign.py tests/test_fact_dual_write.py -q`
Expected: PASS (no regressions; these exercise campaign lifecycle and the add_event dual-write path)

- [ ] **Step 8: Commit**

```bash
git add src/dm20_protocol/storage.py tests/test_event_campaign_scoping.py
git commit -m "feat(DM2-14): per-campaign adventure log for split campaigns with lifecycle reload"
```

---

### Task 3: One-shot legacy log migration

**Files:**
- Modify: `src/dm20_protocol/storage.py` (_load_events + new _migrate_legacy_events)
- Test: `tests/test_event_campaign_scoping.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_event_campaign_scoping.py`:

```python
# ── Legacy log migration ────────────────────────────────────────────


def _write_legacy_log(data_dir: Path, events: list[AdventureEvent]) -> Path:
    events_dir = data_dir / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    legacy_file = events_dir / "adventure_log.json"
    legacy_file.write_text(
        json.dumps([e.model_dump(mode="json") for e in events]), encoding="utf-8"
    )
    return legacy_file


class TestLegacyMigration:
    def test_legacy_log_migrates_to_lone_campaign(self, tmp_path: Path):
        data = tmp_path / "data"
        s = DnDStorage(data_dir=data)
        s.create_campaign(name="Barovia", description="d")
        # Simulate the pre-scoping state: events only in the global log
        legacy_file = _write_legacy_log(
            data, [_make_event(title="Legacy one"), _make_event(title="Legacy two")]
        )

        s2 = DnDStorage(data_dir=data)  # init loads Barovia -> migration runs
        titles = sorted(e.title for e in s2.get_events())
        assert titles == ["Legacy one", "Legacy two"]
        assert all(e.campaign == "Barovia" for e in s2.get_events())
        assert not legacy_file.exists()
        assert (data / "events" / "adventure_log.json.migrated").exists()
        assert (data / "campaigns" / "Barovia" / "adventure_log.json").exists()

    def test_migration_merges_without_duplicates(self, tmp_path: Path):
        data = tmp_path / "data"
        s = DnDStorage(data_dir=data)
        s.create_campaign(name="Barovia", description="d")
        s.add_event(_make_event(title="Native"))
        shared = _make_event(title="Shared")
        s.add_event(shared)
        # Legacy log holds one already-known event and one new one
        _write_legacy_log(data, [shared, _make_event(title="Legacy only")])

        s2 = DnDStorage(data_dir=data)
        titles = sorted(e.title for e in s2.get_events())
        assert titles == ["Legacy only", "Native", "Shared"]

    def test_migration_is_idempotent_across_reinits(self, tmp_path: Path):
        data = tmp_path / "data"
        s = DnDStorage(data_dir=data)
        s.create_campaign(name="Barovia", description="d")
        _write_legacy_log(data, [_make_event(title="Legacy")])

        s2 = DnDStorage(data_dir=data)
        s3 = DnDStorage(data_dir=data)  # second init: legacy file already gone
        assert [e.title for e in s3.get_events()] == ["Legacy"]

    def test_legacy_log_left_alone_with_multiple_campaigns(self, tmp_path: Path):
        data = tmp_path / "data"
        s = DnDStorage(data_dir=data)
        s.create_campaign(name="Alpha", description="d")
        s.create_campaign(name="Beta", description="d")
        legacy_file = _write_legacy_log(data, [_make_event(title="Orphan")])

        s2 = DnDStorage(data_dir=data)
        assert legacy_file.exists()  # untouched — attribution is ambiguous
        assert all(e.title != "Orphan" for e in s2.get_events())
        s2.load_campaign("Alpha")
        assert all(e.title != "Orphan" for e in s2.get_events())

    def test_first_campaign_creation_adopts_legacy_log(self, tmp_path: Path):
        data = tmp_path / "data"
        s = DnDStorage(data_dir=data)
        _write_legacy_log(data, [_make_event(title="Pre-campaign")])

        s2 = DnDStorage(data_dir=data)
        s2.create_campaign(name="First", description="d")
        assert [e.title for e in s2.get_events()] == ["Pre-campaign"]
        assert s2.get_events()[0].campaign == "First"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_event_campaign_scoping.py::TestLegacyMigration -v`
Expected: FAIL — legacy events are simply invisible to split campaigns (no migration exists yet); multi-campaign test may pass (nothing migrates), single-campaign tests fail.

- [ ] **Step 3: Implement migration**

In `src/dm20_protocol/storage.py`, add after `_load_events` (after line 452):

```python
    def _migrate_legacy_events(self) -> None:
        """One-shot migration of the legacy global adventure log (DM2-14).

        With exactly one campaign in the data directory, attribution is
        unambiguous: stamp the legacy events with the campaign name, merge
        them into the campaign's own log (skipping ids already present), and
        vacate the legacy path by renaming the file (data stays recoverable).
        With multiple campaigns the events cannot be attributed — leave the
        file alone and warn.

        Failures are logged and swallowed: migration problems must never
        break campaign loading.
        """
        legacy_file = self._get_legacy_events_file()
        if not legacy_file.exists() or not self._current_campaign:
            return

        try:
            campaigns = self.list_campaigns()
            if campaigns != [self._current_campaign.name]:
                logger.warning(
                    f"⚠️ Legacy global adventure log at {legacy_file} left unmigrated: "
                    f"{len(campaigns)} campaigns exist, so its events cannot be attributed. "
                    "They are excluded from campaign views; attribute them manually if needed."
                )
                return

            with open(legacy_file, 'r', encoding='utf-8') as f:
                legacy_events = [AdventureEvent.model_validate(e) for e in json.load(f)]
            for event in legacy_events:
                if event.campaign is None:
                    event.campaign = self._current_campaign.name

            events_file = self._get_events_file()
            existing: list[AdventureEvent] = []
            if events_file.exists():
                with open(events_file, 'r', encoding='utf-8') as f:
                    existing = [AdventureEvent.model_validate(e) for e in json.load(f)]
            known_ids = {e.id for e in existing}
            merged = existing + [e for e in legacy_events if e.id not in known_ids]

            events_data = [event.model_dump(mode='json') for event in merged]
            with open(events_file, 'w', encoding='utf-8') as f:
                json.dump(events_data, f, default=str)

            legacy_file.rename(legacy_file.parent / (legacy_file.name + ".migrated"))
            logger.info(
                f"✅ Migrated {len(legacy_events)} legacy events to campaign "
                f"'{self._current_campaign.name}' ({events_file})."
            )
        except Exception as e:
            logger.warning(f"Legacy adventure log migration failed (load unaffected): {e}")
```

- [ ] **Step 4: Invoke migration from the events-load path**

In `_load_events`, after `self._events = []` and before `events_file = self._get_events_file()`, add:

```python
        if self._current_format == StorageFormat.SPLIT and self._current_campaign:
            self._migrate_legacy_events()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_event_campaign_scoping.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/dm20_protocol/storage.py tests/test_event_campaign_scoping.py
git commit -m "feat(DM2-14): migrate legacy global adventure log when attribution is unambiguous"
```

---

### Task 4: `sync_facts` — drop the global-log warning, add conditional legacy note

**Files:**
- Modify: `src/dm20_protocol/storage.py` (new `legacy_unattributed_event_count`)
- Modify: `src/dm20_protocol/main.py:5494-5503` (sync_facts return)
- Test: `tests/test_event_campaign_scoping.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_event_campaign_scoping.py`:

```python
# ── sync_facts: warning removed, conditional legacy note ────────────


@pytest.fixture
def scoped_storage(tmp_path: Path) -> DnDStorage:
    s = DnDStorage(data_dir=tmp_path / "data")
    s.create_campaign(name="Scoped", description="d", dm_name="DM")
    return s


@pytest.fixture
def m(scoped_storage: DnDStorage):
    """dm20_protocol.main with its module-level storage swapped for the test."""
    from dm20_protocol import main as m

    original = m.storage
    m.storage = scoped_storage
    yield m
    m.storage = original


class TestSyncFactsLegacyNote:
    def test_no_global_log_warning_for_scoped_campaign(self, m, scoped_storage):
        m.add_event.fn(event_type="world", description="The mists close in.")
        result = m.sync_facts.fn()
        assert "global" not in result.lower()
        assert "other campaigns" not in result.lower()

    def test_notes_unmigrated_legacy_log(self, m, scoped_storage):
        # A second campaign makes the legacy log unattributable
        scoped_storage.create_campaign(name="Second", description="d")
        _write_legacy_log(
            scoped_storage.data_dir,
            [_make_event(title="Orphan one"), _make_event(title="Orphan two")],
        )
        result = m.sync_facts.fn()
        assert "2 unattributed legacy event" in result
        assert "not replayed" in result

    def test_storage_counts_legacy_events(self, scoped_storage):
        assert scoped_storage.legacy_unattributed_event_count() == 0
        scoped_storage.create_campaign(name="Second", description="d")
        _write_legacy_log(scoped_storage.data_dir, [_make_event(title="Orphan")])
        assert scoped_storage.legacy_unattributed_event_count() == 1

    def test_count_is_zero_when_legacy_file_is_the_active_log(self, tmp_path: Path):
        s = DnDStorage(data_dir=tmp_path / "data")  # no campaign loaded
        s.add_event(_make_event())
        assert s.legacy_unattributed_event_count() == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_event_campaign_scoping.py::TestSyncFactsLegacyNote -v`
Expected: FAIL — `legacy_unattributed_event_count` doesn't exist; sync_facts still emits the unconditional global warning.

- [ ] **Step 3: Add the storage counter**

In `src/dm20_protocol/storage.py`, add after `search_events` (after line 1320):

```python
    def legacy_unattributed_event_count(self) -> int:
        """Count events stranded in the legacy global adventure log.

        Returns 0 when the legacy log is absent, unreadable, or is the
        active events file (monolithic / no-campaign state, where it is not
        stranded but simply current).
        """
        legacy_file = self._get_legacy_events_file()
        if not legacy_file.exists() or legacy_file == self._get_events_file():
            return 0
        try:
            with open(legacy_file, 'r', encoding='utf-8') as f:
                return len(json.load(f))
        except (json.JSONDecodeError, OSError, ValueError):
            return 0
```

- [ ] **Step 4: Rework the sync_facts return**

In `src/dm20_protocol/main.py:5494-5503`, replace:

```python
    return (
        f"✅ Fact sync complete for campaign '{campaign.name}'.\n"
        f"- Events replayed: {len(events)}\n"
        f"- Entities swept: {len(npcs)} NPCs, {len(locations)} locations, {len(quests)} quests\n"
        f"- Facts: {facts_before} → {facts_after} (+{facts_after - facts_before})\n"
        f"- NPC interactions recorded: {interactions_recorded}\n\n"
        f"⚠️ The adventure log is global and has no campaign attribution — events "
        f"from other campaigns sharing this data directory may have been ingested "
        f"into this campaign's fact graph."
    )
```

with:

```python
    result = (
        f"✅ Fact sync complete for campaign '{campaign.name}'.\n"
        f"- Events replayed: {len(events)}\n"
        f"- Entities swept: {len(npcs)} NPCs, {len(locations)} locations, {len(quests)} quests\n"
        f"- Facts: {facts_before} → {facts_after} (+{facts_after - facts_before})\n"
        f"- NPC interactions recorded: {interactions_recorded}"
    )
    legacy_count = storage.legacy_unattributed_event_count()
    if legacy_count:
        result += (
            f"\n\nℹ️ {legacy_count} unattributed legacy event(s) at "
            f"events/adventure_log.json were not replayed — the log predates "
            f"per-campaign scoping and could not be auto-attributed (multiple "
            f"campaigns share this data directory). Add a campaign field to "
            f"those events to attribute them manually."
        )
    return result
```

Also update the `sync_facts` docstring's first paragraph (line 5438) — replace "Replays all adventure log events" with "Replays the campaign's adventure log events" (the rest stays).

- [ ] **Step 5: Remove the obsolete pinned-warning test**

`tests/test_fact_dual_write.py:195-197` pins the removed warning:

```python
    def test_warns_about_multi_campaign_attribution(self, m, populated_storage):
        result = m.sync_facts.fn()
        assert "global" in result.lower()
```

Delete this test — its subject (the unconditional global-log warning) no longer
exists, and the replacement behavior is owned by `TestSyncFactsLegacyNote` in
`tests/test_event_campaign_scoping.py`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_event_campaign_scoping.py -v`
Expected: all PASS

- [ ] **Step 7: Run the fact/recap/timeline consumer suites**

Run: `uv run pytest tests/test_fact_dual_write.py tests/test_fact_ingest.py tests/test_session_recap_tool.py tests/test_timeline_lifecycle.py tests/test_timeline_wiring.py -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/dm20_protocol/storage.py src/dm20_protocol/main.py tests/test_event_campaign_scoping.py tests/test_fact_dual_write.py
git commit -m "feat(DM2-14): replace sync_facts global-log warning with conditional legacy note"
```

---

### Task 5: Documentation updates

**Files:**
- Modify: `docs/STORAGE_STRUCTURE.md:29-54` (split listing), `:67-75` (events section), `:120-133` (summary table)
- Modify: `docs/project-continuity-graph-v1.md:50` (limitation bullet)

- [ ] **Step 1: Update the split-format listing**

In `docs/STORAGE_STRUCTURE.md`, in the split-format tree (after the `claudmaster-config.json` line), add:

```
    ├── adventure_log.json          # Campaign-scoped adventure log (AdventureEvent array)
```

- [ ] **Step 2: Update the Events Directory section**

Replace lines 67-75:

```markdown
## Events Directory

- **Path**: `events/`
- **Purpose**: Stores the global adventure log, shared across all campaigns.

```
events/
└── adventure_log.json              # JSON array of all AdventureEvent objects
```
```

with:

```markdown
## Events Directory (legacy)

- **Path**: `events/`
- **Purpose**: Legacy fallback adventure log. Split-format campaigns store
  their log inside the campaign directory (`campaigns/{name}/adventure_log.json`);
  this global file is only used by monolithic campaigns and when no campaign
  is loaded.

```
events/
├── adventure_log.json              # Legacy/fallback JSON array of AdventureEvent objects
└── adventure_log.json.migrated     # Backup left behind after one-shot migration
```

When a split campaign is loaded and it is the only campaign in the data
directory, any legacy global log is migrated into the campaign's own log
(events stamped with the campaign name) and the global file is renamed to
`adventure_log.json.migrated`. With multiple campaigns the legacy events
cannot be attributed automatically and the file is left in place, excluded
from campaign views.
```

- [ ] **Step 3: Update the summary table**

Change the `events/` row:

```markdown
| `events/` | Legacy/fallback adventure log | `DnDStorage.__init__` |
```

and add a row after `campaigns/{name}/claudmaster_sessions/`:

```markdown
| `campaigns/{name}/adventure_log.json` | Campaign-scoped adventure log | `DnDStorage._save_events` |
```

- [ ] **Step 4: Update the v1 caveat**

In `docs/project-continuity-graph-v1.md:50`, replace:

```markdown
- The adventure log is global with no campaign attribution: `sync_facts` may ingest events from other campaigns sharing the same data directory (the tool warns about this in its output).
```

with:

```markdown
- ~~The adventure log is global with no campaign attribution: `sync_facts` may ingest events from other campaigns sharing the same data directory (the tool warns about this in its output).~~ Resolved by DM2-14: split-format campaigns now keep a per-campaign log (`campaigns/{name}/adventure_log.json`) with one-shot migration of the legacy global log; `sync_facts` replays only the current campaign's events.
```

- [ ] **Step 5: Commit**

```bash
git add docs/STORAGE_STRUCTURE.md docs/project-continuity-graph-v1.md
git commit -m "docs(DM2-14): document per-campaign adventure log and legacy fallback"
```

---

### Task 6: Full focused verification

- [ ] **Step 1: Run the complete focused suite**

Run: `uv run pytest tests/test_event_campaign_scoping.py tests/test_storage.py tests/test_split_storage.py tests/test_delete_campaign.py tests/test_fact_dual_write.py tests/test_fact_ingest.py tests/test_session_recap_tool.py tests/test_timeline_lifecycle.py tests/test_timeline_wiring.py tests/test_read_tool_upgrades.py tests/test_milestone_seal_continuity_graph.py -q`
Expected: PASS (the full repo suite has ~143 pre-existing interaction failures on main — judge by these focused suites only)

- [ ] **Step 2: Commit any straggler fixes**

Only if Step 1 surfaced adjustments.
