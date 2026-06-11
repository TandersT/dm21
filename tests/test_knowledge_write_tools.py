"""
Tests for the explicit knowledge write tools (DM2-7).

record_party_fact writes a content-derived fact and marks it party-known via
PartyKnowledge.learn_fact; record_npc_interaction records a PlayerInteraction
on the NPCKnowledgeTracker with strict NPC resolution and an idempotent
ingest_npc upsert. Tools are exercised via the underlying functions (`.fn`)
with the module-level storage swapped, following tests/test_fact_dual_write.py.
"""

from pathlib import Path

import pytest

from dm20_protocol.models import NPC
from dm20_protocol.storage import DnDStorage


@pytest.fixture
def storage(tmp_path: Path) -> DnDStorage:
    s = DnDStorage(data_dir=tmp_path / "data")
    s.create_campaign(name="Knowledge Write Test", description="d", dm_name="DM")
    return s


@pytest.fixture
def m(storage: DnDStorage):
    """dm20_protocol.main with its module-level storage swapped for the test."""
    from dm20_protocol import main as m

    original = m.storage
    m.storage = storage
    yield m
    m.storage = original


# ── record_party_fact ───────────────────────────────────────────────


class TestRecordPartyFact:
    def test_persists_and_queryable_via_party_knowledge(self, m, storage):
        result = m.record_party_fact.fn(
            content="Strahd cannot enter consecrated ground",
            category="npc",
            source="Father Lucian",
            method="told_by_npc",
        )
        assert "✅" in result

        query = m.party_knowledge.fn(topic="Strahd")
        assert "consecrated ground" in query
        assert "Father Lucian" in query

    def test_fact_id_is_content_derived(self, m, storage):
        m.record_party_fact.fn(
            content="The sunsword lies beneath the castle",
            category="item",
            source="Madam Eva",
            method="told_by_npc",
        )
        facts = [f for f in storage.fact_db.facts.values() if f.id.startswith("pfact_")]
        assert len(facts) == 1
        suffix = facts[0].id.removeprefix("pfact_")
        assert len(suffix) == 12
        assert all(c in "0123456789abcdef" for c in suffix)

    def test_identical_repeat_converges(self, m, storage):
        m.record_party_fact.fn(
            content="The mists are Strahd's prison",
            category="world",
            source="Madam Eva",
            method="told_by_npc",
        )
        count = len(storage.fact_db.facts)

        result = m.record_party_fact.fn(
            content="The mists are Strahd's prison",
            category="world",
            source="Madam Eva",
            method="told_by_npc",
        )
        assert "already knows" in result
        assert len(storage.fact_db.facts) == count
        assert storage.party_knowledge.known_fact_count == 1

    def test_case_insensitive_content_converges(self, m, storage):
        m.record_party_fact.fn(
            content="Strahd is a vampire",
            category="npc",
            source="Ismark",
            method="told_by_npc",
        )
        result = m.record_party_fact.fn(
            content="STRAHD IS A VAMPIRE",
            category="npc",
            source="Ireena",
            method="told_by_npc",
        )
        assert "already knows" in result
        assert storage.party_knowledge.known_fact_count == 1

    def test_invalid_category_lists_valid_values(self, m):
        result = m.record_party_fact.fn(
            content="x", category="rumor", source="s", method="observed"
        )
        assert "Invalid category 'rumor'" in result
        assert "world" in result

    def test_invalid_method_lists_valid_values(self, m):
        result = m.record_party_fact.fn(
            content="x", category="world", source="s", method="gossip"
        )
        assert "Invalid method 'gossip'" in result
        assert "told_by_npc" in result

    def test_empty_content_rejected(self, m, storage):
        result = m.record_party_fact.fn(
            content="   ", category="world", source="s", method="observed"
        )
        assert "empty" in result.lower()
        assert len(storage.fact_db.facts) == 0

    def test_session_defaults_to_current(self, m, storage):
        storage.update_game_state(current_session=3)
        m.record_party_fact.fn(
            content="The village is cursed",
            category="location",
            source="observation",
            method="observed",
        )
        entry = storage.party_knowledge.get_all_known_facts()[0]
        assert entry["record"].learned_session == 3
        assert entry["fact"].session_number == 3

    def test_explicit_session_and_metadata(self, m, storage):
        m.record_party_fact.fn(
            content="The bones were stolen",
            category="event",
            source="Father Lucian",
            method="told_by_npc",
            session=2,
            location="The church",
            notes="Told in confidence",
        )
        entry = storage.party_knowledge.get_all_known_facts()[0]
        assert entry["record"].learned_session == 2
        assert entry["record"].location == "The church"
        assert entry["record"].notes == "Told in confidence"

    def test_persists_to_disk(self, m, storage, tmp_path):
        m.record_party_fact.fn(
            content="The Tome of Strahd exists",
            category="item",
            source="Madam Eva",
            method="told_by_npc",
        )

        fresh = DnDStorage(data_dir=tmp_path / "data")
        fresh.load_campaign("Knowledge Write Test")
        assert fresh.party_knowledge.known_fact_count == 1
        assert "Tome" in fresh.party_knowledge.get_all_known_facts()[0]["fact"].content

    def test_unavailable_without_fact_graph(self, m, storage):
        storage._fact_db = None
        storage._party_knowledge = None
        result = m.record_party_fact.fn(
            content="x", category="world", source="s", method="observed"
        )
        assert "could not be loaded" in result

    def test_requires_campaign(self, m, storage):
        storage._current_campaign = None
        result = m.record_party_fact.fn(
            content="x", category="world", source="s", method="observed"
        )
        assert "No active campaign" in result


