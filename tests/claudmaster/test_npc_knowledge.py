"""
Comprehensive tests for the NPC knowledge tracking system.

Tests cover all functionality of the NPCKnowledgeTracker class, including:
- Initialization and persistence
- Adding and querying NPC knowledge
- Knowledge propagation between NPCs
- Player interactions tracking
- Error handling and edge cases
"""

import json
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, MagicMock

from dm20_protocol.claudmaster.consistency import (
    Fact,
    FactCategory,
    FactDatabase,
    KnowledgeEntry,
    KnowledgeSource,
    NPCKnowledgeTracker,
    PlayerInteraction,
)


class TestKnowledgeModels:
    """Tests for knowledge-related models."""

    def test_knowledge_source_enum(self):
        """Test all knowledge source enum values."""
        sources = [
            KnowledgeSource.WITNESSED,
            KnowledgeSource.TOLD_BY_PLAYER,
            KnowledgeSource.TOLD_BY_NPC,
            KnowledgeSource.COMMON_KNOWLEDGE,
            KnowledgeSource.PROFESSION,
            KnowledgeSource.RUMOR,
        ]

        assert len(sources) == 6
        assert KnowledgeSource.WITNESSED.value == "witnessed"
        assert KnowledgeSource.TOLD_BY_PLAYER.value == "told_by_player"
        assert KnowledgeSource.TOLD_BY_NPC.value == "told_by_npc"
        assert KnowledgeSource.COMMON_KNOWLEDGE.value == "common_knowledge"
        assert KnowledgeSource.PROFESSION.value == "profession"
        assert KnowledgeSource.RUMOR.value == "rumor"

    def test_knowledge_entry_creation_minimal(self):
        """Test creating a knowledge entry with minimal fields."""
        entry = KnowledgeEntry(
            fact_id="fact_123",
            source=KnowledgeSource.WITNESSED,
            acquired_session=5
        )

        assert entry.fact_id == "fact_123"
        assert entry.source == KnowledgeSource.WITNESSED
        assert entry.acquired_session == 5
        assert entry.confidence == 1.0
        assert entry.source_entity is None

    def test_knowledge_entry_creation_full(self):
        """Test creating a knowledge entry with all fields."""
        timestamp = datetime(2026, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        entry = KnowledgeEntry(
            fact_id="fact_456",
            source=KnowledgeSource.TOLD_BY_PLAYER,
            acquired_session=3,
            acquired_timestamp=timestamp,
            confidence=0.8,
            source_entity="Aragorn"
        )

        assert entry.fact_id == "fact_456"
        assert entry.source == KnowledgeSource.TOLD_BY_PLAYER
        assert entry.acquired_session == 3
        assert entry.acquired_timestamp == timestamp
        assert entry.confidence == 0.8
        assert entry.source_entity == "Aragorn"

    def test_player_interaction_creation_minimal(self):
        """Test creating a player interaction with minimal fields."""
        interaction = PlayerInteraction(
            session_number=2,
            interaction_type="conversation",
            summary="Discussed the weather"
        )

        assert interaction.session_number == 2
        assert interaction.interaction_type == "conversation"
        assert interaction.summary == "Discussed the weather"
        assert interaction.player_characters == []
        assert interaction.location == ""

    def test_player_interaction_creation_full(self):
        """Test creating a player interaction with all fields."""
        timestamp = datetime(2026, 1, 10, 14, 0, 0, tzinfo=timezone.utc)
        interaction = PlayerInteraction(
            session_number=1,
            timestamp=timestamp,
            interaction_type="combat",
            summary="Fought off bandits together",
            player_characters=["Aragorn", "Legolas"],
            location="The Prancing Pony"
        )

        assert interaction.session_number == 1
        assert interaction.timestamp == timestamp
        assert interaction.interaction_type == "combat"
        assert interaction.summary == "Fought off bandits together"
        assert interaction.player_characters == ["Aragorn", "Legolas"]
        assert interaction.location == "The Prancing Pony"


class TestNPCKnowledgeTrackerInitialization:
    """Tests for NPCKnowledgeTracker initialization."""

    def test_init_creates_empty_tracker(self, tmp_path):
        """Test that initialization creates an empty tracker."""
        campaign_path = tmp_path / "test_campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        assert tracker._campaign_path == campaign_path
        assert len(tracker._npc_knowledge) == 0
        assert len(tracker._npc_interactions) == 0
        assert campaign_path.exists()

    def test_init_creates_directory_if_missing(self, tmp_path):
        """Test that initialization creates the campaign directory if it doesn't exist."""
        campaign_path = tmp_path / "nested" / "campaign"
        assert not campaign_path.exists()

        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        assert campaign_path.exists()
        assert tracker._campaign_path == campaign_path

    def test_init_loads_existing_knowledge(self, tmp_path):
        """Test that initialization loads existing knowledge."""
        campaign_path = tmp_path / "test_campaign"
        campaign_path.mkdir()

        # Create a knowledge file
        knowledge_file = campaign_path / "npc_knowledge.json"
        data = {
            "version": "1.0",
            "npc_knowledge": {
                "gandalf": {
                    "known_facts": [
                        {
                            "fact_id": "fact_001",
                            "source": "common_knowledge",
                            "acquired_session": 1,
                            "acquired_timestamp": "2026-01-01T12:00:00+00:00",
                            "confidence": 1.0,
                            "source_entity": None
                        }
                    ],
                    "interactions": []
                }
            },
            "metadata": {
                "last_updated": "2026-01-01T12:00:00+00:00"
            }
        }
        with open(knowledge_file, "w") as f:
            json.dump(data, f)

        # Load the tracker
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        assert "gandalf" in tracker._npc_knowledge
        assert len(tracker._npc_knowledge["gandalf"]) == 1
        assert tracker._npc_knowledge["gandalf"][0].fact_id == "fact_001"


class TestAddAndGetKnowledge:
    """Tests for adding and retrieving NPC knowledge."""

    def test_add_knowledge_basic(self, tmp_path):
        """Test adding basic knowledge to an NPC."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        tracker.add_knowledge(
            npc_id="gandalf",
            fact_id="fact_001",
            source=KnowledgeSource.WITNESSED,
            session=1
        )

        knowledge = tracker.get_npc_knowledge("gandalf")
        assert len(knowledge) == 1
        assert knowledge[0].fact_id == "fact_001"
        assert knowledge[0].source == KnowledgeSource.WITNESSED
        assert knowledge[0].acquired_session == 1
        assert knowledge[0].confidence == 1.0
        assert knowledge[0].source_entity is None

    def test_add_knowledge_with_all_params(self, tmp_path):
        """Test adding knowledge with all parameters."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        tracker.add_knowledge(
            npc_id="frodo",
            fact_id="fact_002",
            source=KnowledgeSource.TOLD_BY_PLAYER,
            session=2,
            confidence=0.7,
            source_entity="Aragorn"
        )

        knowledge = tracker.get_npc_knowledge("frodo")
        assert len(knowledge) == 1
        assert knowledge[0].fact_id == "fact_002"
        assert knowledge[0].confidence == 0.7
        assert knowledge[0].source_entity == "Aragorn"

    def test_add_knowledge_duplicate_skipped(self, tmp_path):
        """Test that adding duplicate knowledge is skipped."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        # Add same knowledge twice
        tracker.add_knowledge(
            npc_id="sam",
            fact_id="fact_003",
            source=KnowledgeSource.WITNESSED,
            session=1
        )
        tracker.add_knowledge(
            npc_id="sam",
            fact_id="fact_003",
            source=KnowledgeSource.TOLD_BY_PLAYER,  # Different source
            session=2
        )

        # Should only have one entry
        knowledge = tracker.get_npc_knowledge("sam")
        assert len(knowledge) == 1
        # Should keep the first entry
        assert knowledge[0].source == KnowledgeSource.WITNESSED
        assert knowledge[0].acquired_session == 1

    def test_get_npc_knowledge_empty(self, tmp_path):
        """Test getting knowledge for NPC with no knowledge."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        knowledge = tracker.get_npc_knowledge("unknown_npc")
        assert len(knowledge) == 0

    def test_get_npc_knowledge_multiple_facts(self, tmp_path):
        """Test getting knowledge for NPC with multiple facts."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        # Add multiple facts
        for i in range(5):
            tracker.add_knowledge(
                npc_id="gimli",
                fact_id=f"fact_{i:03d}",
                source=KnowledgeSource.WITNESSED,
                session=1
            )

        knowledge = tracker.get_npc_knowledge("gimli")
        assert len(knowledge) == 5
        fact_ids = {entry.fact_id for entry in knowledge}
        assert fact_ids == {f"fact_{i:03d}" for i in range(5)}


class TestNPCKnowsFact:
    """Tests for checking if NPC knows a fact."""

    def test_npc_knows_fact_true(self, tmp_path):
        """Test checking if NPC knows a fact (true case)."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        tracker.add_knowledge(
            npc_id="legolas",
            fact_id="fact_123",
            source=KnowledgeSource.WITNESSED,
            session=1
        )

        assert tracker.npc_knows_fact("legolas", "fact_123") is True

    def test_npc_knows_fact_false(self, tmp_path):
        """Test checking if NPC knows a fact (false case)."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        tracker.add_knowledge(
            npc_id="boromir",
            fact_id="fact_456",
            source=KnowledgeSource.WITNESSED,
            session=1
        )

        assert tracker.npc_knows_fact("boromir", "fact_789") is False

    def test_npc_knows_fact_unknown_npc(self, tmp_path):
        """Test checking if unknown NPC knows a fact."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        assert tracker.npc_knows_fact("unknown_npc", "fact_001") is False


