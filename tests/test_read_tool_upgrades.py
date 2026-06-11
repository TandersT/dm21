"""
Tests for the DM2-9 read-tool upgrades.

- get_sessions(detail="full") expands the LATEST session (untruncated summary
  + non-empty structured fields); older sessions keep the one-line format.
- get_events(session_number=N) filters by session at both the tool and the
  storage layer, composing with event_type, limit, and search.
- get_npc appends a continuity block (first met / last seen / interaction
  count) from the NPC knowledge tracker, for all callers; missing tracker
  omits the block entirely.

Tools are exercised via the underlying functions (`.fn`) with the
module-level storage swapped, following tests/test_session_recap_tool.py.
"""

from datetime import datetime
from pathlib import Path

import pytest

from dm20_protocol.models import NPC, AdventureEvent, EventType, SessionNote
from dm20_protocol.storage import DnDStorage


@pytest.fixture
def storage(tmp_path: Path) -> DnDStorage:
    s = DnDStorage(data_dir=tmp_path / "data")
    s.create_campaign(name="Read Tools Test", description="d", dm_name="DM")
    return s


@pytest.fixture
def m(storage: DnDStorage):
    """dm20_protocol.main with its module-level storage swapped for the test."""
    from dm20_protocol import main as m

    original = m.storage
    m.storage = storage
    yield m
    m.storage = original


def _event(
    title: str,
    description: str,
    session: int | None = 1,
    event_type: EventType = EventType.EXPLORATION,
    timestamp: datetime | None = None,
) -> AdventureEvent:
    kwargs = {"timestamp": timestamp} if timestamp is not None else {}
    return AdventureEvent(
        event_type=event_type,
        title=title,
        description=description,
        session_number=session,
        **kwargs,
    )


LONG_SUMMARY = (
    "The party crossed the Tser Pool encampment and bargained with Madam Eva "
    "for a reading of the cards, learning that their fates are bound to the "
    "castle on the hill and the man who rules it."
)


class TestGetSessionsDetail:
    def test_summary_default_truncates(self, m, storage):
        storage.add_session_note(
            SessionNote(session_number=1, summary=LONG_SUMMARY, title="The Reading")
        )

        result = m.get_sessions.fn()
        assert LONG_SUMMARY[:100] in result
        assert LONG_SUMMARY not in result
        assert "..." in result

    def test_full_expands_only_latest_session(self, m, storage):
        storage.add_session_note(
            SessionNote(
                session_number=1,
                summary=LONG_SUMMARY,
                events=["Old event from session one"],
            )
        )
        storage.add_session_note(
            SessionNote(
                session_number=2,
                summary=LONG_SUMMARY,
                events=["Bargained with Madam Eva", "Crossed the Tser Pool"],
                npcs_encountered=["Madam Eva"],
                quest_updates={"Escape Barovia": "The cards point to the castle"},
            )
        )

        result = m.get_sessions.fn(detail="full")
        # Latest session expanded: untruncated summary + structured fields.
        assert LONG_SUMMARY in result
        assert "**Events:**" in result
        assert "- Bargained with Madam Eva" in result
        assert "**NPCs encountered:** Madam Eva" in result
        assert "**Quest updates:**" in result
        assert "- Escape Barovia: The cards point to the castle" in result
        # Older session stays one-line: truncated, no structured fields.
        assert "Old event from session one" not in result
        assert f"{LONG_SUMMARY[:100]}..." in result

    def test_full_omits_empty_structured_fields(self, m, storage):
        storage.add_session_note(SessionNote(session_number=1, summary=LONG_SUMMARY))

        result = m.get_sessions.fn(detail="full")
        assert LONG_SUMMARY in result
        assert "**Events:**" not in result
        assert "**NPCs encountered:**" not in result
        assert "**Quest updates:**" not in result

    def test_no_sessions(self, m):
        assert m.get_sessions.fn() == "No session notes recorded."
        assert m.get_sessions.fn(detail="full") == "No session notes recorded."


