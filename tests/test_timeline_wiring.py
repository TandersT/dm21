"""
Tests for the play-loop timeline wiring (DM2-11).

Time tools (set_game_time / advance_game_time / get_timeline), the add_event
stamping hook, and the prose-only nudge, per the DM2-6 date-model spike.
Tools are exercised via the underlying functions (`.fn`) with the module-level
storage swapped, following tests/test_fact_dual_write.py.
"""

from pathlib import Path

import pytest

from dm20_protocol.storage import DnDStorage


@pytest.fixture
def storage(tmp_path: Path) -> DnDStorage:
    s = DnDStorage(data_dir=tmp_path / "data")
    s.create_campaign(name="Timeline Test", description="d", dm_name="DM")
    return s


@pytest.fixture
def m(storage: DnDStorage):
    """dm20_protocol.main with its module-level storage swapped for the test."""
    from dm20_protocol import main as m

    original = m.storage
    m.storage = storage
    yield m
    m.storage = original


def _unanchor(storage: DnDStorage) -> None:
    storage.timeline_tracker.anchored = False
    storage.timeline_tracker.save()


# ── set_game_time ───────────────────────────────────────────────────


class TestSetGameTime:
    def test_day_two_maps_to_epoch_plus_one_day(self, m, storage):
        m.set_game_time.fn(day=2, hour=5, minute=30)
        t = storage.timeline_tracker.get_current_time()
        assert (t.year, t.month, t.day, t.hour, t.minute) == (1492, 1, 2, 5, 30)

    def test_day_forty_five_rolls_into_month_two(self, m, storage):
        m.set_game_time.fn(day=45, hour=6)
        t = storage.timeline_tracker.get_current_time()
        assert (t.month, t.day) == (2, 15)

    def test_set_anchors_the_timeline(self, m, storage):
        _unanchor(storage)
        m.set_game_time.fn(day=2)
        assert storage.timeline_tracker.anchored is True

    def test_set_twice_with_same_args_is_idempotent(self, m, storage):
        m.set_game_time.fn(day=2, hour=6)
        first = storage.timeline_tracker.get_current_time()
        m.set_game_time.fn(day=2, hour=6)
        second = storage.timeline_tracker.get_current_time()
        assert first == second

    def test_derives_prose_display_when_none_given(self, m, storage):
        m.set_game_time.fn(day=2, hour=5, minute=30)
        assert storage.get_game_state().current_date_in_game == "Day 2, dawn (05:30)"

    def test_explicit_date_display_overrides_derived(self, m, storage):
        m.set_game_time.fn(day=2, hour=5, date_display="Dawn — first morning in Barovia")
        assert storage.get_game_state().current_date_in_game == "Dawn — first morning in Barovia"

    def test_unavailable_timeline_degrades_with_guidance(self, m, storage):
        storage._timeline_tracker = None
        result = m.set_game_time.fn(day=2)
        assert "unavailable" in result.lower()


# ── advance_game_time ───────────────────────────────────────────────


class TestAdvanceGameTime:
    def test_advances_the_clock(self, m, storage):
        m.set_game_time.fn(day=1, hour=8)
        m.advance_game_time.fn(amount=2, unit="day")
        t = storage.timeline_tracker.get_current_time()
        assert (t.day, t.hour) == (3, 8)

    def test_advance_persists_to_disk(self, m, storage, tmp_path):
        m.advance_game_time.fn(amount=1, unit="day")
        fresh = DnDStorage(data_dir=tmp_path / "data")
        fresh.load_campaign("Timeline Test")
        assert fresh.timeline_tracker.get_current_time().day == 2

    def test_derives_prose_display(self, m, storage):
        m.set_game_time.fn(day=1, hour=8)
        m.advance_game_time.fn(amount=10, unit="hour")
        assert storage.get_game_state().current_date_in_game == "Day 1, evening (18:00)"

    def test_refuses_when_unanchored(self, m, storage):
        _unanchor(storage)
        before = storage.timeline_tracker.get_current_time()
        result = m.advance_game_time.fn(amount=1, unit="day")
        assert "not anchored" in result.lower()
        assert storage.timeline_tracker.get_current_time() == before

    def test_unavailable_timeline_degrades_with_guidance(self, m, storage):
        storage._timeline_tracker = None
        result = m.advance_game_time.fn(amount=1, unit="day")
        assert "unavailable" in result.lower()


# ── get_timeline ────────────────────────────────────────────────────


class TestGetTimeline:
    def test_shows_clock_and_anchor_status(self, m, storage):
        m.set_game_time.fn(day=2, hour=5, minute=30)
        result = m.get_timeline.fn()
        assert "Day 2, dawn (05:30)" in result
        assert "anchored" in result

    def test_flags_unanchored_clock(self, m, storage):
        _unanchor(storage)
        result = m.get_timeline.fn()
        assert "NOT anchored" in result

    def test_range_query_returns_events_between_days(self, m, storage):
        m.set_game_time.fn(day=1)
        m.add_event.fn(event_type="world", description="Day one happening")
        m.advance_game_time.fn(amount=2, unit="day")
        m.add_event.fn(event_type="world", description="Day three happening")

        result = m.get_timeline.fn(from_day=1, to_day=2)
        assert "Day one happening" in result
        assert "Day three happening" not in result

    def test_single_day_query_defaults_to_day(self, m, storage):
        m.set_game_time.fn(day=3)
        m.add_event.fn(event_type="world", description="Only on day three")
        result = m.get_timeline.fn(from_day=3)
        assert "Only on day three" in result

    def test_unavailable_timeline_degrades_with_guidance(self, m, storage):
        storage._timeline_tracker = None
        result = m.get_timeline.fn()
        assert "unavailable" in result.lower()


