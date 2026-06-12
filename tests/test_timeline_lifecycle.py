"""
Tests for the storage-held timeline tracker lifecycle (DM2-11).

DnDStorage caches a per-campaign TimelineTracker, mirroring the fact-graph /
DiscoveryTracker precedent: loaded on campaign create/load, cleared on delete,
degrading to None on failure. New split campaigns are born anchored at the
epoch (a fresh campaign's Day 1 is a genuine anchor, per the DM2-6 spike);
campaigns without a timeline.json load unanchored.
"""

from pathlib import Path

import pytest

from dm20_protocol.storage import DnDStorage


@pytest.fixture
def storage(tmp_path: Path) -> DnDStorage:
    return DnDStorage(data_dir=tmp_path / "data")


class TestTimelineLifecycle:
    def test_accessor_none_without_campaign(self, storage: DnDStorage):
        assert storage.timeline_tracker is None

    def test_created_campaign_is_born_anchored_at_epoch(self, storage: DnDStorage):
        storage.create_campaign(name="Test", description="d")
        tracker = storage.timeline_tracker
        assert tracker is not None
        assert tracker.anchored is True
        epoch = tracker.get_current_time()
        assert (epoch.year, epoch.month, epoch.day) == (1492, 1, 1)

    def test_born_anchored_persists_to_disk(self, storage: DnDStorage, tmp_path: Path):
        storage.create_campaign(name="Test", description="d")
        fresh = DnDStorage(data_dir=tmp_path / "data")
        fresh.load_campaign("Test")
        assert fresh.timeline_tracker is not None
        assert fresh.timeline_tracker.anchored is True

    def test_campaign_without_timeline_file_loads_unanchored(
        self, storage: DnDStorage, tmp_path: Path
    ):
        storage.create_campaign(name="Test", description="d")
        timeline_file = storage.timeline_tracker.campaign_path / "timeline.json"
        timeline_file.unlink()

        fresh = DnDStorage(data_dir=tmp_path / "data")
        fresh.load_campaign("Test")
        assert fresh.timeline_tracker is not None
        assert fresh.timeline_tracker.anchored is False

    def test_switch_campaign_swaps_tracker(self, storage: DnDStorage):
        storage.create_campaign(name="One", description="d")
        tracker_one = storage.timeline_tracker
        storage.create_campaign(name="Two", description="d")
        assert storage.timeline_tracker is not tracker_one

    def test_cleared_on_delete_of_active_campaign(self, storage: DnDStorage):
        storage.create_campaign(name="Test", description="d")
        storage.delete_campaign("Test")
        assert storage.timeline_tracker is None

    def test_failure_degrades_to_none(self, storage: DnDStorage, tmp_path: Path, monkeypatch):
        storage.create_campaign(name="Test", description="d")

        def boom(*args, **kwargs):
            raise RuntimeError("timeline store unavailable")

        import dm20_protocol.claudmaster.consistency.timeline as timeline_module

        monkeypatch.setattr(timeline_module.TimelineTracker, "load", boom)
        fresh = DnDStorage(data_dir=tmp_path / "data")
        fresh.load_campaign("Test")
        assert fresh.timeline_tracker is None
