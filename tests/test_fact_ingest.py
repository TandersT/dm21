"""
Tests for the FactIngest adapter (DM2-5).

FactIngest is the single ingestion pipeline from journal/entity stores into
the fact graph (FactDatabase + NPCKnowledgeTracker). It is used both live
(dual-write from the MCP tools) and retroactively (sync_facts backfill), so
the core properties under test are deterministic ids, idempotent convergence,
and merge-preserve upsert semantics.
"""

from datetime import datetime
from pathlib import Path

import pytest

from dm20_protocol.claudmaster.consistency.fact_database import FactDatabase
from dm20_protocol.claudmaster.consistency.models import FactCategory
from dm20_protocol.claudmaster.consistency.npc_knowledge import NPCKnowledgeTracker
from dm20_protocol.consistency.fact_ingest import FactIngest
from dm20_protocol.models import AdventureEvent, EventType, Location, NPC, Quest


@pytest.fixture
def campaign_dir(tmp_path: Path) -> Path:
    d = tmp_path / "campaign"
    d.mkdir()
    return d


@pytest.fixture
def fact_db(campaign_dir: Path) -> FactDatabase:
    return FactDatabase(campaign_dir)


@pytest.fixture
def npc_tracker(fact_db: FactDatabase, campaign_dir: Path) -> NPCKnowledgeTracker:
    return NPCKnowledgeTracker(fact_db, campaign_dir)


@pytest.fixture
def ingest(fact_db: FactDatabase, npc_tracker: NPCKnowledgeTracker) -> FactIngest:
    return FactIngest(fact_db, npc_tracker)


def make_event(**overrides) -> AdventureEvent:
    defaults = dict(
        event_type=EventType.ROLEPLAY,
        title="A tense meeting",
        description="The party met Ireena at the church.",
        session_number=3,
        characters_involved=[],
        location="Barovia Village",
        importance=4,
        tags=["story"],
    )
    defaults.update(overrides)
    return AdventureEvent(**defaults)


# ── Event ingestion ─────────────────────────────────────────────────


class TestIngestEvent:
    def test_basic_mapping(self, ingest: FactIngest, fact_db: FactDatabase):
        event = make_event()
        fact_id = ingest.ingest_event(event)

        assert fact_id == f"evt_{event.id}"
        fact = fact_db.get_fact(fact_id)
        assert fact is not None
        assert fact.category == FactCategory.EVENT
        assert fact.content == "The party met Ireena at the church."
        assert fact.relevance_score == pytest.approx(4 / 5)
        assert fact.session_number == 3
        assert fact.tags == ["story"]
        assert fact.source == "adventure_log"
        assert fact.timestamp == event.timestamp

    @pytest.mark.parametrize(
        "event_type,expected",
        [
            (EventType.QUEST, FactCategory.QUEST),
            (EventType.WORLD, FactCategory.WORLD),
            (EventType.COMBAT, FactCategory.EVENT),
            (EventType.EXPLORATION, FactCategory.EVENT),
            (EventType.SESSION, FactCategory.EVENT),
        ],
    )
    def test_category_mapping(self, ingest, fact_db, event_type, expected):
        event = make_event(event_type=event_type)
        fact_id = ingest.ingest_event(event)
        assert fact_db.get_fact(fact_id).category == expected

    def test_session_fallback(self, ingest, fact_db):
        event = make_event(session_number=None)
        fact_id = ingest.ingest_event(event, default_session=7)
        assert fact_db.get_fact(fact_id).session_number == 7

    def test_reingest_converges(self, ingest, fact_db):
        event = make_event()
        ingest.ingest_event(event)
        ingest.ingest_event(event)
        assert len(fact_db.facts) == 1


# ── Entity ingestion ────────────────────────────────────────────────