class TestRevealToNPC:
    """Tests for revealing information to NPCs."""

    def test_reveal_to_npc(self, tmp_path):
        """Test revealing information to an NPC."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        tracker.reveal_to_npc(
            npc_id="pippin",
            fact_id="fact_secret",
            revealed_by="Gandalf",
            session=3
        )

        knowledge = tracker.get_npc_knowledge("pippin")
        assert len(knowledge) == 1
        assert knowledge[0].fact_id == "fact_secret"
        assert knowledge[0].source == KnowledgeSource.TOLD_BY_PLAYER
        assert knowledge[0].source_entity == "Gandalf"
        assert knowledge[0].acquired_session == 3
        assert knowledge[0].confidence == 1.0

    def test_reveal_to_npc_already_knows(self, tmp_path):
        """Test revealing information NPC already knows is skipped."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        # Add knowledge via witnessed
        tracker.add_knowledge(
            npc_id="merry",
            fact_id="fact_known",
            source=KnowledgeSource.WITNESSED,
            session=1
        )

        # Try to reveal same fact
        tracker.reveal_to_npc(
            npc_id="merry",
            fact_id="fact_known",
            revealed_by="Pippin",
            session=2
        )

        # Should still only have one entry
        knowledge = tracker.get_npc_knowledge("merry")
        assert len(knowledge) == 1
        assert knowledge[0].source == KnowledgeSource.WITNESSED


