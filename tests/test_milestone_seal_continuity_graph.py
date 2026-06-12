"""
Milestone seal tests for Continuity Graph v1.

Integration tests across the milestone's tickets, exercising the full
write → fact graph → read roundtrips that no per-ticket suite covers:
facts and interactions recorded during play must surface when the DM
resumes (recap, NPC lookup, session-filtered events), and the backfill
path must heal campaigns that predate the fact graph.

Tools are exercised via the underlying functions (`.fn`) with the
module-level storage swapped, following tests/test_session_recap_tool.py.
Per-ticket behavior is covered by tests/test_fact_ingest.py,
test_fact_dual_write.py, test_knowledge_write_tools.py,
test_session_recap_tool.py, and test_read_tool_upgrades.py.
"""

from pathlib import Path

import pytest

from dm20_protocol.models import NPC, AdventureEvent, EventType, SessionNote
from dm20_protocol.storage import DnDStorage


@pytest.fixture
def storage(tmp_path: Path) -> DnDStorage:
    s = DnDStorage(data_dir=tmp_path / "data")
    s.create_campaign(name="Milestone Seal Test", description="d", dm_name="DM")
    return s


@pytest.fixture
def m(storage: DnDStorage):
    """dm20_protocol.main with its module-level storage swapped for the test."""
    from dm20_protocol import main as m

    original = m.storage
    m.storage = storage
    yield m
    m.storage = original


def _section(markdown: str, heading: str) -> str:
    """The body of one markdown section (up to the next heading)."""
    return markdown.split(heading)[1].split("##")[0]


class TestPlayToResumeRoundtrips:
    def test_party_fact_recorded_during_play_surfaces_in_resume_recap(self, m, storage):
        """Covers DM2-7 explicit write → DM2-8 recap read."""
        m.record_party_fact.fn(
            content="The sunsword is hidden beneath the chapel",
            category="event",
            source="Madam Eva",
            method="told_by_npc",
        )

        recap = m.get_session_recap.fn()
        assert "sunsword is hidden beneath the chapel" in recap

        query = m.party_knowledge.fn(topic="sunsword")
        assert "Madam Eva" in query

    def test_events_and_quests_written_during_play_feed_recap_without_sync(
        self, m, storage
    ):
        """Covers DM2-5 dual-write → DM2-8 recap read (no sync_facts call)."""
        m.add_event.fn(
            event_type="exploration",
            description="The party crossed the Ivlis river into Vallaki",
            session_number=2,
            importance=4,
        )
        m.create_quest.fn(
            title="Protect Ireena", description="Keep Ireena safe in Vallaki"
        )

        recap = m.get_session_recap.fn()
        assert "# Session Recap — Session 2" in recap
        assert "crossed the Ivlis river" in recap
        assert "Protect Ireena" in recap

    def test_quest_completed_during_play_drops_out_of_recap_threads(self, m, storage):
        """Covers DM2-5 quest resolution tagging → DM2-8 thread filtering."""
        m.create_quest.fn(
            title="Find the bones", description="Recover the stolen bones of St. Andral"
        )
        recap_active = m.get_session_recap.fn()
        assert "Find the bones" in recap_active

        m.update_quest.fn(title="Find the bones", status="completed")
        recap_done = m.get_session_recap.fn()
        assert "Find the bones" not in recap_done

    def test_npc_met_during_play_is_remembered_by_recap_and_npc_lookup(
        self, m, storage
    ):
        """Covers DM2-5/DM2-7 writes → DM2-8 NPC reminders + DM2-9 continuity
        block, joined on NPC fact id == entity id."""
        m.create_npc.fn(name="Donavich", description="The distraught priest of Barovia")
        m.add_event.fn(
            event_type="roleplay",
            description="The party met Donavich at the church",
            session_number=1,
            characters_involved='["Donavich"]',
        )
        m.record_npc_interaction.fn(
            npc="Donavich",
            interaction_type="conversation",
            summary="Confessed his son Doru is locked in the undercroft",
            session=1,
        )

        recap = m.get_session_recap.fn()
        assert "Donavich" in _section(recap, "## NPC Reminders")

        npc_view = m.get_npc.fn("Donavich")
        assert (
            "**Continuity:** First met: Session 1 / Last seen: Session 1 / "
            "Interactions: 2" in npc_view
        )

    def test_recap_and_session_filtered_events_agree_on_latest_session(
        self, m, storage
    ):
        """Covers DM2-5 dual-write → DM2-8 latest-session resolution agreeing
        with DM2-9 get_events(session_number)."""
        m.add_event.fn(
            event_type="exploration",
            description="Arrived at the village gates",
            session_number=1,
        )
        m.add_event.fn(
            event_type="exploration",
            description="Entered Castle Ravenloft at dusk",
            session_number=2,
        )

        recap = m.get_session_recap.fn()
        assert "# Session Recap — Session 2" in recap
        assert "Entered Castle Ravenloft" in recap
        assert "Arrived at the village gates" not in recap

        events_view = m.get_events.fn(session_number=2)
        assert "Entered Castle Ravenloft" in events_view
        assert "Arrived at the village gates" not in events_view


