"""
Tests for the DM-facing contradiction check wiring (DM2-12).

check_consistency / resolve_contradiction tools, the storage-held detector
lifecycle, and the no-side-effects guarantee of the check path. Tools are
exercised via the underlying functions (`.fn`) with the module-level storage
swapped, following tests/test_timeline_wiring.py.
"""

from pathlib import Path

import pytest

from dm20_protocol.claudmaster.consistency.contradiction import ContradictionDetector
from dm20_protocol.claudmaster.consistency.models import (
    Fact,
    FactCategory,
    ResolutionStrategy,
)
from dm20_protocol.storage import DnDStorage

STATEMENT = "Father Donavich is dead in the church"


@pytest.fixture
def storage(tmp_path: Path) -> DnDStorage:
    s = DnDStorage(data_dir=tmp_path / "data")
    s.create_campaign(name="Contradiction Test", description="d", dm_name="DM")
    return s


@pytest.fixture
def m(storage: DnDStorage):
    """dm20_protocol.main with its module-level storage swapped for the test."""
    from dm20_protocol import main as m

    original = m.storage
    m.storage = storage
    yield m
    m.storage = original


def _seed_fact(storage: DnDStorage) -> None:
    storage.fact_db.add_fact(Fact(
        id="fact_donavich",
        category=FactCategory.NPC,
        content="Father Donavich is alive and hiding in the church",
        session_number=1,
    ))
    storage.fact_db.save()


# ── Detector lifecycle ──────────────────────────────────────────────


class TestDetectorLifecycle:
    def test_detector_loaded_for_split_campaign(self, storage):
        assert storage.contradiction_detector is not None

    def test_detector_shares_live_fact_db_instance(self, storage):
        assert storage.contradiction_detector._fact_db is storage.fact_db