class TestPropagateKnowledge:
    """Tests for knowledge propagation between NPCs."""

    def test_propagate_knowledge_single_fact(self, tmp_path):
        """Test propagating a single fact from one NPC to another."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        # Give knowledge to source NPC
        tracker.add_knowledge(
            npc_id="aragorn",
            fact_id="fact_001",
            source=KnowledgeSource.WITNESSED,
            session=1
        )

        # Propagate to target NPC
        tracker.propagate_knowledge(
            from_npc="aragorn",
            to_npc="boromir",
            fact_ids=["fact_001"],
            session=2
        )

        # Check target NPC has the knowledge
        knowledge = tracker.get_npc_knowledge("boromir")
        assert len(knowledge) == 1
        assert knowledge[0].fact_id == "fact_001"
        assert knowledge[0].source == KnowledgeSource.TOLD_BY_NPC
        assert knowledge[0].source_entity == "aragorn"
        assert knowledge[0].acquired_session == 2

    def test_propagate_knowledge_multiple_facts(self, tmp_path):
        """Test propagating multiple facts."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        # Give multiple facts to source NPC
        for i in range(3):
            tracker.add_knowledge(
                npc_id="gandalf",
                fact_id=f"fact_{i:03d}",
                source=KnowledgeSource.WITNESSED,
                session=1
            )

        # Propagate all facts
        tracker.propagate_knowledge(
            from_npc="gandalf",
            to_npc="frodo",
            fact_ids=["fact_000", "fact_001", "fact_002"],
            session=2
        )

        # Check target NPC has all facts
        knowledge = tracker.get_npc_knowledge("frodo")
        assert len(knowledge) == 3
        fact_ids = {entry.fact_id for entry in knowledge}
        assert fact_ids == {"fact_000", "fact_001", "fact_002"}

    def test_propagate_knowledge_only_known_facts(self, tmp_path):
        """Test that only facts known by source NPC are propagated."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        # Source NPC only knows fact_001
        tracker.add_knowledge(
            npc_id="sam",
            fact_id="fact_001",
            source=KnowledgeSource.WITNESSED,
            session=1
        )

        # Try to propagate both fact_001 and fact_002
        tracker.propagate_knowledge(
            from_npc="sam",
            to_npc="pippin",
            fact_ids=["fact_001", "fact_002"],  # fact_002 not known
            session=2
        )

        # Target should only receive fact_001
        knowledge = tracker.get_npc_knowledge("pippin")
        assert len(knowledge) == 1
        assert knowledge[0].fact_id == "fact_001"

    def test_propagate_knowledge_skip_already_known(self, tmp_path):
        """Test that facts already known by target are skipped."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        # Both NPCs know fact_001
        tracker.add_knowledge("legolas", "fact_001", KnowledgeSource.WITNESSED, 1)
        tracker.add_knowledge("gimli", "fact_001", KnowledgeSource.WITNESSED, 1)

        # Try to propagate
        tracker.propagate_knowledge(
            from_npc="legolas",
            to_npc="gimli",
            fact_ids=["fact_001"],
            session=2
        )

        # Gimli should still only have one entry
        knowledge = tracker.get_npc_knowledge("gimli")
        assert len(knowledge) == 1
        assert knowledge[0].source == KnowledgeSource.WITNESSED


