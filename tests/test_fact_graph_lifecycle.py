"""
Tests for the storage-held fact graph lifecycle (DM2-5).

DnDStorage caches per-campaign FactDatabase / NPCKnowledgeTracker /
PartyKnowledge accessors, mirroring the DiscoveryTracker precedent: loaded on
campaign create/load, cleared on delete, and degrading to None on failure so
graph-store problems never break the journal write path.
"""

from pathlib import Path

import pytest

from dm20_protocol.storage import DnDStorage


@pytest.fixture
def storage(tmp_path: Path) -> DnDStorage:
    return DnDStorage(data_dir=tmp_path / "data")


class TestFactGraphLifecycle:
    def test_accessors_none_without_campaign(self, storage: DnDStorage):
        assert storage.fact_db is None
        assert storage.npc_knowledge_tracker is None
        assert storage.party_knowledge is None

    def test_loaded_on_create_campaign(self, storage: DnDStorage):
        storage.create_campaign(name="Test", description="d")
        assert storage.fact_db is not None
        assert storage.npc_knowledge_tracker is not None
        assert storage.party_knowledge is not None

    def test_loaded_on_load_campaign(self, storage: DnDStorage, tmp_path: Path):
        storage.create_campaign(name="Test", description="d")

        fresh = DnDStorage(data_dir=tmp_path / "data")
        fresh.load_campaign("Test")
        assert fresh.fact_db is not None
        assert fresh.npc_knowledge_tracker is not None
        assert fresh.party_knowledge is not None

    def test_trackers_share_fact_db_instance(self, storage: DnDStorage):
        storage.create_campaign(name="Test", description="d")
        assert storage.npc_knowledge_tracker._fact_db is storage.fact_db
        assert storage.party_knowledge._fact_db is storage.fact_db

    def test_switch_campaign_swaps_graph(self, storage: DnDStorage):
        storage.create_campaign(name="One", description="d")
        db_one = storage.fact_db
        storage.create_campaign(name="Two", description="d")
        assert storage.fact_db is not db_one
        assert storage.fact_db.campaign_id != db_one.campaign_id

    def test_cleared_on_delete_of_active_campaign(self, storage: DnDStorage):
        storage.create_campaign(name="Test", description="d")
        storage.delete_campaign("Test")
        assert storage.fact_db is None
        assert storage.npc_knowledge_tracker is None
        assert storage.party_knowledge is None

    def test_failure_degrades_to_none(self, storage: DnDStorage, monkeypatch):
        storage.create_campaign(name="Test", description="d")

        def boom(*args, **kwargs):
            raise RuntimeError("graph store unavailable")

        import dm20_protocol.claudmaster.consistency.fact_database as fdb_module

        monkeypatch.setattr(fdb_module.FactDatabase, "load", boom)
        storage.load_campaign("Test")
        assert storage.fact_db is None
        assert storage.npc_knowledge_tracker is None
        assert storage.party_knowledge is None
        # the campaign itself still loaded fine
        assert storage.get_current_campaign() is not None

    def test_persisted_facts_survive_reload(self, storage: DnDStorage, tmp_path: Path):
        from dm20_protocol.claudmaster.consistency.models import Fact, FactCategory

        storage.create_campaign(name="Test", description="d")
        storage.fact_db.add_fact(
            Fact(id="f1", category=FactCategory.WORLD, content="Barovia is cursed", session_number=1)
        )
        storage.fact_db.save()

        fresh = DnDStorage(data_dir=tmp_path / "data")
        fresh.load_campaign("Test")
        assert fresh.fact_db.get_fact("f1") is not None
