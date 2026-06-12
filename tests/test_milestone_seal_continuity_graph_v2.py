"""
Milestone seal tests for Continuity Graph v2.

Integration tests across the milestone's tickets — the timeline clock and
journal stamping built on the date-model spike, the DM-facing contradiction
check, NPC knowledge propagation, and per-campaign event scoping — exercising
what is only visible across ticket boundaries: a campaign switch swaps the
clock, journal, and stamped history together; the consistency check sees lore
minted through the NPC knowledge path; the recap's session view and the
timeline's day view cover the same journal writes; and a played session
survives a storage reload intact.

Tools are exercised via the underlying functions (`.fn`) with the
module-level storage swapped, following
tests/test_milestone_seal_continuity_graph.py. Per-ticket behavior is covered
by tests/test_timeline_wiring.py, test_timeline_lifecycle.py,
test_contradiction_check_wiring.py, test_npc_knowledge_wiring.py, and
test_event_campaign_scoping.py.
"""

import hashlib
import re
from pathlib import Path

import pytest

from dm20_protocol.models import AdventureEvent, EventType
from dm20_protocol.storage import DnDStorage

CAMPAIGN = "Continuity Seal Test"


@pytest.fixture
def storage(tmp_path: Path) -> DnDStorage:
    s = DnDStorage(data_dir=tmp_path / "data")
    s.create_campaign(name=CAMPAIGN, description="d", dm_name="DM")
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


def _contradiction_id(check_output: str) -> str:
    match = re.search(r"ctr_\w+", check_output)
    assert match is not None, f"no contradiction id in: {check_output}"
    return match.group()


class TestPerCampaignContinuity:
    def test_each_campaign_keeps_its_own_clock_journal_and_stamped_history(
        self, m, storage
    ):
        """Verifies the combined behavior of DM2-11 (timeline clock + journal
        stamping) and DM2-14 (per-campaign event scoping): switching campaigns
        swaps the clock, the adventure log, and the stamped timeline together."""
        m.set_game_time.fn(day=3, hour=12)
        m.add_event.fn(
            event_type="world", description="The mists thicken around the village"
        )

        storage.create_campaign(name="Second Front", description="d", dm_name="DM")
        # A new campaign starts with its own fresh clock at Day 1.
        assert storage.timeline_tracker.get_current_time().day == 1
        assert storage.get_events() == []
        m.add_event.fn(
            event_type="world", description="A caravan arrives at the crossroads"
        )
        timeline_b = m.get_timeline.fn()
        assert "caravan arrives" in timeline_b
        assert "mists thicken" not in timeline_b

        storage.load_campaign(CAMPAIGN)
        timeline_a = m.get_timeline.fn()
        assert "Day 3" in timeline_a  # the clock comes back with the campaign
        assert "mists thicken" in timeline_a
        assert "caravan arrives" not in timeline_a

        events_a = m.get_events.fn()
        assert "mists thicken" in events_a
        assert "caravan arrives" not in events_a

    def test_fact_backfill_replays_only_the_active_campaigns_journal(
        self, m, storage
    ):
        """Verifies the combined behavior of DM2-14 (per-campaign event
        scoping) and the v1 sync_facts backfill: replaying the journal of one
        campaign no longer ingests another campaign's events into the fact
        graph or the recap."""
        # Journals seeded at the storage layer, as for campaigns that predate
        # the fact graph.
        storage.add_event(
            AdventureEvent(
                event_type=EventType.EXPLORATION,
                title="Marsh survey",
                description="The party mapped the eastern marsh",
                session_number=1,
            )
        )
        storage.create_campaign(name="Other Table", description="d", dm_name="DM")
        storage.add_event(
            AdventureEvent(
                event_type=EventType.EXPLORATION,
                title="Shrine raid",
                description="A rival party looted the marsh shrine",
                session_number=1,
            )
        )

        storage.load_campaign(CAMPAIGN)
        assert len(storage.fact_db.facts) == 0

        result = m.sync_facts.fn()
        assert "Events replayed: 1" in result

        contents = [f.content for f in storage.fact_db.facts.values()]
        assert any("eastern marsh" in c for c in contents)
        assert not any("rival party" in c for c in contents)

        recap = m.get_session_recap.fn()
        assert "eastern marsh" in recap
        assert "rival party" not in recap