class TestInteractions:
    """Tests for recording and retrieving player interactions."""

    def test_record_interaction_basic(self, tmp_path):
        """Test recording a basic interaction."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        interaction = PlayerInteraction(
            session_number=1,
            interaction_type="conversation",
            summary="Discussed quest details"
        )

        tracker.record_interaction("gandalf", interaction)

        interactions = tracker.get_interactions("gandalf")
        assert len(interactions) == 1
        assert interactions[0].session_number == 1
        assert interactions[0].interaction_type == "conversation"
        assert interactions[0].summary == "Discussed quest details"

    def test_record_multiple_interactions(self, tmp_path):
        """Test recording multiple interactions for same NPC."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        # Record multiple interactions
        for i in range(3):
            interaction = PlayerInteraction(
                session_number=i + 1,
                interaction_type="conversation",
                summary=f"Interaction {i + 1}"
            )
            tracker.record_interaction("aragorn", interaction)

        interactions = tracker.get_interactions("aragorn")
        assert len(interactions) == 3

    def test_get_interactions_empty(self, tmp_path):
        """Test getting interactions for NPC with no interactions."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        interactions = tracker.get_interactions("unknown_npc")
        assert len(interactions) == 0

    def test_get_interactions_filtered_by_session(self, tmp_path):
        """Test filtering interactions by session."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        # Record interactions in different sessions
        for session in [1, 1, 2, 2, 3]:
            interaction = PlayerInteraction(
                session_number=session,
                interaction_type="conversation",
                summary=f"Session {session}"
            )
            tracker.record_interaction("legolas", interaction)

        # Filter by session 2
        session_2_interactions = tracker.get_interactions("legolas", session=2)
        assert len(session_2_interactions) == 2
        assert all(i.session_number == 2 for i in session_2_interactions)

        # All interactions
        all_interactions = tracker.get_interactions("legolas")
        assert len(all_interactions) == 5


