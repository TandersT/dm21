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
