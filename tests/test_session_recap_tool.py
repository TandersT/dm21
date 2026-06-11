"""
Tests for the get_session_recap tool (DM2-8).

get_session_recap assembles a SessionRecap from the per-campaign fact graph
and carries the session's journal events verbatim. session_number=None
resolves to the latest session with recorded journal data, falling back to
the current game-state session. Tools are exercised via the underlying
functions (`.fn`) with the module-level storage swapped, following
tests/test_knowledge_write_tools.py.

The E2E test is the bug we lived through, encoded as a regression test:
journal entries existed but the recap path never surfaced them.
"""

from datetime import datetime
from pathlib import Path

import pytest

from dm20_protocol.models import AdventureEvent, EventType
from dm20_protocol.storage import DnDStorage


@pytest.fixture
def storage(tmp_path: Path) -> DnDStorage:
    s = DnDStorage(data_dir=tmp_path / "data")
    s.create_campaign(name="Recap Test", description="d", dm_name="DM")
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
    importance: int = 3,
    timestamp: datetime | None = None,
    event_type: EventType = EventType.EXPLORATION,
) -> AdventureEvent:
    kwargs = {"timestamp": timestamp} if timestamp is not None else {}
    return AdventureEvent(
        event_type=event_type,
        title=title,
        description=description,
        session_number=session,
        importance=importance,
        **kwargs,
    )


class TestGetSessionRecap:
    def test_e2e_seeded_journal_sync_facts_recap_surfaces_facts(self, m, storage):
        """Regression: storage-seeded journal → sync_facts → recap shows both facts."""
        # Seed at the storage layer — no dual-write, fact graph stays empty.
        storage.add_event(
            _event(
                "Visited the church",
                "The party visited the church of Barovia",
                importance=4,
            )
        )
        storage.add_event(
            _event(
                "Met Donavich",
                "The party met Donavich, the distraught priest",
                importance=4,
                event_type=EventType.SOCIAL,
            )
        )
        assert len(storage.fact_db.facts) == 0

        result = m.sync_facts.fn()
        assert "✅" in result

        recap = m.get_session_recap.fn()
        assert "visited the church" in recap
        assert "met Donavich" in recap

    def test_resolves_latest_session_with_journal_data(self, m, storage):
        storage.add_event(_event("Old news", "Arrived in the village", session=1))
        storage.add_event(_event("Fresh news", "Entered the castle", session=3))

        recap = m.get_session_recap.fn()
        assert "# Session Recap — Session 3" in recap
        assert "Entered the castle" in recap
        # Verbatim section is scoped to the resolved session only.
        assert "Arrived in the village" not in recap

    def test_falls_back_to_current_session_when_journal_empty(self, m, storage):
        storage.update_game_state(current_session=2)

        recap = m.get_session_recap.fn()
        assert "# Session Recap — Session 2" in recap
        assert "No journal events recorded for this session." in recap

    def test_events_without_session_number_do_not_drive_resolution(self, m, storage):
        storage.update_game_state(current_session=5)
        storage.add_event(_event("Unattributed", "A stray note", session=None))
        storage.add_event(_event("Attributed", "Crossed the bridge", session=2))

        recap = m.get_session_recap.fn()
        assert "# Session Recap — Session 2" in recap

    def test_explicit_session_number_is_honoured(self, m, storage):
        storage.add_event(_event("Early", "Met the burgomaster", session=1))
        storage.add_event(_event("Late", "Stormed the keep", session=4))

        recap = m.get_session_recap.fn(session_number=1)
        assert "# Session Recap — Session 1" in recap
        assert "Met the burgomaster" in recap
        assert "Stormed the keep" not in recap

    def test_verbatim_events_sorted_importance_desc_then_timestamp(self, m, storage):
        storage.add_event(
            _event("minor", "Minor detail", importance=2, timestamp=datetime(2026, 1, 1, 10))
        )
        storage.add_event(
            _event("major-late", "Major late", importance=5, timestamp=datetime(2026, 1, 1, 12))
        )
        storage.add_event(
            _event("major-early", "Major early", importance=5, timestamp=datetime(2026, 1, 1, 11))
        )

        recap = m.get_session_recap.fn()
        verbatim = recap.split("## Verbatim Journal Events")[1]
        assert (
            verbatim.index("major-early")
            < verbatim.index("major-late")
            < verbatim.index("minor")
        )

    def test_verbatim_descriptions_are_untruncated(self, m, storage):
        long_description = (
            "Donavich confessed that his son Doru descended into the church "
            "undercroft a year ago after joining the failed uprising against "
            "Strahd, and has been locked there ever since, begging for blood "
            "each night while his father prays for a salvation that never comes."
        )
        storage.add_event(_event("The confession", long_description, importance=5))

        recap = m.get_session_recap.fn()
        assert long_description in recap

    def test_recap_includes_summary_sections(self, m, storage):
        storage.add_event(_event("Visited the church", "Visited the church", importance=4))
        m.sync_facts.fn()

        recap = m.get_session_recap.fn()
        assert "## Previously On" in recap
        assert "## Current Situation" in recap
        assert "## Party Status" in recap
        assert "## Verbatim Journal Events (Session 1)" in recap

    def test_invalid_length_lists_valid_values(self, m):
        result = m.get_session_recap.fn(length="epic")
        assert "Invalid length 'epic'" in result
        assert "brief" in result
        assert "standard" in result
        assert "detailed" in result

    def test_invalid_style_lists_valid_values(self, m):
        result = m.get_session_recap.fn(style="haiku")
        assert "Invalid style 'haiku'" in result
        assert "narrative" in result
        assert "bullet" in result
        assert "mixed" in result

    def test_unavailable_without_fact_graph(self, m, storage):
        storage._fact_db = None
        result = m.get_session_recap.fn()
        assert "could not be loaded" in result

    def test_requires_campaign(self, m, storage):
        storage._current_campaign = None
        result = m.get_session_recap.fn()
        assert "No active campaign" in result