class TestBackfillBridge:
    def test_pre_dual_write_campaign_self_heals_with_one_sync_facts_call(
        self, m, storage
    ):
        """Covers DM2-10's resume self-heal flow: DM2-5 sync_facts backfill →
        DM2-8 recap + DM2-9 continuity block for a journal that predates the
        fact graph."""
        # Seed at the storage layer — journal + NPC predate the fact graph.
        storage.add_npc(NPC(name="Donavich", description="The distraught priest"))
        storage.add_event(
            AdventureEvent(
                event_type=EventType.SOCIAL,
                title="Met Donavich",
                description="The party met Donavich, the distraught priest",
                session_number=1,
                characters_involved=["Donavich"],
                importance=4,
            )
        )
        assert len(storage.fact_db.facts) == 0

        # The resume-flow trigger signals: graph-backed surfaces are empty.
        assert "has not learned any facts" in m.party_knowledge.fn()
        before = m.get_session_recap.fn()
        assert "The adventure continues..." in _section(before, "## Previously On")
        assert "**Continuity:** Not yet met" in m.get_npc.fn("Donavich")

        m.sync_facts.fn()

        after = m.get_session_recap.fn()
        assert "met Donavich" in _section(after, "## Previously On")
        npc_view = m.get_npc.fn("Donavich")
        assert (
            "**Continuity:** First met: Session 1 / Last seen: Session 1 / "
            "Interactions: 1" in npc_view
        )

    def test_explicit_records_converge_with_sync_facts_backfill(self, m, storage):
        """Covers DM2-7 explicit writes surviving DM2-5 sync_facts replays
        without duplication."""
        m.create_npc.fn(name="Ireena", description="The burgomaster's daughter")
        m.add_event.fn(
            event_type="roleplay",
            description="The party spoke with Ireena",
            session_number=1,
            characters_involved='["Ireena"]',
        )
        m.record_npc_interaction.fn(
            npc="Ireena",
            interaction_type="conversation",
            summary="Pledged to protect her on the road to Vallaki",
            session=1,
        )
        m.record_party_fact.fn(
            content="Ireena bears Tatyana's likeness",
            category="npc",
            source="Ismark",
            method="told_by_npc",
        )

        npc = storage.get_npc("Ireena")
        fact_count = len(storage.fact_db.facts)
        # One auto-recorded (dual-write) + one explicit interaction.
        assert len(storage.npc_knowledge_tracker.get_interactions(npc.id)) == 2

        m.sync_facts.fn()
        m.sync_facts.fn()

        assert len(storage.fact_db.facts) == fact_count
        assert len(storage.npc_knowledge_tracker.get_interactions(npc.id)) == 2
        assert storage.party_knowledge.known_fact_count == 1


class TestDegradedResumeFallback:
    def test_resume_falls_back_to_full_session_detail_when_fact_graph_unavailable(
        self, m, storage
    ):
        """Covers DM2-10's documented fallback (start.md resume step 4): when
        the DM2-8 recap degrades because the fact graph could not load,
        DM2-9's get_sessions(detail="full") + get_events(session_number) still
        restore the last session untruncated."""
        long_summary = (
            "The party bargained with Madam Eva for a reading of the cards, "
            "learning that their fates are bound to the castle on the hill "
            "and the man who rules it, then made camp at the Tser Pool."
        )
        storage.add_session_note(
            SessionNote(
                session_number=2,
                summary=long_summary,
                npcs_encountered=["Madam Eva"],
            )
        )
        storage.add_event(
            AdventureEvent(
                event_type=EventType.EXPLORATION,
                title="Left the village",
                description="The party left the village of Barovia",
                session_number=1,
            )
        )
        storage.add_event(
            AdventureEvent(
                event_type=EventType.EXPLORATION,
                title="Reached Tser Pool",
                description="The party reached the Tser Pool encampment",
                session_number=2,
            )
        )
        storage._fact_db = None

        # The degraded-recap signal the resume flow keys the fallback on.
        assert "could not be loaded" in m.get_session_recap.fn()

        sessions_view = m.get_sessions.fn(detail="full")
        assert long_summary in sessions_view  # untruncated, not the 100-char cut
        assert "Madam Eva" in sessions_view

        events_view = m.get_events.fn(session_number=2)
        assert "Tser Pool encampment" in events_view
        assert "village" not in events_view
