"""
Comprehensive tests for the SessionRecapGenerator.

Tests cover:
- Recap generation with various lengths and styles
- Key event extraction
- Story thread identification
- Quest summaries
- NPC reminders
- Edge cases and empty databases
"""

import pytest
from datetime import datetime
from pathlib import Path

from dm20_protocol.claudmaster.continuity import (
    SessionRecapGenerator,
    SessionRecap,
    StoryThread,
    QuestSummary,
)
from dm20_protocol.claudmaster.consistency import (
    Fact,
    FactCategory,
    FactDatabase,
)
from dm20_protocol.claudmaster.consistency.npc_knowledge import NPCKnowledgeTracker
from dm20_protocol.claudmaster.consistency.models import (
    PlayerInteraction,
    KnowledgeSource,
)
from dm20_protocol.claudmaster.consistency.timeline import TimelineTracker


class TestSessionRecapGeneratorInitialization:
    """Tests for SessionRecapGenerator initialization."""

    def test_init_with_fact_database_only(self, tmp_path):
        """Test initialization with only fact database."""
        fact_db = FactDatabase(tmp_path / "campaign")
        generator = SessionRecapGenerator(fact_db)

        assert generator.facts == fact_db
        assert generator.npc_tracker is None
        assert generator.timeline is None

    def test_init_with_all_components(self, tmp_path):
        """Test initialization with all optional components."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        npc_tracker = NPCKnowledgeTracker(fact_db, campaign_path)
        timeline = TimelineTracker(campaign_path)

        generator = SessionRecapGenerator(fact_db, npc_tracker, timeline)

        assert generator.facts == fact_db
        assert generator.npc_tracker == npc_tracker
        assert generator.timeline == timeline


class TestGetKeyEvents:
    """Tests for get_key_events() method."""

    def test_get_key_events_returns_events(self, tmp_path):
        """Test that get_key_events returns event facts."""
        fact_db = FactDatabase(tmp_path / "campaign")

        # Add some events
        fact_db.add_fact(Fact(
            category=FactCategory.EVENT,
            content="Party defeated the dragon",
            session_number=1,
            relevance_score=2.0
        ))
        fact_db.add_fact(Fact(
            category=FactCategory.EVENT,
            content="Found the magic sword",
            session_number=1,
            relevance_score=1.5
        ))

        generator = SessionRecapGenerator(fact_db)
        events = generator.get_key_events(session_number=1)

        assert len(events) == 2
        # Should be sorted by relevance
        assert events[0].content == "Party defeated the dragon"
        assert events[1].content == "Found the magic sword"

    def test_get_key_events_filters_by_session(self, tmp_path):
        """Test that get_key_events filters by session number."""
        fact_db = FactDatabase(tmp_path / "campaign")

        fact_db.add_fact(Fact(
            category=FactCategory.EVENT,
            content="Session 1 event",
            session_number=1,
            relevance_score=1.0
        ))
        fact_db.add_fact(Fact(
            category=FactCategory.EVENT,
            content="Session 2 event",
            session_number=2,
            relevance_score=1.0
        ))

        generator = SessionRecapGenerator(fact_db)
        events = generator.get_key_events(session_number=1)

        assert len(events) == 1
        assert events[0].content == "Session 1 event"

    def test_get_key_events_respects_limit(self, tmp_path):
        """Test that get_key_events respects the limit parameter."""
        fact_db = FactDatabase(tmp_path / "campaign")

        # Add 10 events
        for i in range(10):
            fact_db.add_fact(Fact(
                category=FactCategory.EVENT,
                content=f"Event {i}",
                session_number=1,
                relevance_score=float(i)
            ))

        generator = SessionRecapGenerator(fact_db)
        events = generator.get_key_events(session_number=1, limit=3)

        assert len(events) == 3

    def test_get_key_events_returns_empty_when_none(self, tmp_path):
        """Test that get_key_events returns empty list when no events."""
        fact_db = FactDatabase(tmp_path / "campaign")
        generator = SessionRecapGenerator(fact_db)

        events = generator.get_key_events(session_number=1)

        assert events == []

    def test_get_key_events_ignores_non_events(self, tmp_path):
        """Test that get_key_events ignores non-event facts."""
        fact_db = FactDatabase(tmp_path / "campaign")

        fact_db.add_fact(Fact(
            category=FactCategory.NPC,
            content="Gandalf",
            session_number=1
        ))
        fact_db.add_fact(Fact(
            category=FactCategory.LOCATION,
            content="The Shire",
            session_number=1
        ))

        generator = SessionRecapGenerator(fact_db)
        events = generator.get_key_events(session_number=1)

        assert len(events) == 0


class TestGetActiveThreads:
    """Tests for get_active_threads() method."""

    def test_get_active_threads_finds_unresolved_quests(self, tmp_path):
        """Test that get_active_threads finds unresolved quest facts."""
        fact_db = FactDatabase(tmp_path / "campaign")

        fact_db.add_fact(Fact(
            id="quest_1",
            category=FactCategory.QUEST,
            content="Find the lost artifact",
            session_number=1,
            relevance_score=1.5
        ))

        generator = SessionRecapGenerator(fact_db)
        threads = generator.get_active_threads(session_number=1)

        assert len(threads) == 1
        assert threads[0].description == "Find the lost artifact"
        assert threads[0].started_session == 1

    def test_get_active_threads_filters_completed_quests(self, tmp_path):
        """Test that get_active_threads filters out completed quests."""
        fact_db = FactDatabase(tmp_path / "campaign")

        fact_db.add_fact(Fact(
            category=FactCategory.QUEST,
            content="Active quest",
            session_number=1,
            tags=[]
        ))
        fact_db.add_fact(Fact(
            category=FactCategory.QUEST,
            content="Completed quest",
            session_number=1,
            tags=["completed"]
        ))
        fact_db.add_fact(Fact(
            category=FactCategory.QUEST,
            content="Failed quest",
            session_number=1,
            tags=["failed"]
        ))

        generator = SessionRecapGenerator(fact_db)
        threads = generator.get_active_threads(session_number=1)

        assert len(threads) == 1
        assert threads[0].description == "Active quest"

    def test_get_active_threads_groups_related_quests(self, tmp_path):
        """Test that get_active_threads groups related quest facts."""
        fact_db = FactDatabase(tmp_path / "campaign")

        quest1_id = fact_db.add_fact(Fact(
            category=FactCategory.QUEST,
            content="Quest part 1",
            session_number=1,
            relevance_score=1.0,
            related_facts=["related_fact"]
        ))
        quest2_id = fact_db.add_fact(Fact(
            category=FactCategory.QUEST,
            content="Quest part 2",
            session_number=2,
            relevance_score=1.5,
            related_facts=["related_fact"]
        ))

        generator = SessionRecapGenerator(fact_db)
        threads = generator.get_active_threads(session_number=2)

        # Should group into one thread
        assert len(threads) == 1
        assert "Quest part 1" in threads[0].description
        assert "Quest part 2" in threads[0].description

    def test_get_active_threads_sorts_by_importance(self, tmp_path):
        """Test that threads are sorted by importance (relevance)."""
        fact_db = FactDatabase(tmp_path / "campaign")

        fact_db.add_fact(Fact(
            category=FactCategory.QUEST,
            content="Low priority quest",
            session_number=1,
            relevance_score=0.5
        ))
        fact_db.add_fact(Fact(
            category=FactCategory.QUEST,
            content="High priority quest",
            session_number=1,
            relevance_score=2.0
        ))

        generator = SessionRecapGenerator(fact_db)
        threads = generator.get_active_threads(session_number=1)

        assert threads[0].description == "High priority quest"
        assert threads[1].description == "Low priority quest"

    def test_get_active_threads_filters_by_session(self, tmp_path):
        """Test that only quests up to the specified session are included."""
        fact_db = FactDatabase(tmp_path / "campaign")

        fact_db.add_fact(Fact(
            category=FactCategory.QUEST,
            content="Quest from session 1",
            session_number=1
        ))
        fact_db.add_fact(Fact(
            category=FactCategory.QUEST,
            content="Quest from session 3",
            session_number=3
        ))

        generator = SessionRecapGenerator(fact_db)
        threads = generator.get_active_threads(session_number=2)

        # Should only include session 1 quest
        assert len(threads) == 1
        assert threads[0].description == "Quest from session 1"


class TestGetSituationSummary:
    """Tests for get_situation_summary() method."""

    def test_get_situation_summary_uses_location(self, tmp_path):
        """Test that situation summary uses location facts."""
        fact_db = FactDatabase(tmp_path / "campaign")

        fact_db.add_fact(Fact(
            category=FactCategory.LOCATION,
            content="The Dark Forest",
            session_number=1,
            relevance_score=1.0
        ))

        generator = SessionRecapGenerator(fact_db)
        summary = generator.get_situation_summary(session_number=1)

        assert "The Dark Forest" in summary

    def test_get_situation_summary_falls_back_to_event(self, tmp_path):
        """Test that situation summary falls back to events if no location."""
        fact_db = FactDatabase(tmp_path / "campaign")

        fact_db.add_fact(Fact(
            category=FactCategory.EVENT,
            content="Party camping by the river",
            session_number=1,
            relevance_score=1.0
        ))

        generator = SessionRecapGenerator(fact_db)
        summary = generator.get_situation_summary(session_number=1)

        assert "Party camping by the river" in summary

    def test_get_situation_summary_default_when_no_facts(self, tmp_path):
        """Test that situation summary provides default when no facts."""
        fact_db = FactDatabase(tmp_path / "campaign")
        generator = SessionRecapGenerator(fact_db)

        summary = generator.get_situation_summary(session_number=1)

        assert "unclear" in summary.lower()


class TestBuildPreviouslyOn:
    """Tests for _build_previously_on() method."""

    def test_previously_on_brief_narrative(self, tmp_path):
        """Test brief narrative style recap."""
        fact_db = FactDatabase(tmp_path / "campaign")
        events = [
            Fact(
                category=FactCategory.EVENT,
                content="Party defeated the dragon",
                session_number=1
            ),
            Fact(
                category=FactCategory.EVENT,
                content="Found the treasure",
                session_number=1
            ),
        ]

        generator = SessionRecapGenerator(fact_db)
        recap = generator._build_previously_on(events, length="brief", style="narrative")

        assert "Party defeated the dragon" in recap
        assert "Found the treasure" in recap
        assert len(recap) < 200  # Should be brief

    def test_previously_on_bullet_style(self, tmp_path):
        """Test bullet point style recap."""
        fact_db = FactDatabase(tmp_path / "campaign")
        events = [
            Fact(
                category=FactCategory.EVENT,
                content="Event 1",
                session_number=1
            ),
            Fact(
                category=FactCategory.EVENT,
                content="Event 2",
                session_number=1
            ),
        ]

        generator = SessionRecapGenerator(fact_db)
        recap = generator._build_previously_on(events, length="standard", style="bullet")

        assert recap.startswith("-")
        assert "Event 1" in recap
        assert "Event 2" in recap

    def test_previously_on_mixed_style(self, tmp_path):
        """Test mixed style recap (narrative intro + bullets)."""
        fact_db = FactDatabase(tmp_path / "campaign")
        events = [
            Fact(
                category=FactCategory.EVENT,
                content="Event 1",
                session_number=1
            ),
        ]

        generator = SessionRecapGenerator(fact_db)
        recap = generator._build_previously_on(events, length="standard", style="mixed")

        assert "Previously" in recap
        assert "-" in recap  # Should have bullets

    def test_previously_on_detailed_narrative(self, tmp_path):
        """Test detailed narrative style recap."""
        fact_db = FactDatabase(tmp_path / "campaign")
        events = [
            Fact(
                category=FactCategory.EVENT,
                content=f"The party discovered a hidden treasure chamber filled with ancient artifacts from a long-lost civilization",
                session_number=1
            ),
            Fact(
                category=FactCategory.EVENT,
                content=f"They encountered a mysterious guardian who challenged them to prove their worth through a test of wisdom",
                session_number=1
            ),
            Fact(
                category=FactCategory.EVENT,
                content=f"After successfully completing the trial, the guardian revealed crucial information about the quest",
                session_number=1
            ),
        ]

        generator = SessionRecapGenerator(fact_db)
        recap = generator._build_previously_on(events, length="detailed", style="narrative")

        assert "When we last left" in recap
        assert len(recap) > 200  # Should be detailed

    def test_previously_on_empty_events(self, tmp_path):
        """Test recap with no events."""
        fact_db = FactDatabase(tmp_path / "campaign")
        generator = SessionRecapGenerator(fact_db)

        recap = generator._build_previously_on([], length="standard", style="narrative")

        assert "continues" in recap.lower()


class TestGenerateRecap:
    """Tests for generate_recap() method (integration)."""

    def test_generate_recap_complete(self, tmp_path):
        """Test generating a complete recap with all components."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)

        # Add various facts
        fact_db.add_fact(Fact(
            category=FactCategory.EVENT,
            content="Party entered the dungeon",
            session_number=1,
            relevance_score=2.0
        ))
        fact_db.add_fact(Fact(
            category=FactCategory.QUEST,
            content="Find the ancient artifact",
            session_number=1,
            relevance_score=1.5
        ))
        fact_db.add_fact(Fact(
            category=FactCategory.LOCATION,
            content="Dark Cavern",
            session_number=1,
            relevance_score=1.0
        ))

        generator = SessionRecapGenerator(fact_db)
        recap = generator.generate_recap(session_number=1)

        assert isinstance(recap, SessionRecap)
        assert recap.previously_on != ""
        assert len(recap.key_events) > 0
        assert len(recap.active_quests) > 0
        assert recap.current_situation != ""

    def test_generate_recap_brief_length(self, tmp_path):
        """Test generating a brief recap."""
        fact_db = FactDatabase(tmp_path / "campaign")

        fact_db.add_fact(Fact(
            category=FactCategory.EVENT,
            content="Event 1",
            session_number=1,
            relevance_score=1.0
        ))

        generator = SessionRecapGenerator(fact_db)
        recap = generator.generate_recap(session_number=1, length="brief")

        # Brief should be concise
        assert len(recap.previously_on) < 200

    def test_generate_recap_detailed_length(self, tmp_path):
        """Test generating a detailed recap."""
        fact_db = FactDatabase(tmp_path / "campaign")

        for i in range(5):
            fact_db.add_fact(Fact(
                category=FactCategory.EVENT,
                content=f"Event {i}",
                session_number=1,
                relevance_score=float(i)
            ))

        generator = SessionRecapGenerator(fact_db)
        recap = generator.generate_recap(session_number=1, length="detailed")

        # Detailed should be longer
        assert len(recap.previously_on) > 100

    def test_generate_recap_empty_database(self, tmp_path):
        """Test generating recap with empty database."""
        fact_db = FactDatabase(tmp_path / "campaign")
        generator = SessionRecapGenerator(fact_db)

        recap = generator.generate_recap(session_number=1)

        assert isinstance(recap, SessionRecap)
        assert recap.key_events == []
        assert recap.active_quests == []

    def test_generate_recap_with_npc_tracker(self, tmp_path):
        """Test generating recap with NPC tracker integration."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        npc_tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        # Add NPC fact
        npc_id = fact_db.add_fact(Fact(
            category=FactCategory.NPC,
            content="Gandalf the Wizard",
            session_number=1
        ))

        # Record interaction
        interaction = PlayerInteraction(
            session_number=1,
            interaction_type="conversation",
            summary="Discussed the quest",
            player_characters=["Frodo"]
        )
        npc_tracker.record_interaction(npc_id, interaction)

        generator = SessionRecapGenerator(fact_db, npc_tracker=npc_tracker)
        recap = generator.generate_recap(session_number=1)

        # Should have NPC reminders
        assert len(recap.npc_reminders) > 0


class TestNPCReminders:
    """Tests for _get_npc_reminders() method."""

    def test_npc_reminders_with_no_tracker(self, tmp_path):
        """Test that NPC reminders returns empty when no tracker."""
        fact_db = FactDatabase(tmp_path / "campaign")
        generator = SessionRecapGenerator(fact_db)

        reminders = generator._get_npc_reminders(session_number=1)

        assert reminders == []

    def test_npc_reminders_with_interactions(self, tmp_path):
        """Test NPC reminders with interactions."""
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        npc_tracker = NPCKnowledgeTracker(fact_db, campaign_path)

        # Add NPC
        npc_id = fact_db.add_fact(Fact(
            category=FactCategory.NPC,
            content="Aragorn",
            session_number=1
        ))

        # Add interactions
        npc_tracker.record_interaction(npc_id, PlayerInteraction(
            session_number=1,
            interaction_type="combat",
            summary="Fought together"
        ))

        generator = SessionRecapGenerator(fact_db, npc_tracker=npc_tracker)
        reminders = generator._get_npc_reminders(session_number=1)

        assert len(reminders) > 0
        assert "Aragorn" in reminders[0]


class TestQuestSummaries:
    """Tests for _get_active_quests() method."""

    def test_quest_summaries_from_threads(self, tmp_path):
        """Test that quest summaries are generated from active threads."""
        fact_db = FactDatabase(tmp_path / "campaign")

        fact_db.add_fact(Fact(
            category=FactCategory.QUEST,
            content="Retrieve the stolen crown",
            session_number=1,
            tags=["urgent", "main_quest"]
        ))

        generator = SessionRecapGenerator(fact_db)
        quests = generator._get_active_quests(session_number=1)

        assert len(quests) > 0
        assert isinstance(quests[0], QuestSummary)
        assert quests[0].status == "active"

    def test_quest_summaries_limited_to_five(self, tmp_path):
        """Test that quest summaries are limited to 5."""
        fact_db = FactDatabase(tmp_path / "campaign")

        for i in range(10):
            fact_db.add_fact(Fact(
                category=FactCategory.QUEST,
                content=f"Quest {i}",
                session_number=1,
                relevance_score=float(i)
            ))

        generator = SessionRecapGenerator(fact_db)
        quests = generator._get_active_quests(session_number=1)

        assert len(quests) <= 5


class TestSuggestedHooks:
    """Tests for _generate_hooks() method."""

    def test_generate_hooks_from_threads(self, tmp_path):
        """Test hook generation from story threads."""
        fact_db = FactDatabase(tmp_path / "campaign")

        threads = [
            StoryThread(
                thread_id="thread_1",
                description="Find the hidden temple",
                started_session=1,
                importance=1.0
            )
        ]

        generator = SessionRecapGenerator(fact_db)
        hooks = generator._generate_hooks(threads, [])

        assert len(hooks) > 0
        assert any("hidden temple" in hook for hook in hooks)

    def test_generate_hooks_limited_to_five(self, tmp_path):
        """Test that hooks are limited to 5."""
        fact_db = FactDatabase(tmp_path / "campaign")

        threads = [
            StoryThread(
                thread_id=f"thread_{i}",
                description=f"Thread {i}",
                started_session=1,
                importance=1.0
            )
            for i in range(10)
        ]

        generator = SessionRecapGenerator(fact_db)
        hooks = generator._generate_hooks(threads, [])

        assert len(hooks) <= 5

    def test_generate_hooks_fallback(self, tmp_path):
        """Test that hooks provide fallback when no threads."""
        fact_db = FactDatabase(tmp_path / "campaign")
        generator = SessionRecapGenerator(fact_db)

        hooks = generator._generate_hooks([], [])

        assert len(hooks) > 0  # Should have generic fallback hooks


class TestPartyStatus:
    """Tests for _get_party_status() method."""

    def test_party_status_from_tags(self, tmp_path):
        """Test party status extraction from event tags."""
        fact_db = FactDatabase(tmp_path / "campaign")

        fact_db.add_fact(Fact(
            category=FactCategory.EVENT,
            content="Party engaged in combat",
            session_number=1,
            tags=["combat", "danger"]
        ))

        generator = SessionRecapGenerator(fact_db)
        status = generator._get_party_status(session_number=1)

        assert "danger" in status.lower() or "combat" in status.lower()

    def test_party_status_default(self, tmp_path):
        """Test default party status when no indicators."""
        fact_db = FactDatabase(tmp_path / "campaign")
        generator = SessionRecapGenerator(fact_db)

        status = generator._get_party_status(session_number=1)

        assert "ready" in status.lower() or "adventure" in status.lower()


class TestDataClasses:
    """Tests for data classes."""

    def test_story_thread_creation(self):
        """Test StoryThread data class."""
        thread = StoryThread(
            thread_id="thread_1",
            description="Find the artifact",
            related_fact_ids=["fact_1", "fact_2"],
            started_session=1,
            importance=0.8
        )

        assert thread.thread_id == "thread_1"
        assert thread.description == "Find the artifact"
        assert len(thread.related_fact_ids) == 2
        assert thread.started_session == 1
        assert thread.importance == 0.8

    def test_quest_summary_creation(self):
        """Test QuestSummary data class."""
        quest = QuestSummary(
            quest_name="The Lost Crown",
            status="active",
            key_objectives=["Find the crown", "Return to king"],
            progress_notes="Making progress"
        )

        assert quest.quest_name == "The Lost Crown"
        assert quest.status == "active"
        assert len(quest.key_objectives) == 2
        assert quest.progress_notes == "Making progress"

    def test_session_recap_creation(self):
        """Test SessionRecap data class."""
        recap = SessionRecap(
            previously_on="The adventure began...",
            key_events=["Event 1", "Event 2"],
            active_quests=[],
            unresolved_threads=["Thread 1"],
            current_situation="Party in dungeon",
            party_status="Ready",
            npc_reminders=["Met Gandalf"],
            suggested_hooks=["Explore further"]
        )

        assert recap.previously_on == "The adventure began..."
        assert len(recap.key_events) == 2
        assert len(recap.unresolved_threads) == 1
        assert recap.current_situation == "Party in dungeon"


class TestVerbatimEvents:
    """Tests for verbatim journal events carried on the recap (DM2-8)."""

    def _event(self, title, importance, timestamp):
        from dm20_protocol.models import AdventureEvent, EventType

        return AdventureEvent(
            event_type=EventType.EXPLORATION,
            title=title,
            description=f"{title} description",
            timestamp=timestamp,
            session_number=1,
            importance=importance,
        )

    def test_verbatim_events_default_empty(self, tmp_path):
        """Recap without injected events has an empty verbatim list."""
        fact_db = FactDatabase(tmp_path / "campaign")
        generator = SessionRecapGenerator(fact_db)

        recap = generator.generate_recap(session_number=1)

        assert recap.verbatim_events == []

    def test_injected_events_sorted_importance_desc_then_timestamp(self, tmp_path):
        """Injected events land on the recap, importance desc, timestamp asc."""
        fact_db = FactDatabase(tmp_path / "campaign")
        generator = SessionRecapGenerator(fact_db)

        events = [
            self._event("minor", 2, datetime(2026, 1, 1, 10, 0)),
            self._event("major-late", 5, datetime(2026, 1, 1, 12, 0)),
            self._event("major-early", 5, datetime(2026, 1, 1, 11, 0)),
        ]

        recap = generator.generate_recap(session_number=1, events=events)

        assert [e.title for e in recap.verbatim_events] == [
            "major-early",
            "major-late",
            "minor",
        ]