# ── record_npc_interaction ──────────────────────────────────────────


class TestRecordNpcInteraction:
    def test_records_interaction(self, m, storage):
        m.create_npc.fn(name="Ireena Kolyana", description="The burgomaster's daughter")
        result = m.record_npc_interaction.fn(
            npc="Ireena Kolyana",
            interaction_type="conversation",
            summary="The party properly met Ireena and pledged to protect her",
            player_characters='["Thalion"]',
            location="The burgomaster's mansion",
        )
        assert "✅" in result

        npc = storage.get_npc("Ireena Kolyana")
        interactions = storage.npc_knowledge_tracker.get_interactions(npc.id)
        assert len(interactions) == 1
        assert interactions[0].interaction_type == "conversation"
        assert interactions[0].player_characters == ["Thalion"]
        assert interactions[0].location == "The burgomaster's mansion"

    def test_unknown_npc_is_rejected(self, m, storage):
        result = m.record_npc_interaction.fn(
            npc="Strahd", interaction_type="combat", summary="A duel"
        )
        assert "not found" in result
        assert "create_npc" in result

    def test_upserts_npc_fact_for_pre_dual_write_npc(self, m, storage):
        # Entity written at the storage layer (no dual-write), simulating an
        # NPC that predates the fact graph.
        storage.add_npc(NPC(name="Ismark", description="The burgomaster's son"))
        npc = storage.get_npc("Ismark")
        assert storage.fact_db.get_fact(npc.id) is None

        m.record_npc_interaction.fn(
            npc="Ismark", interaction_type="conversation", summary="Asked for help"
        )
        fact = storage.fact_db.get_fact(npc.id)
        assert fact is not None
        assert fact.category.value == "npc"

    def test_exact_repeat_same_session_is_noop(self, m, storage):
        m.create_npc.fn(name="Ismark")
        npc = storage.get_npc("Ismark")
        m.record_npc_interaction.fn(
            npc="Ismark", interaction_type="conversation", summary="Asked for help", session=1
        )
        result = m.record_npc_interaction.fn(
            npc="Ismark", interaction_type="conversation", summary="Asked for help", session=1
        )
        assert "already recorded" in result
        assert len(storage.npc_knowledge_tracker.get_interactions(npc.id)) == 1

    def test_same_summary_later_session_records_again(self, m, storage):
        m.create_npc.fn(name="Ismark")
        npc = storage.get_npc("Ismark")
        m.record_npc_interaction.fn(
            npc="Ismark", interaction_type="conversation", summary="Asked for help", session=1
        )
        result = m.record_npc_interaction.fn(
            npc="Ismark", interaction_type="conversation", summary="Asked for help", session=2
        )
        assert "✅" in result
        assert len(storage.npc_knowledge_tracker.get_interactions(npc.id)) == 2

    def test_resolves_npc_by_id_and_case_insensitive_name(self, m, storage):
        m.create_npc.fn(name="Ireena Kolyana")
        npc = storage.get_npc("Ireena Kolyana")

        result = m.record_npc_interaction.fn(
            npc=npc.id, interaction_type="conversation", summary="By id"
        )
        assert "✅" in result
        result = m.record_npc_interaction.fn(
            npc="ireena kolyana", interaction_type="conversation", summary="By name"
        )
        assert "✅" in result
        assert len(storage.npc_knowledge_tracker.get_interactions(npc.id)) == 2

    def test_session_defaults_to_current(self, m, storage):
        storage.update_game_state(current_session=4)
        m.create_npc.fn(name="Ismark")
        npc = storage.get_npc("Ismark")
        m.record_npc_interaction.fn(
            npc="Ismark", interaction_type="trade", summary="Bought supplies"
        )
        assert storage.npc_knowledge_tracker.get_interactions(npc.id)[0].session_number == 4

    def test_empty_summary_rejected(self, m, storage):
        m.create_npc.fn(name="Ismark")
        npc = storage.get_npc("Ismark")
        result = m.record_npc_interaction.fn(
            npc="Ismark", interaction_type="conversation", summary="  "
        )
        assert "empty" in result.lower()
        assert len(storage.npc_knowledge_tracker.get_interactions(npc.id)) == 0

    def test_persists_to_disk(self, m, storage, tmp_path):
        m.create_npc.fn(name="Ismark")
        npc = storage.get_npc("Ismark")
        m.record_npc_interaction.fn(
            npc="Ismark", interaction_type="conversation", summary="Asked for help"
        )

        fresh = DnDStorage(data_dir=tmp_path / "data")
        fresh.load_campaign("Knowledge Write Test")
        assert len(fresh.npc_knowledge_tracker.get_interactions(npc.id)) == 1

    def test_unavailable_without_fact_graph(self, m, storage):
        m.create_npc.fn(name="Ismark")
        storage._fact_db = None
        result = m.record_npc_interaction.fn(
            npc="Ismark", interaction_type="conversation", summary="x"
        )
        assert "could not be loaded" in result

    def test_requires_campaign(self, m, storage):
        storage._current_campaign = None
        result = m.record_npc_interaction.fn(
            npc="Ismark", interaction_type="conversation", summary="x"
        )
        assert "No active campaign" in result
