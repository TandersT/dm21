"""
Tests for the tool-layer fact graph dual-write and sync_facts backfill (DM2-5).

The five play-loop MCP tools (add_event, create_npc, create_location,
create_quest, update_quest) dual-write into the fact graph after each storage
write; sync_facts retroactively replays the journal and sweeps campaign
entities. Tools are exercised via the underlying functions (`.fn`) with the
module-level storage swapped, following tests/test_tool_output_enrichment.py.
"""

from pathlib import Path

import pytest

from dm20_protocol.models import NPC
from dm20_protocol.storage import DnDStorage


@pytest.fixture
def storage(tmp_path: Path) -> DnDStorage:
    s = DnDStorage(data_dir=tmp_path / "data")
    s.create_campaign(name="Dual Write Test", description="d", dm_name="DM")
    return s


@pytest.fixture
def m(storage: DnDStorage):
    """dm20_protocol.main with its module-level storage swapped for the test."""
    from dm20_protocol import main as m

    original = m.storage
    m.storage = storage
    yield m
    m.storage = original


# ── Dual-write hooks ────────────────────────────────────────────────


class TestDualWrite:
    def test_add_event_emits_fact(self, m, storage):
        result = m.add_event.fn(
            event_type="world",
            description="The mists of Barovia close in.",
            session_number=2,
            importance=5,
            tags='["mists"]',
        )
        assert "event" in result.lower()

        event = storage.get_events(limit=1)[0]
        fact = storage.fact_db.get_fact(f"evt_{event.id}")
        assert fact is not None
        assert fact.category.value == "world"
        assert fact.relevance_score == pytest.approx(1.0)
        assert fact.session_number == 2
        assert "mists" in fact.tags

    def test_add_event_records_npc_interaction(self, m, storage):
        m.create_npc.fn(name="Ireena Kolyana", description="The burgomaster's daughter")
        m.add_event.fn(
            event_type="roleplay",
            description="The party spoke with Ireena.",
            session_number=1,
            characters_involved='["Ireena Kolyana", "Thalion"]',
        )

        npc = storage.get_npc("Ireena Kolyana")
        interactions = storage.npc_knowledge_tracker.get_interactions(npc.id)
        assert len(interactions) == 1
        assert interactions[0].interaction_type == "conversation"
        assert interactions[0].player_characters == ["Thalion"]

    def test_create_npc_emits_fact_with_entity_id(self, m, storage):
        m.create_npc.fn(name="Ismark", description="The burgomaster's son")
        npc = storage.get_npc("Ismark")
        fact = storage.fact_db.get_fact(npc.id)
        assert fact is not None
        assert fact.category.value == "npc"
        assert "Ismark" in fact.content

    def test_create_location_emits_fact(self, m, storage):
        m.create_location.fn(
            name="Castle Ravenloft", location_type="castle", description="A brooding fortress"
        )
        loc = storage.get_location("Castle Ravenloft")
        fact = storage.fact_db.get_fact(f"loc_{loc.id}")
        assert fact is not None
        assert fact.category.value == "location"

    def test_create_quest_emits_fact(self, m, storage):
        m.create_quest.fn(title="Find the sunsword", description="Locate the lost blade")
        quest = storage.get_quest("Find the sunsword")
        fact = storage.fact_db.get_fact(f"quest_{quest.id}")
        assert fact is not None
        assert fact.category.value == "quest"
        assert "completed" not in fact.tags

    def test_update_quest_appends_resolution_tag(self, m, storage):
        m.create_quest.fn(title="Find the sunsword", description="Locate the lost blade")
        m.update_quest.fn(title="Find the sunsword", status="completed")

        quest = storage.get_quest("Find the sunsword")
        fact = storage.fact_db.get_fact(f"quest_{quest.id}")
        assert "completed" in fact.tags

    def test_entity_fact_uses_current_session(self, m, storage):
        storage.update_game_state(current_session=4)
        m.create_npc.fn(name="Ismark")
        npc = storage.get_npc("Ismark")
        assert storage.fact_db.get_fact(npc.id).session_number == 4

    def test_facts_persisted_to_disk(self, m, storage, tmp_path):
        m.create_npc.fn(name="Ismark")
        npc = storage.get_npc("Ismark")

        fresh = DnDStorage(data_dir=tmp_path / "data")
        fresh.load_campaign("Dual Write Test")
        assert fresh.fact_db.get_fact(npc.id) is not None


# ── Failure isolation ───────────────────────────────────────────────


