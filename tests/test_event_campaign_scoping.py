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