class TestTimelineActivation:
    def test_resuming_a_pre_timeline_campaign_anchors_the_clock_before_stamping(
        self, m, storage, tmp_path
    ):
        """Verifies the combined behavior of DM2-6 (date-model migration
        rules) and DM2-11 (stamping + time tools): a campaign without a
        timeline file resumes unanchored, journal writes land without stamps
        until set_game_time anchors the clock, and pre-anchor history is
        never backfilled. The journal itself keeps writing to the
        per-campaign log throughout (DM2-14)."""
        # Simulate a campaign that predates the timeline: no timeline.json.
        (storage.timeline_tracker.campaign_path / "timeline.json").unlink()
        fresh = DnDStorage(data_dir=tmp_path / "data")
        fresh.load_campaign(CAMPAIGN)
        m.storage = fresh
        assert fresh.timeline_tracker.anchored is False

        result = m.add_event.fn(
            event_type="exploration",
            description="The party returns to the crossroads",
        )
        assert "unanchored" in result.lower()
        assert fresh.timeline_tracker.event_count == 0
        assert len(fresh.get_events()) == 1  # the journal write still landed

        # 'Day 2' anchors to the epoch advanced by one day.
        m.set_game_time.fn(day=2, hour=5, minute=30)
        t = fresh.timeline_tracker.get_current_time()
        assert (t.year, t.month, t.day, t.hour, t.minute) == (1492, 1, 2, 5, 30)

        m.add_event.fn(
            event_type="exploration",
            description="At dawn the party crosses the river",
        )
        stamps = fresh.timeline_tracker.events
        assert len(stamps) == 1  # the pre-anchor event got no retroactive stamp
        assert stamps[0].game_time.day == 2
        assert len(fresh.get_events()) == 2

    def test_recap_session_view_and_timeline_day_view_cover_the_same_journal(
        self, m, storage
    ):
        """Verifies the combined behavior of DM2-11 (game-time stamping) and
        the v1 recap: the same journal writes appear whole on the
        session-number axis and split correctly on the game-day axis."""
        m.set_game_time.fn(day=1, hour=9)
        m.add_event.fn(
            event_type="exploration",
            description="The party departs the village at first light",
            session_number=3,
        )
        m.advance_game_time.fn(amount=2, unit="day")
        m.add_event.fn(
            event_type="exploration",
            description="The party reaches the mountain pass",
            session_number=3,
        )

        recap = m.get_session_recap.fn()
        assert "# Session Recap — Session 3" in recap
        assert "departs the village" in recap
        assert "reaches the mountain pass" in recap

        day_one = m.get_timeline.fn(from_day=1)
        assert "departs the village" in day_one
        assert "reaches the mountain pass" not in day_one

        day_three = m.get_timeline.fn(from_day=3)
        assert "reaches the mountain pass" in day_three
        assert "departs the village" not in day_three


class TestConsistencyDuringPlay:
    def test_narration_checks_catch_conflicts_with_lore_revealed_to_npcs(
        self, m, storage
    ):
        """Verifies the combined behavior of DM2-13 (reveal_fact_to_npc mints
        fact-graph nodes) and DM2-12 (check_consistency reads the live fact
        graph): lore revealed to an NPC mid-session is immediately visible to
        the consistency check, the check stays read-only, and resolving
        persists the DM's ruling."""
        lore = "Father Donavich is alive and hiding in the church"
        m.create_npc.fn(name="Father Lucian")
        m.reveal_fact_to_npc.fn(npc="Father Lucian", fact=lore, source="witnessed")

        check = m.check_consistency.fn(
            statement="Father Donavich is dead in the church"
        )
        assert "contradiction(s) detected" in check
        assert _pfact_id(lore) in check  # the cited conflict is the revealed lore
        assert "alive and hiding" in check

        # The check itself persisted nothing.
        detector = storage.contradiction_detector
        assert not detector._contradictions_path.exists()

        resolved = m.resolve_contradiction.fn(
            contradiction_id=_contradiction_id(check),
            strategy="explain",
            notes="The body in the crypt was a doppelganger",
        )
        assert "persisted" in resolved
        assert detector._contradictions_path.exists()


class TestResumeAcrossReload:
    def test_a_played_session_survives_a_storage_reload_intact(
        self, m, storage, tmp_path
    ):
        """Verifies the combined behavior of DM2-11, DM2-12, DM2-13, and
        DM2-14: after a session of clock moves, stamped journal writes, NPC
        reveals and propagation, and a resolved contradiction, a fresh
        storage load restores every continuity surface from the campaign
        directory."""
        m.set_game_time.fn(day=4, hour=19)
        m.add_event.fn(
            event_type="roleplay",
            description="The council votes to seal the north gate",
            session_number=2,
        )
        m.create_npc.fn(name="Gatekeeper Ruslan")
        m.create_npc.fn(name="Widow Marta")
        decree = "The north gate is closed by decree"
        m.reveal_fact_to_npc.fn(
            npc="Gatekeeper Ruslan", fact=decree, source="witnessed"
        )
        m.propagate_npc_knowledge.fn(
            from_npc="Gatekeeper Ruslan", to_npc="Widow Marta"
        )
        check = m.check_consistency.fn(
            statement="The north gate is open to travelers"
        )
        cid = _contradiction_id(check)
        m.resolve_contradiction.fn(contradiction_id=cid, strategy="ignore")

        fresh = DnDStorage(data_dir=tmp_path / "data")
        fresh.load_campaign(CAMPAIGN)
        m.storage = fresh

        timeline = m.get_timeline.fn()
        assert "Day 4" in timeline
        assert "seal the north gate" in timeline

        recap = m.get_session_recap.fn()
        assert "# Session Recap — Session 2" in recap
        assert "council votes" in recap

        knowledge = m.npc_knowledge.fn(fact=decree)
        assert "Gatekeeper Ruslan" in knowledge
        assert "Widow Marta" in knowledge
        assert "0.75" in knowledge  # one rumor hop of confidence decay

        contradictions = fresh.contradiction_detector.get_all_contradictions()
        assert [c.id for c in contradictions] == [cid]
        assert contradictions[0].resolved is True
