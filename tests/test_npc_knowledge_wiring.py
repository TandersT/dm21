"""
Tests for the NPC knowledge tools (DM2-13).

reveal_fact_to_npc writes KnowledgeEntries with source/confidence (dual fact
resolution: existing id, else the pinned content-derived pfact_ id shared
with record_party_fact); propagate_npc_knowledge transfers entries with
confidence decay; npc_knowledge answers both query directions. Tools are
exercised via the underlying functions (`.fn`) with the module-level storage
swapped, following tests/test_knowledge_write_tools.py.
"""

import hashlib
from pathlib import Path

import pytest

from dm20_protocol.storage import DnDStorage


@pytest.fixture
def storage(tmp_path: Path) -> DnDStorage:
    s = DnDStorage(data_dir=tmp_path / "data")
    s.create_campaign(name="NPC Knowledge Test", description="d", dm_name="DM")
    return s


@pytest.fixture
def m(storage: DnDStorage):
    """dm20_protocol.main with its module-level storage swapped for the test."""
    from dm20_protocol import main as m

    original = m.storage
    m.storage = storage
    yield m
    m.storage = original


def _pfact_id(content: str) -> str:
    return f"pfact_{hashlib.sha256(content.strip().lower().encode('utf-8')).hexdigest()[:12]}"


# ── reveal_fact_to_npc ──────────────────────────────────────────────


class TestRevealFactToNpc:
    def test_reveals_with_source_and_confidence(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        result = m.reveal_fact_to_npc.fn(
            npc="Barkeep",
            fact="The mill burned down",
            source="rumor",
            confidence=0.5,
        )
        assert "✅" in result

        npc = storage.get_npc("Barkeep")
        entries = storage.npc_knowledge_tracker.get_npc_knowledge(npc.id)
        assert len(entries) == 1
        assert entries[0].confidence == 0.5
        assert entries[0].source.value == "rumor"

    def test_mints_fact_with_pinned_content_id(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        content = "The mill burned down"
        m.reveal_fact_to_npc.fn(npc="Barkeep", fact=content)
        assert storage.fact_db.get_fact(_pfact_id(content)) is not None

    def test_converges_with_record_party_fact_node(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        content = "Strahd cannot enter consecrated ground"
        m.record_party_fact.fn(
            content=content, category="npc", source="Father Lucian", method="told_by_npc"
        )
        facts_before = len(storage.fact_db.facts)

        m.reveal_fact_to_npc.fn(npc="Barkeep", fact=content)
        assert len(storage.fact_db.facts) == facts_before  # no new node

        npc = storage.get_npc("Barkeep")
        entries = storage.npc_knowledge_tracker.get_npc_knowledge(npc.id)
        assert entries[0].fact_id == _pfact_id(content)

    def test_accepts_existing_fact_id(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        content = "The vistani know the way"
        m.record_party_fact.fn(
            content=content, category="world", source="Madam Eva", method="told_by_npc"
        )
        fact_id = _pfact_id(content)

        result = m.reveal_fact_to_npc.fn(npc="Barkeep", fact=fact_id)
        assert "✅" in result
        npc = storage.get_npc("Barkeep")
        assert storage.npc_knowledge_tracker.npc_knows_fact(npc.id, fact_id)

    def test_told_by_player_routes_through_reveal_to_npc(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        m.reveal_fact_to_npc.fn(
            npc="Barkeep",
            fact="We slew the dragon",
            source="told_by_player",
            source_entity="Aldric",
        )
        npc = storage.get_npc("Barkeep")
        entry = storage.npc_knowledge_tracker.get_npc_knowledge(npc.id)[0]
        assert entry.source.value == "told_by_player"
        assert entry.source_entity == "Aldric"

    def test_already_known_is_noop(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        m.reveal_fact_to_npc.fn(npc="Barkeep", fact="The mill burned down", confidence=1.0)
        result = m.reveal_fact_to_npc.fn(
            npc="Barkeep", fact="The mill burned down", confidence=0.5
        )
        assert "already knows" in result

        npc = storage.get_npc("Barkeep")
        entries = storage.npc_knowledge_tracker.get_npc_knowledge(npc.id)
        assert len(entries) == 1
        assert entries[0].confidence == 1.0  # original entry kept

    def test_unknown_npc_rejected(self, m, storage):
        result = m.reveal_fact_to_npc.fn(npc="Strahd", fact="x")
        assert "not found" in result
        assert "create_npc" in result

    def test_invalid_source_lists_valid_values(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        result = m.reveal_fact_to_npc.fn(npc="Barkeep", fact="x", source="gossip")
        assert "Invalid source 'gossip'" in result
        assert "rumor" in result

    def test_invalid_category_lists_valid_values(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        result = m.reveal_fact_to_npc.fn(npc="Barkeep", fact="x", category="rumor")
        assert "Invalid category 'rumor'" in result
        assert "world" in result

    def test_empty_fact_rejected(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        result = m.reveal_fact_to_npc.fn(npc="Barkeep", fact="   ")
        assert "empty" in result.lower()

    def test_session_defaults_to_current(self, m, storage):
        storage.update_game_state(current_session=3)
        m.create_npc.fn(name="Barkeep")
        m.reveal_fact_to_npc.fn(npc="Barkeep", fact="The mill burned down")
        npc = storage.get_npc("Barkeep")
        entry = storage.npc_knowledge_tracker.get_npc_knowledge(npc.id)[0]
        assert entry.acquired_session == 3

    def test_persists_to_disk(self, m, storage, tmp_path):
        m.create_npc.fn(name="Barkeep")
        m.reveal_fact_to_npc.fn(npc="Barkeep", fact="The mill burned down")
        npc = storage.get_npc("Barkeep")

        fresh = DnDStorage(data_dir=tmp_path / "data")
        fresh.load_campaign("NPC Knowledge Test")
        fact_id = _pfact_id("The mill burned down")
        assert fresh.npc_knowledge_tracker.npc_knows_fact(npc.id, fact_id)
        assert fresh.fact_db.get_fact(fact_id) is not None

    def test_unavailable_without_fact_graph(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        storage._fact_db = None
        result = m.reveal_fact_to_npc.fn(npc="Barkeep", fact="x")
        assert "could not be loaded" in result

    def test_requires_campaign(self, m, storage):
        storage._current_campaign = None
        result = m.reveal_fact_to_npc.fn(npc="Barkeep", fact="x")
        assert "No active campaign" in result