class TestStorageGetEventsSessionFilter:
    def test_filters_by_session(self, storage):
        storage.add_event(_event("one", "d", session=1))
        storage.add_event(_event("two", "d", session=2))

        events = storage.get_events(session_number=2)
        assert [e.title for e in events] == ["two"]

    def test_composes_with_event_type(self, storage):
        storage.add_event(_event("fight", "d", session=1, event_type=EventType.COMBAT))
        storage.add_event(_event("walk", "d", session=1))
        storage.add_event(_event("other-fight", "d", session=2, event_type=EventType.COMBAT))

        events = storage.get_events(event_type=EventType.COMBAT, session_number=1)
        assert [e.title for e in events] == ["fight"]

    def test_filter_applies_before_limit(self, storage):
        # Newest events overall belong to session 2; a limit-first
        # implementation would never reach the session-1 events.
        storage.add_event(_event("s1-early", "d", session=1, timestamp=datetime(2026, 1, 1, 10)))
        storage.add_event(_event("s1-late", "d", session=1, timestamp=datetime(2026, 1, 1, 11)))
        storage.add_event(_event("s2-a", "d", session=2, timestamp=datetime(2026, 1, 2, 10)))
        storage.add_event(_event("s2-b", "d", session=2, timestamp=datetime(2026, 1, 2, 11)))

        events = storage.get_events(limit=2, session_number=1)
        assert [e.title for e in events] == ["s1-late", "s1-early"]

    def test_none_returns_all(self, storage):
        storage.add_event(_event("one", "d", session=1))
        storage.add_event(_event("two", "d", session=2))
        storage.add_event(_event("loose", "d", session=None))

        assert len(storage.get_events()) == 3

    def test_unattributed_events_never_match(self, storage):
        storage.add_event(_event("loose", "d", session=None))

        assert storage.get_events(session_number=1) == []


class TestGetEventsToolSessionFilter:
    def test_filters_by_session(self, m, storage):
        storage.add_event(_event("Met the burgomaster", "Met the burgomaster", session=1))
        storage.add_event(_event("Stormed the keep", "Stormed the keep", session=2))

        result = m.get_events.fn(session_number=2)
        assert "Stormed the keep" in result
        assert "Met the burgomaster" not in result

    def test_search_composes_with_session(self, m, storage):
        storage.add_event(_event("Vistani camp visit", "Visited the Vistani", session=1))
        storage.add_event(_event("Vistani warning", "A Vistani warned the party", session=2))

        result = m.get_events.fn(search="vistani", session_number=2)
        assert "Vistani warning" in result
        assert "Vistani camp visit" not in result

    def test_no_match(self, m, storage):
        storage.add_event(_event("one", "d", session=1))

        assert m.get_events.fn(session_number=3) == "No events found."


class TestGetNpcContinuityBlock:
    def test_tracker_missing_omits_block(self, m, storage):
        storage.add_npc(NPC(name="Ismark"))
        storage._npc_knowledge_tracker = None

        result = m.get_npc.fn("Ismark")
        assert "Continuity" not in result

    def test_no_interactions_shows_not_yet_met(self, m, storage):
        storage.add_npc(NPC(name="Ismark"))

        result = m.get_npc.fn("Ismark")
        assert "**Continuity:** Not yet met" in result

    def test_interactions_show_first_last_and_count(self, m, storage):
        m.create_npc.fn(name="Madam Eva")
        m.record_npc_interaction.fn(
            npc="Madam Eva", interaction_type="conversation",
            summary="Read the party's fortune", session=2,
        )
        m.record_npc_interaction.fn(
            npc="Madam Eva", interaction_type="conversation",
            summary="Warned about the castle", session=4,
        )

        result = m.get_npc.fn("Madam Eva")
        assert (
            "**Continuity:** First met: Session 2 / Last seen: Session 4 / Interactions: 2"
            in result
        )

    def test_block_present_for_player_callers(self, m, storage):
        m.create_npc.fn(name="Madam Eva", bio="Secretly Strahd's half-sister")
        m.record_npc_interaction.fn(
            npc="Madam Eva", interaction_type="conversation",
            summary="Read the party's fortune", session=2,
        )

        result = m.get_npc.fn("Madam Eva", player_id="player-1")
        # DM-only fields stay filtered for players...
        assert "Strahd's half-sister" not in result
        # ...but the continuity block is appended for all callers.
        assert (
            "**Continuity:** First met: Session 2 / Last seen: Session 2 / Interactions: 1"
            in result
        )