class TestIngestEntities:
    def test_npc_fact_id_equals_entity_id(self, ingest, fact_db):
        npc = NPC(name="Ireena Kolyana", description="The burgomaster's daughter")
        fact_id = ingest.ingest_npc(npc, session=2)

        # id equality is required by SessionRecapGenerator._get_npc_reminders
        assert fact_id == npc.id
        fact = fact_db.get_fact(npc.id)
        assert fact.category == FactCategory.NPC
        assert "Ireena Kolyana" in fact.content
        assert "burgomaster's daughter" in fact.content
        assert fact.session_number == 2
        assert fact.source == "campaign"

    def test_npc_without_description(self, ingest, fact_db):
        npc = NPC(name="Mysterious Stranger")
        fact_id = ingest.ingest_npc(npc)
        assert fact_db.get_fact(fact_id).content == "Mysterious Stranger"

    def test_location(self, ingest, fact_db):
        loc = Location(
            name="Castle Ravenloft", location_type="castle", description="A brooding fortress"
        )
        fact_id = ingest.ingest_location(loc, session=2)
        assert fact_id == f"loc_{loc.id}"
        fact = fact_db.get_fact(fact_id)
        assert fact.category == FactCategory.LOCATION
        assert "Castle Ravenloft" in fact.content
        assert "castle" in fact.content
        assert "brooding fortress" in fact.content

    def test_quest_active_has_no_resolution_tag(self, ingest, fact_db):
        quest = Quest(title="Find the sunsword", description="Locate the lost blade")
        fact_id = ingest.ingest_quest(quest, session=2)
        assert fact_id == f"quest_{quest.id}"
        fact = fact_db.get_fact(fact_id)
        assert fact.category == FactCategory.QUEST
        assert "Find the sunsword" in fact.content
        assert "completed" not in fact.tags
        assert "failed" not in fact.tags

    @pytest.mark.parametrize("status", ["completed", "failed"])
    def test_quest_resolution_tag(self, ingest, fact_db, status):
        quest = Quest(title="Q", description="D", status=status)
        fact_id = ingest.ingest_quest(quest)
        assert status in fact_db.get_fact(fact_id).tags

    def test_quest_on_hold_stays_unresolved(self, ingest, fact_db):
        quest = Quest(title="Q", description="D", status="on_hold")
        fact_id = ingest.ingest_quest(quest)
        fact = fact_db.get_fact(fact_id)
        assert "completed" not in fact.tags
        assert "failed" not in fact.tags

    def test_entity_reingest_converges(self, ingest, fact_db):
        npc = NPC(name="Ismark")
        quest = Quest(title="Q", description="D")
        for _ in range(2):
            ingest.ingest_npc(npc)
            ingest.ingest_quest(quest)
        assert len(fact_db.facts) == 2


# ── Merge-preserve upsert semantics ─────────────────────────────────


class TestMergePreserve:
    def test_party_known_tag_survives_reingest(self, ingest, fact_db):
        npc = NPC(name="Ireena")
        fact_id = ingest.ingest_npc(npc)
        fact_db.get_fact(fact_id).tags.append("party_known")

        ingest.ingest_npc(npc)
        assert "party_known" in fact_db.get_fact(fact_id).tags

    def test_related_facts_survive_reingest(self, ingest, fact_db):
        event = make_event()
        npc = NPC(name="Ireena")
        evt_id = ingest.ingest_event(event)
        npc_id = ingest.ingest_npc(npc)
        fact_db.link_facts(evt_id, npc_id)

        ingest.ingest_event(event)
        ingest.ingest_npc(npc)
        assert npc_id in fact_db.get_fact(evt_id).related_facts
        assert evt_id in fact_db.get_fact(npc_id).related_facts

    def test_session_and_timestamp_preserved_on_reingest(self, ingest, fact_db):
        npc = NPC(name="Ireena")
        fact_id = ingest.ingest_npc(npc, session=3)
        original_ts = fact_db.get_fact(fact_id).timestamp

        ingest.ingest_npc(npc, session=9)
        fact = fact_db.get_fact(fact_id)
        assert fact.session_number == 3
        assert fact.timestamp == original_ts

    def test_content_updates_on_reingest(self, ingest, fact_db):
        npc = NPC(name="Ireena")
        fact_id = ingest.ingest_npc(npc)
        npc.description = "Now a vampire hunter"
        ingest.ingest_npc(npc)
        assert "vampire hunter" in fact_db.get_fact(fact_id).content

    def test_event_relevance_updates_on_reingest(self, ingest, fact_db):
        event = make_event(importance=2)
        fact_id = ingest.ingest_event(event)
        event.importance = 5
        ingest.ingest_event(event)
        assert fact_db.get_fact(fact_id).relevance_score == pytest.approx(1.0)

    def test_quest_resolution_tag_added_then_removed(self, ingest, fact_db):
        quest = Quest(title="Q", description="D", status="active")
        fact_id = ingest.ingest_quest(quest)
        fact_db.get_fact(fact_id).tags.append("party_known")

        quest.status = "completed"
        ingest.ingest_quest(quest)
        assert "completed" in fact_db.get_fact(fact_id).tags

        quest.status = "active"
        ingest.ingest_quest(quest)
        fact = fact_db.get_fact(fact_id)
        assert "completed" not in fact.tags
        # foreign tags survive the managed-tag refresh
        assert "party_known" in fact.tags