class TestQueryNPCsWhoKnow:
    """Tests for querying which NPCs know a fact."""

    def test_query_npcs_who_know_single(self, tmp_path):
        """Test finding NPCs who know a specific fact."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        tracker.add_knowledge("gandalf", "fact_123", KnowledgeSource.WITNESSED, 1)

        npcs = tracker.query_npcs_who_know("fact_123")
        assert len(npcs) == 1
        assert "gandalf" in npcs

    def test_query_npcs_who_know_multiple(self, tmp_path):
        """Test finding multiple NPCs who know a fact."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        # Multiple NPCs know the same fact
        for npc_id in ["gandalf", "aragorn", "legolas"]:
            tracker.add_knowledge(npc_id, "fact_shared", KnowledgeSource.WITNESSED, 1)

        npcs = tracker.query_npcs_who_know("fact_shared")
        assert len(npcs) == 3
        assert set(npcs) == {"gandalf", "aragorn", "legolas"}

    def test_query_npcs_who_know_none(self, tmp_path):
        """Test querying for fact that no NPCs know."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        tracker.add_knowledge("frodo", "fact_001", KnowledgeSource.WITNESSED, 1)

        npcs = tracker.query_npcs_who_know("fact_999")
        assert len(npcs) == 0


class TestGetKnowledgeContext:
    """Tests for getting full knowledge context."""

    def test_get_knowledge_context_empty(self, tmp_path):
        """Test getting context for NPC with no knowledge."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        context = tracker.get_knowledge_context("unknown_npc")

        assert context["known_facts"] == []
        assert context["knowledge_entries"] == []
        assert context["interactions"] == []
        assert context["fact_count"] == 0
        assert context["interaction_count"] == 0

    def test_get_knowledge_context_with_facts(self, tmp_path):
        """Test getting context with facts resolved from database."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        # Add facts to database
        fact1 = Fact(
            id="fact_001",
            category=FactCategory.EVENT,
            content="Dragon attack",
            session_number=1
        )
        fact2 = Fact(
            id="fact_002",
            category=FactCategory.NPC,
            content="Gandalf is wise",
            session_number=1
        )
        fact_db.add_fact(fact1)
        fact_db.add_fact(fact2)

        # Give knowledge to NPC
        tracker.add_knowledge("frodo", "fact_001", KnowledgeSource.WITNESSED, 1)
        tracker.add_knowledge("frodo", "fact_002", KnowledgeSource.TOLD_BY_PLAYER, 2)

        context = tracker.get_knowledge_context("frodo")

        assert context["fact_count"] == 2
        assert len(context["known_facts"]) == 2
        assert len(context["knowledge_entries"]) == 2

        # Check facts are resolved
        fact_contents = {f.content for f in context["known_facts"]}
        assert "Dragon attack" in fact_contents
        assert "Gandalf is wise" in fact_contents

    def test_get_knowledge_context_with_interactions(self, tmp_path):
        """Test getting context with interactions."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        # Record interactions
        for i in range(3):
            interaction = PlayerInteraction(
                session_number=i + 1,
                interaction_type="conversation",
                summary=f"Talk {i + 1}"
            )
            tracker.record_interaction("sam", interaction)

        context = tracker.get_knowledge_context("sam")

        assert context["interaction_count"] == 3
        assert len(context["interactions"]) == 3

    def test_get_knowledge_context_missing_fact(self, tmp_path):
        """Test context handles missing facts gracefully."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        # Give knowledge to NPC for fact that doesn't exist in database
        tracker.add_knowledge("pippin", "fact_missing", KnowledgeSource.WITNESSED, 1)

        context = tracker.get_knowledge_context("pippin")

        # Should have knowledge entry but no resolved fact
        assert context["fact_count"] == 1
        assert len(context["knowledge_entries"]) == 1
        assert len(context["known_facts"]) == 0


class TestSaveAndLoad:
    """Tests for persistence (save/load)."""

    def test_save_creates_file(self, tmp_path):
        """Test that save creates the knowledge file."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)
        knowledge_file = campaign_path / "npc_knowledge.json"

        assert not knowledge_file.exists()

        tracker.save()

        assert knowledge_file.exists()

    def test_save_and_load_roundtrip(self, tmp_path):
        """Test that knowledge can be saved and loaded correctly."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker1 = NPCKnowledgeTracker(fact_db, campaign_path)

        # Add knowledge
        tracker1.add_knowledge(
            npc_id="gandalf",
            fact_id="fact_001",
            source=KnowledgeSource.WITNESSED,
            session=1,
            confidence=1.0
        )
        tracker1.add_knowledge(
            npc_id="gandalf",
            fact_id="fact_002",
            source=KnowledgeSource.TOLD_BY_PLAYER,
            session=2,
            confidence=0.8,
            source_entity="Frodo"
        )

        # Add interaction
        interaction = PlayerInteraction(
            session_number=1,
            interaction_type="conversation",
            summary="Discussed the ring",
            player_characters=["Frodo"],
            location="Bag End"
        )
        tracker1.record_interaction("gandalf", interaction)

        tracker1.save()

        # Load into new tracker
        tracker2 = NPCKnowledgeTracker(fact_db, campaign_path)

        assert "gandalf" in tracker2._npc_knowledge
        knowledge = tracker2.get_npc_knowledge("gandalf")
        assert len(knowledge) == 2

        # Verify first knowledge entry
        entry1 = next(e for e in knowledge if e.fact_id == "fact_001")
        assert entry1.source == KnowledgeSource.WITNESSED
        assert entry1.acquired_session == 1
        assert entry1.confidence == 1.0

        # Verify second knowledge entry
        entry2 = next(e for e in knowledge if e.fact_id == "fact_002")
        assert entry2.source == KnowledgeSource.TOLD_BY_PLAYER
        assert entry2.source_entity == "Frodo"
        assert entry2.confidence == 0.8

        # Verify interaction
        interactions = tracker2.get_interactions("gandalf")
        assert len(interactions) == 1
        assert interactions[0].summary == "Discussed the ring"
        assert interactions[0].player_characters == ["Frodo"]

    def test_load_with_missing_file(self, tmp_path):
        """Test that load handles missing file gracefully."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        # Should not raise, should create empty tracker
        assert len(tracker._npc_knowledge) == 0
        assert len(tracker._npc_interactions) == 0

    def test_load_with_corrupt_json(self, tmp_path):
        """Test that load handles corrupt JSON gracefully."""
        campaign_path = tmp_path / "campaign"
        campaign_path.mkdir()
        knowledge_file = campaign_path / "npc_knowledge.json"

        # Write corrupt JSON
        with open(knowledge_file, "w") as f:
            f.write("{ this is not valid json }")

        # Should not raise, should start with empty tracker
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)
        assert len(tracker._npc_knowledge) == 0

    def test_load_with_invalid_structure(self, tmp_path):
        """Test that load handles invalid structure gracefully."""
        campaign_path = tmp_path / "campaign"
        campaign_path.mkdir()
        knowledge_file = campaign_path / "npc_knowledge.json"

        # Write valid JSON but invalid structure
        with open(knowledge_file, "w") as f:
            json.dump({"wrong": "structure"}, f)

        # Should not raise, should start with empty tracker
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)
        assert len(tracker._npc_knowledge) == 0

    def test_save_preserves_metadata(self, tmp_path):
        """Test that save includes correct metadata."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        tracker.add_knowledge("aragorn", "fact_001", KnowledgeSource.WITNESSED, 1)
        tracker.save()

        # Read the file directly
        knowledge_file = campaign_path / "npc_knowledge.json"
        with open(knowledge_file, "r") as f:
            data = json.load(f)

        assert data["version"] == "1.0"
        assert "npc_knowledge" in data
        assert "aragorn" in data["npc_knowledge"]
        assert "metadata" in data
        assert "last_updated" in data["metadata"]


class TestEdgeCases:
    """Tests for edge cases and special scenarios."""

    def test_multiple_npcs_independent(self, tmp_path):
        """Test that different NPCs have independent knowledge."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        # Give different knowledge to different NPCs
        tracker.add_knowledge("gandalf", "fact_001", KnowledgeSource.WITNESSED, 1)
        tracker.add_knowledge("saruman", "fact_002", KnowledgeSource.WITNESSED, 1)

        assert tracker.npc_knows_fact("gandalf", "fact_001") is True
        assert tracker.npc_knows_fact("gandalf", "fact_002") is False
        assert tracker.npc_knows_fact("saruman", "fact_001") is False
        assert tracker.npc_knows_fact("saruman", "fact_002") is True

    def test_confidence_levels(self, tmp_path):
        """Test different confidence levels."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        # Test various confidence levels
        confidences = [0.0, 0.5, 1.0, 2.0, 10.0]
        for i, conf in enumerate(confidences):
            tracker.add_knowledge(
                npc_id="tester",
                fact_id=f"fact_{i:03d}",
                source=KnowledgeSource.RUMOR,
                session=1,
                confidence=conf
            )

        knowledge = tracker.get_npc_knowledge("tester")
        assert len(knowledge) == 5
        for i, entry in enumerate(knowledge):
            assert entry.confidence == confidences[i]

    def test_unicode_handling(self, tmp_path):
        """Test handling of unicode in NPC IDs and source entities."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        # Unicode NPC ID
        tracker.add_knowledge(
            npc_id="Éowyn",
            fact_id="fact_001",
            source=KnowledgeSource.TOLD_BY_PLAYER,
            session=1,
            source_entity="Aragørn"
        )

        tracker.save()

        # Reload
        tracker2 = NPCKnowledgeTracker(fact_db, campaign_path)
        knowledge = tracker2.get_npc_knowledge("Éowyn")
        assert len(knowledge) == 1
        assert knowledge[0].source_entity == "Aragørn"

    def test_many_npcs(self, tmp_path):
        """Test handling many NPCs."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        # Create knowledge for 100 NPCs
        for i in range(100):
            tracker.add_knowledge(
                npc_id=f"npc_{i:03d}",
                fact_id=f"fact_{i:03d}",
                source=KnowledgeSource.WITNESSED,
                session=1
            )

        # Each NPC should have exactly one fact
        for i in range(100):
            knowledge = tracker.get_npc_knowledge(f"npc_{i:03d}")
            assert len(knowledge) == 1
            assert knowledge[0].fact_id == f"fact_{i:03d}"

    def test_knowledge_without_interactions(self, tmp_path):
        """Test NPC with knowledge but no interactions."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        tracker.add_knowledge("recluse", "fact_001", KnowledgeSource.WITNESSED, 1)

        context = tracker.get_knowledge_context("recluse")
        assert context["fact_count"] == 1
        assert context["interaction_count"] == 0

    def test_interactions_without_knowledge(self, tmp_path):
        """Test NPC with interactions but no knowledge."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        interaction = PlayerInteraction(
            session_number=1,
            interaction_type="combat",
            summary="Brief skirmish"
        )
        tracker.record_interaction("bandit", interaction)

        context = tracker.get_knowledge_context("bandit")
        assert context["fact_count"] == 0
        assert context["interaction_count"] == 1


class TestConfidenceDecay:
    """Reveal confidence pass-through and propagation decay (DM2-13)."""

    def _tracker(self, tmp_path):
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        return NPCKnowledgeTracker(fact_db, campaign_path)

    def test_reveal_to_npc_confidence_passthrough(self, tmp_path):
        tracker = self._tracker(tmp_path)
        tracker.reveal_to_npc(
            "barkeep", "fact_001", revealed_by="Aldric", session=1, confidence=0.5
        )
        entry = tracker.get_npc_knowledge("barkeep")[0]
        assert entry.confidence == 0.5
        assert entry.source == KnowledgeSource.TOLD_BY_PLAYER
        assert entry.source_entity == "Aldric"

    def test_reveal_to_npc_defaults_to_certain(self, tmp_path):
        tracker = self._tracker(tmp_path)
        tracker.reveal_to_npc("barkeep", "fact_001", revealed_by="Aldric", session=1)
        assert tracker.get_npc_knowledge("barkeep")[0].confidence == 1.0

    def test_propagate_applies_decay_to_sender_confidence(self, tmp_path):
        tracker = self._tracker(tmp_path)
        tracker.add_knowledge(
            "aragorn", "fact_001", KnowledgeSource.WITNESSED, 1, confidence=1.0
        )
        tracker.propagate_knowledge("aragorn", "boromir", ["fact_001"], session=2)
        assert tracker.get_npc_knowledge("boromir")[0].confidence == pytest.approx(0.75)

    def test_propagate_decay_compounds_over_hops(self, tmp_path):
        tracker = self._tracker(tmp_path)
        tracker.add_knowledge("a", "fact_001", KnowledgeSource.WITNESSED, 1, confidence=1.0)
        tracker.propagate_knowledge("a", "b", ["fact_001"], session=2)
        tracker.propagate_knowledge("b", "c", ["fact_001"], session=3)
        assert tracker.get_npc_knowledge("c")[0].confidence == pytest.approx(0.5625)

    def test_propagate_custom_decay(self, tmp_path):
        tracker = self._tracker(tmp_path)
        tracker.add_knowledge("a", "fact_001", KnowledgeSource.WITNESSED, 1, confidence=0.8)
        tracker.propagate_knowledge("a", "b", ["fact_001"], session=2, decay=0.5)
        assert tracker.get_npc_knowledge("b")[0].confidence == pytest.approx(0.4)

    def test_propagate_returns_propagated_fact_ids(self, tmp_path):
        tracker = self._tracker(tmp_path)
        tracker.add_knowledge("a", "fact_001", KnowledgeSource.WITNESSED, 1)
        tracker.add_knowledge("a", "fact_002", KnowledgeSource.WITNESSED, 1)
        result = tracker.propagate_knowledge(
            "a", "b", ["fact_001", "fact_002"], session=2
        )
        assert result == ["fact_001", "fact_002"]

    def test_propagate_return_excludes_skips(self, tmp_path):
        tracker = self._tracker(tmp_path)
        tracker.add_knowledge("a", "fact_001", KnowledgeSource.WITNESSED, 1)
        tracker.add_knowledge("b", "fact_001", KnowledgeSource.WITNESSED, 1)
        # b already knows fact_001; a doesn't know fact_999
        result = tracker.propagate_knowledge(
            "a", "b", ["fact_001", "fact_999"], session=2
        )
        assert result == []
