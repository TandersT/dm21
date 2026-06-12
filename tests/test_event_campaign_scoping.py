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