# ── Met-tracking (PlayerInteraction) ────────────────────────────────


class TestMetTracking:
    def test_matching_npc_records_interaction(self, ingest, npc_tracker):
        npc = NPC(name="Ireena Kolyana")
        event = make_event(characters_involved=["Thalion", "Ireena Kolyana"])

        ingest.ingest_event(event, npcs_by_name={npc.name: npc})

        interactions = npc_tracker.get_interactions(npc.id)
        assert len(interactions) == 1
        interaction = interactions[0]
        assert interaction.interaction_type == "conversation"
        assert interaction.session_number == 3
        assert interaction.location == "Barovia Village"
        assert interaction.player_characters == ["Thalion"]
        assert f"evt_{event.id}" in interaction.summary

    def test_match_is_case_insensitive(self, ingest, npc_tracker):
        npc = NPC(name="Ireena Kolyana")
        event = make_event(characters_involved=["ireena kolyana"])
        ingest.ingest_event(event, npcs_by_name={npc.name: npc})
        assert len(npc_tracker.get_interactions(npc.id)) == 1

    @pytest.mark.parametrize(
        "event_type,expected",
        [
            (EventType.COMBAT, "combat"),
            (EventType.ROLEPLAY, "conversation"),
            (EventType.SOCIAL, "conversation"),
            (EventType.EXPLORATION, "exploration"),
        ],
    )
    def test_interaction_type_mapping(self, ingest, npc_tracker, event_type, expected):
        npc = NPC(name="Ireena")
        event = make_event(event_type=event_type, characters_involved=["Ireena"])
        ingest.ingest_event(event, npcs_by_name={npc.name: npc})
        assert npc_tracker.get_interactions(npc.id)[0].interaction_type == expected

    def test_reingest_does_not_duplicate_interactions(self, ingest, npc_tracker):
        npc = NPC(name="Ireena")
        event = make_event(characters_involved=["Ireena"])
        lookup = {npc.name: npc}
        ingest.ingest_event(event, npcs_by_name=lookup)
        ingest.ingest_event(event, npcs_by_name=lookup)
        assert len(npc_tracker.get_interactions(npc.id)) == 1

    def test_unmatched_names_record_nothing(self, ingest, npc_tracker):
        npc = NPC(name="Ireena")
        event = make_event(characters_involved=["Somebody Else"])
        ingest.ingest_event(event, npcs_by_name={npc.name: npc})
        assert npc_tracker.get_interactions(npc.id) == []

    def test_no_tracker_does_not_crash(self, fact_db):
        ingest = FactIngest(fact_db, npc_tracker=None)
        event = make_event(characters_involved=["Ireena"])
        ingest.ingest_event(event, npcs_by_name={"Ireena": NPC(name="Ireena")})
        assert fact_db.get_fact(f"evt_{event.id}") is not None


# ── Persistence ─────────────────────────────────────────────────────


class TestSave:
    def test_save_persists_both_stores(self, ingest, campaign_dir):
        npc = NPC(name="Ireena")
        event = make_event(characters_involved=["Ireena"])
        ingest.ingest_npc(npc)
        ingest.ingest_event(event, npcs_by_name={npc.name: npc})
        ingest.save()

        reloaded_db = FactDatabase(campaign_dir)
        assert reloaded_db.get_fact(npc.id) is not None
        assert reloaded_db.get_fact(f"evt_{event.id}") is not None

        reloaded_tracker = NPCKnowledgeTracker(reloaded_db, campaign_dir)
        assert len(reloaded_tracker.get_interactions(npc.id)) == 1

    def test_save_without_tracker(self, fact_db, campaign_dir):
        ingest = FactIngest(fact_db)
        ingest.ingest_npc(NPC(name="Ireena"))
        ingest.save()
        assert (campaign_dir / "fact_database.json").exists()