# ── add_event stamping hook ─────────────────────────────────────────


class TestJournalStamping:
    def test_add_event_stamps_timeline_event_at_current_time(self, m, storage):
        m.set_game_time.fn(day=2, hour=5, minute=30)
        m.add_event.fn(
            event_type="roleplay",
            description="The party spoke with Ismark.",
            session_number=3,
            location="Barovia Village",
            characters_involved='["Ismark", "Thalion"]',
        )

        events = storage.timeline_tracker.events
        assert len(events) == 1
        stamped = events[0]
        t = stamped.game_time
        assert (t.day, t.hour, t.minute) == (2, 5, 30)
        assert stamped.real_session == 3
        assert stamped.location == "Barovia Village"
        assert stamped.characters_involved == ["Ismark", "Thalion"]
        assert stamped.description == "The party spoke with Ismark."

    def test_stamp_links_to_journal_event_and_fact(self, m, storage):
        m.add_event.fn(event_type="world", description="The mists close in.")
        journal_event = storage.get_events(limit=1)[0]
        stamped = storage.timeline_tracker.events[0]
        assert stamped.id == f"tl_{journal_event.id}"
        assert stamped.fact_ids == [f"evt_{journal_event.id}"]

    def test_stamp_session_falls_back_to_game_state(self, m, storage):
        storage.update_game_state(current_session=4)
        m.add_event.fn(event_type="world", description="No explicit session.")
        assert storage.timeline_tracker.events[0].real_session == 4

    def test_response_mentions_timeline_stamp(self, m, storage):
        m.set_game_time.fn(day=2, hour=5, minute=30)
        result = m.add_event.fn(event_type="world", description="Stamped.")
        assert "Day 2, dawn (05:30)" in result

    def test_unanchored_clock_skips_stamp_and_says_so(self, m, storage):
        _unanchor(storage)
        result = m.add_event.fn(event_type="world", description="Too early.")
        assert storage.timeline_tracker.event_count == 0
        assert "unanchored" in result.lower()

    def test_stamps_persist_to_disk(self, m, storage, tmp_path):
        m.add_event.fn(event_type="world", description="Persisted.")
        fresh = DnDStorage(data_dir=tmp_path / "data")
        fresh.load_campaign("Timeline Test")
        assert fresh.timeline_tracker.event_count == 1

    def test_no_tracker_means_no_stamp_and_clean_response(self, m, storage):
        storage._timeline_tracker = None
        result = m.add_event.fn(event_type="world", description="Legacy campaign.")
        assert "Added world event" in result

    def test_temporal_conflict_warns_in_response(self, m, storage):
        m.set_game_time.fn(day=1, hour=12)
        m.add_event.fn(
            event_type="roleplay",
            description="Thalion bargains at the tavern.",
            location="Tavern",
            characters_involved='["Thalion"]',
        )
        result = m.add_event.fn(
            event_type="exploration",
            description="Thalion scouts the castle.",
            location="Castle Ravenloft",
            characters_involved='["Thalion"]',
        )
        assert "conflict" in result.lower()
        # The stamp still lands — the journal write already happened
        assert storage.timeline_tracker.event_count == 2


# ── prose-only nudge and game-state clock ───────────────────────────


class TestProseOnlyNudge:
    def test_prose_only_date_update_notes_clock_did_not_advance(self, m, storage):
        result = m.update_game_state.fn(current_date_in_game="Dawn — first morning in Barovia")
        assert "did not advance" in result

    def test_non_date_updates_get_no_nudge(self, m, storage):
        result = m.update_game_state.fn(current_location="Barovia Village")
        assert "did not advance" not in result

    def test_no_tracker_means_no_nudge(self, m, storage):
        storage._timeline_tracker = None
        result = m.update_game_state.fn(current_date_in_game="Day 2")
        assert "did not advance" not in result


class TestGameStateClock:
    def test_game_state_shows_timeline_clock(self, m, storage):
        m.set_game_time.fn(day=2, hour=5, minute=30)
        result = m.get_game_state.fn()
        assert "Timeline Clock:" in result
        assert "Day 2, dawn (05:30)" in result
        assert "(anchored)" in result

    def test_game_state_flags_unanchored_clock(self, m, storage):
        _unanchor(storage)
        result = m.get_game_state.fn()
        assert "not anchored" in result

    def test_no_tracker_omits_clock_line(self, m, storage):
        storage._timeline_tracker = None
        result = m.get_game_state.fn()
        assert "Timeline Clock:" not in result
