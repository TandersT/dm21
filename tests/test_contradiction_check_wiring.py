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


# ── check_consistency ───────────────────────────────────────────────


class TestCheckConsistency:
    def test_reports_conflict_with_severity_fact_and_suggestions(self, m, storage):
        _seed_fact(storage)
        result = m.check_consistency.fn(statement=STATEMENT, category="npc")
        assert "ctr_" in result
        assert "major" in result
        assert "character" in result
        assert "Father Donavich is alive and hiding in the church" in result
        assert "flag_for_dm" in result
        assert "resolve_contradiction" in result

    def test_clean_statement_reports_no_conflicts(self, m, storage):
        _seed_fact(storage)
        result = m.check_consistency.fn(
            statement="The party shares a quiet meal at the tavern"
        )
        assert "No conflicts" in result

    def test_check_writes_nothing_to_disk(self, m, storage):
        _seed_fact(storage)
        detector = storage.contradiction_detector
        m.check_consistency.fn(statement=STATEMENT)
        assert not detector._contradictions_path.exists()
        assert detector.get_all_contradictions() == []

    def test_invalid_category_lists_valid_values(self, m, storage):
        result = m.check_consistency.fn(statement=STATEMENT, category="bogus")
        assert "Invalid category" in result
        assert "npc" in result

    def test_empty_statement_rejected(self, m, storage):
        result = m.check_consistency.fn(statement="   ")
        assert "empty" in result.lower()

    def test_detector_unavailable_degrades_with_guidance(self, m, storage):
        storage._contradiction_detector = None
        result = m.check_consistency.fn(statement=STATEMENT)
        assert "unavailable" in result.lower()


# ── resolve_contradiction ───────────────────────────────────────────


class TestResolveContradiction:
    def _detect(self, m, storage) -> str:
        _seed_fact(storage)
        m.check_consistency.fn(statement=STATEMENT)
        pending_ids = list(storage.contradiction_detector._pending)
        assert len(pending_ids) == 1
        return pending_ids[0]

    def test_resolve_persists_with_strategy_and_notes(self, m, storage):
        cid = self._detect(m, storage)
        result = m.resolve_contradiction.fn(
            contradiction_id=cid, strategy="retcon", notes="He died offscreen"
        )
        assert "persisted" in result
        reloaded = ContradictionDetector(
            storage.fact_db,
            campaign_path=storage.contradiction_detector._campaign_path,
        )
        contradictions = reloaded.get_all_contradictions()
        assert len(contradictions) == 1
        assert contradictions[0].id == cid
        assert contradictions[0].resolved is True
        assert contradictions[0].resolution == ResolutionStrategy.RETCON
        assert contradictions[0].resolution_notes == "He died offscreen"

    def test_flag_alias_maps_to_flag_for_dm(self, m, storage):
        cid = self._detect(m, storage)
        result = m.resolve_contradiction.fn(contradiction_id=cid, strategy="flag")
        assert "flag_for_dm" in result

    def test_retcon_reminds_to_update_the_fact(self, m, storage):
        cid = self._detect(m, storage)
        result = m.resolve_contradiction.fn(contradiction_id=cid, strategy="retcon")
        assert "write tools" in result

    def test_unknown_id_explains_session_scope(self, m, storage):
        result = m.resolve_contradiction.fn(contradiction_id="ctr_nope", strategy="ignore")
        assert "not found" in result
        assert "check_consistency" in result

    def test_invalid_strategy_lists_valid_values(self, m, storage):
        result = m.resolve_contradiction.fn(contradiction_id="ctr_x", strategy="bogus")
        assert "Invalid strategy" in result
        assert "retcon" in result