class TestFailureIsolation:
    def test_broken_graph_never_breaks_journal_write(self, m, storage):
        storage._fact_db = object()  # poison: not a FactDatabase

        result = m.add_event.fn(event_type="combat", description="A fight broke out.")
        assert "combat" in result.lower()
        assert len(storage.get_events()) == 1

    def test_missing_graph_never_breaks_entity_write(self, m, storage):
        storage._fact_db = None

        result = m.create_npc.fn(name="Ismark")
        assert "Ismark" in result
        assert storage.get_npc("Ismark") is not None


# ── sync_facts backfill ─────────────────────────────────────────────


@pytest.fixture
def populated_storage(storage: DnDStorage) -> DnDStorage:
    """Journal + entities written at the storage layer (no dual-write),
    simulating a campaign that predates the fact graph."""
    from dm20_protocol.models import AdventureEvent, EventType, Location, Quest

    storage.add_npc(NPC(name="Ireena Kolyana", description="The burgomaster's daughter"))
    storage.add_location(
        Location(name="Barovia Village", location_type="village", description="A gloomy village")
    )
    storage.add_quest(Quest(title="Escort Ireena", description="Bring her to Vallaki"))
    storage.add_event(
        AdventureEvent(
            event_type=EventType.ROLEPLAY,
            title="Meeting Ireena",
            description="The party met Ireena at the church.",
            session_number=1,
            characters_involved=["Ireena Kolyana", "Thalion"],
        )
    )
    storage.add_event(
        AdventureEvent(
            event_type=EventType.QUEST,
            title="Quest accepted",
            description="The party agreed to escort Ireena.",
            session_number=1,
        )
    )
    return storage


class TestSyncFacts:
    def test_backfills_empty_fact_db(self, m, populated_storage):
        s = populated_storage
        assert len(s.fact_db.facts) == 0

        result = m.sync_facts.fn()

        npc = s.get_npc("Ireena Kolyana")
        loc = s.get_location("Barovia Village")
        quest = s.get_quest("Escort Ireena")
        assert s.fact_db.get_fact(npc.id) is not None
        assert s.fact_db.get_fact(f"loc_{loc.id}") is not None
        assert s.fact_db.get_fact(f"quest_{quest.id}") is not None
        # 2 events + 3 entities
        assert len(s.fact_db.facts) == 5
        # met-tracking from the replayed journal
        assert len(s.npc_knowledge_tracker.get_interactions(npc.id)) == 1
        assert "5" in result
        assert "interactions recorded: 1" in result

    def test_warns_about_multi_campaign_attribution(self, m, populated_storage):
        result = m.sync_facts.fn()
        assert "global" in result.lower()

    def test_second_run_converges(self, m, populated_storage):
        s = populated_storage
        m.sync_facts.fn()
        fact_count = len(s.fact_db.facts)
        npc = s.get_npc("Ireena Kolyana")

        m.sync_facts.fn()
        assert len(s.fact_db.facts) == fact_count
        assert len(s.npc_knowledge_tracker.get_interactions(npc.id)) == 1

    def test_converges_with_dual_write(self, m, populated_storage):
        """sync after live dual-write produces no duplicates."""
        s = populated_storage
        m.create_npc.fn(name="Ismark", description="The burgomaster's son")
        baseline = len(s.fact_db.facts)

        m.sync_facts.fn()
        # +5 backfilled, Ismark not duplicated
        assert len(s.fact_db.facts) == baseline + 5

    def test_requires_fact_graph(self, m, storage):
        storage._fact_db = None
        result = m.sync_facts.fn()
        assert "unavailable" in result.lower()

    def test_persists_to_disk(self, m, populated_storage, tmp_path):
        m.sync_facts.fn()
        fresh = DnDStorage(data_dir=tmp_path / "data")
        fresh.load_campaign("Dual Write Test")
        assert len(fresh.fact_db.facts) == 5


# ── party_knowledge rewiring ────────────────────────────────────────


class TestPartyKnowledgeRewiring:
    def test_uses_storage_accessors(self, m, storage):
        m.create_npc.fn(name="Ireena Kolyana", description="Cursed by Strahd")
        npc = storage.get_npc("Ireena Kolyana")
        storage.party_knowledge.learn_fact(
            fact_id=npc.id, source="Ismark", method="told_by_npc", session=1
        )

        result = m.party_knowledge.fn(topic="Strahd")
        assert "Ireena" in result

    def test_unavailable_without_fact_graph(self, m, storage):
        storage._fact_db = None
        storage._party_knowledge = None
        result = m.party_knowledge.fn()
        assert "unavailable" in result.lower()
