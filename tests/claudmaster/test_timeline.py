"""
Tests for timeline tracking module.

Tests GameTime, TimelineEvent, and TimelineTracker classes for managing
in-game time progression and event tracking.
"""

import json

import pytest
from pathlib import Path

from dm20_protocol.claudmaster.consistency.timeline import (
    GameTime,
    TimeUnit,
    TimelineEvent,
    TimelineTracker,
    TIME_OF_DAY,
    day_number_to_game_time,
    format_day_relative,
)


class TestGameTime:
    """Tests for GameTime class."""

    def test_default_values(self):
        """Test default GameTime initialization."""
        gt = GameTime()
        assert gt.year == 1492
        assert gt.month == 1
        assert gt.day == 1
        assert gt.hour == 8
        assert gt.minute == 0
        assert gt.round == 0

    def test_advance_hours(self):
        """Test advancing time by hours."""
        gt = GameTime(year=1492, month=1, day=1, hour=8, minute=0)
        new_gt = gt.advance(5, TimeUnit.HOUR)
        assert new_gt.hour == 13
        assert new_gt.minute == 0
        assert new_gt.day == 1

    def test_advance_days(self):
        """Test advancing time by days."""
        gt = GameTime(year=1492, month=1, day=1, hour=8, minute=0)
        new_gt = gt.advance(3, TimeUnit.DAY)
        assert new_gt.day == 4
        assert new_gt.hour == 8
        assert new_gt.month == 1

    def test_advance_months(self):
        """Test advancing time across month boundaries."""
        gt = GameTime(year=1492, month=1, day=28, hour=12, minute=0)
        new_gt = gt.advance(3, TimeUnit.DAY)
        # Should roll over to next month (30 days per month)
        assert new_gt.month == 2
        assert new_gt.day == 1
        assert new_gt.hour == 12

    def test_advance_year_rollover(self):
        """Test advancing time across year boundaries."""
        gt = GameTime(year=1492, month=12, day=29, hour=12, minute=0)
        new_gt = gt.advance(2, TimeUnit.DAY)
        # Should roll over to next year
        assert new_gt.year == 1493
        assert new_gt.month == 1
        assert new_gt.day == 1

    def test_to_string_full(self):
        """Test full string formatting."""
        gt = GameTime(year=1492, month=3, day=15, hour=14, minute=30)
        result = gt.to_string("full")
        assert result == "Year 1492, Month 3, Day 15, 14:30"

    def test_to_string_short(self):
        """Test short string formatting."""
        gt = GameTime(year=1492, month=3, day=15, hour=14, minute=30)
        result = gt.to_string("short")
        assert result == "Y1492 M3 D15 14:30"

    def test_to_string_time_only(self):
        """Test time-only string formatting."""
        gt = GameTime(year=1492, month=3, day=15, hour=14, minute=30)
        result = gt.to_string("time_only")
        assert result == "14:30"

    def test_comparison_less_than(self):
        """Test less than comparison."""
        gt1 = GameTime(year=1492, month=1, day=1, hour=8, minute=0)
        gt2 = GameTime(year=1492, month=1, day=1, hour=9, minute=0)
        assert gt1 < gt2
        assert not gt2 < gt1

    def test_comparison_less_equal(self):
        """Test less than or equal comparison."""
        gt1 = GameTime(year=1492, month=1, day=1, hour=8, minute=0)
        gt2 = GameTime(year=1492, month=1, day=1, hour=8, minute=0)
        gt3 = GameTime(year=1492, month=1, day=1, hour=9, minute=0)
        assert gt1 <= gt2
        assert gt1 <= gt3
        assert not gt3 <= gt1

    def test_comparison_greater_than(self):
        """Test greater than comparison."""
        gt1 = GameTime(year=1492, month=1, day=2, hour=8, minute=0)
        gt2 = GameTime(year=1492, month=1, day=1, hour=8, minute=0)
        assert gt1 > gt2
        assert not gt2 > gt1

    def test_comparison_greater_equal(self):
        """Test greater than or equal comparison."""
        gt1 = GameTime(year=1492, month=1, day=1, hour=8, minute=0)
        gt2 = GameTime(year=1492, month=1, day=1, hour=8, minute=0)
        gt3 = GameTime(year=1492, month=1, day=1, hour=7, minute=0)
        assert gt1 >= gt2
        assert gt1 >= gt3
        assert not gt3 >= gt1


class TestTimelineEvent:
    """Tests for TimelineEvent class."""

    def test_create_event(self):
        """Test creating a timeline event."""
        gt = GameTime(year=1492, month=1, day=1, hour=10, minute=0)
        event = TimelineEvent(
            id="evt_001",
            game_time=gt,
            real_session=1,
            description="Party meets in tavern",
            location="The Prancing Pony",
            characters_involved=["Gandalf", "Frodo"],
            fact_ids=["fact_001"]
        )
        assert event.id == "evt_001"
        assert event.description == "Party meets in tavern"
        assert event.location == "The Prancing Pony"
        assert len(event.characters_involved) == 2

    def test_auto_id_generation(self):
        """Test that TimelineTracker auto-generates IDs."""
        gt = GameTime(year=1492, month=1, day=1, hour=10, minute=0)
        event = TimelineEvent(
            game_time=gt,
            real_session=1,
            description="Test event"
        )
        # ID should be empty before being added to tracker
        assert event.id == ""


class TestTimelineTracker:
    """Tests for TimelineTracker class."""

    def test_initial_state(self, tmp_path):
        """Test initial timeline tracker state."""
        tracker = TimelineTracker(tmp_path)
        current = tracker.get_current_time()
        assert current.year == 1492
        assert current.month == 1
        assert tracker.event_count == 0

    def test_advance_time(self, tmp_path):
        """Test advancing the current time."""
        tracker = TimelineTracker(tmp_path)
        tracker.advance_time(2, TimeUnit.HOUR)
        current = tracker.get_current_time()
        assert current.hour == 10

    def test_set_time(self, tmp_path):
        """Test setting time directly."""
        tracker = TimelineTracker(tmp_path)
        new_time = GameTime(year=1492, month=6, day=15, hour=14, minute=30)
        tracker.set_time(new_time)
        current = tracker.get_current_time()
        assert current.month == 6
        assert current.day == 15
        assert current.hour == 14

    def test_add_event(self, tmp_path):
        """Test adding events to the timeline."""
        tracker = TimelineTracker(tmp_path)
        gt = GameTime(year=1492, month=1, day=1, hour=10, minute=0)
        event = TimelineEvent(
            game_time=gt,
            real_session=1,
            description="First event"
        )
        event_id = tracker.add_event(event)
        assert event_id.startswith("evt_")
        assert tracker.event_count == 1

    def test_get_events_at(self, tmp_path):
        """Test retrieving events at a specific time."""
        tracker = TimelineTracker(tmp_path)
        gt1 = GameTime(year=1492, month=1, day=1, hour=10, minute=0)
        gt2 = GameTime(year=1492, month=1, day=1, hour=11, minute=0)

        event1 = TimelineEvent(game_time=gt1, real_session=1, description="Event 1")
        event2 = TimelineEvent(game_time=gt1, real_session=1, description="Event 2")
        event3 = TimelineEvent(game_time=gt2, real_session=1, description="Event 3")

        tracker.add_event(event1)
        tracker.add_event(event2)
        tracker.add_event(event3)

        events = tracker.get_events_at(gt1)
        assert len(events) == 2
        assert all(e.game_time == gt1 for e in events)

    def test_get_events_between(self, tmp_path):
        """Test retrieving events in a time range."""
        tracker = TimelineTracker(tmp_path)
        gt1 = GameTime(year=1492, month=1, day=1, hour=8, minute=0)
        gt2 = GameTime(year=1492, month=1, day=1, hour=10, minute=0)
        gt3 = GameTime(year=1492, month=1, day=1, hour=12, minute=0)
        gt4 = GameTime(year=1492, month=1, day=1, hour=14, minute=0)

        event1 = TimelineEvent(game_time=gt1, real_session=1, description="Event 1")
        event2 = TimelineEvent(game_time=gt2, real_session=1, description="Event 2")
        event3 = TimelineEvent(game_time=gt3, real_session=1, description="Event 3")
        event4 = TimelineEvent(game_time=gt4, real_session=1, description="Event 4")

        tracker.add_event(event1)
        tracker.add_event(event2)
        tracker.add_event(event3)
        tracker.add_event(event4)

        events = tracker.get_events_between(gt2, gt3)
        assert len(events) == 2
        assert events[0].description == "Event 2"
        assert events[1].description == "Event 3"

    def test_validate_temporal_order_valid(self, tmp_path):
        """Test temporal validation with valid event."""
        tracker = TimelineTracker(tmp_path)
        gt1 = GameTime(year=1492, month=1, day=1, hour=10, minute=0)

        event1 = TimelineEvent(
            game_time=gt1,
            real_session=1,
            description="Event 1",
            location="Tavern",
            characters_involved=["Alice"]
        )
        tracker.add_event(event1)

        # Different location, different character - should be valid
        event2 = TimelineEvent(
            game_time=gt1,
            real_session=1,
            description="Event 2",
            location="Forest",
            characters_involved=["Bob"]
        )
        valid, error = tracker.validate_temporal_order(event2)
        assert valid is True
        assert error is None

    def test_validate_temporal_order_conflict(self, tmp_path):
        """Test temporal validation with conflicting event."""
        tracker = TimelineTracker(tmp_path)
        gt1 = GameTime(year=1492, month=1, day=1, hour=10, minute=0)

        event1 = TimelineEvent(
            game_time=gt1,
            real_session=1,
            description="Event 1",
            location="Tavern",
            characters_involved=["Alice", "Bob"]
        )
        tracker.add_event(event1)

        # Same character, same time, different location - should conflict
        event2 = TimelineEvent(
            game_time=gt1,
            real_session=1,
            description="Event 2",
            location="Forest",
            characters_involved=["Alice", "Charlie"]
        )
        valid, error = tracker.validate_temporal_order(event2)
        assert valid is False
        assert "Alice" in error
        assert "Tavern" in error
        assert "Forest" in error

    def test_calculate_travel_time(self, tmp_path):
        """Test travel time calculation."""
        tracker = TimelineTracker(tmp_path)

        # Walking 6 miles at 3 mph = 2 hours = 120 minutes
        time_walking = tracker.calculate_travel_time(6.0, "walking")
        assert time_walking == 120

        # Riding 8 miles at 8 mph = 1 hour = 60 minutes
        time_riding = tracker.calculate_travel_time(8.0, "riding")
        assert time_riding == 60

    def test_get_time_of_day(self, tmp_path):
        """Test time of day descriptions."""
        tracker = TimelineTracker(tmp_path)

        # Test dawn (5-7)
        tracker.set_time(GameTime(year=1492, month=1, day=1, hour=6, minute=0))
        assert tracker.get_time_of_day() == "dawn"

        # Test morning (7-12)
        tracker.set_time(GameTime(year=1492, month=1, day=1, hour=9, minute=0))
        assert tracker.get_time_of_day() == "morning"

        # Test midday (12-14)
        tracker.set_time(GameTime(year=1492, month=1, day=1, hour=13, minute=0))
        assert tracker.get_time_of_day() == "midday"

        # Test afternoon (14-17)
        tracker.set_time(GameTime(year=1492, month=1, day=1, hour=15, minute=0))
        assert tracker.get_time_of_day() == "afternoon"

        # Test evening (17-19)
        tracker.set_time(GameTime(year=1492, month=1, day=1, hour=18, minute=0))
        assert tracker.get_time_of_day() == "evening"

        # Test night (21-24)
        tracker.set_time(GameTime(year=1492, month=1, day=1, hour=22, minute=0))
        assert tracker.get_time_of_day() == "night"

        # Test deep night (0-5)
        tracker.set_time(GameTime(year=1492, month=1, day=1, hour=3, minute=0))
        assert tracker.get_time_of_day() == "deep night"

    def test_save_and_load(self, tmp_path):
        """Test persistence round-trip."""
        tracker1 = TimelineTracker(tmp_path)
        tracker1.set_time(GameTime(year=1492, month=3, day=15, hour=14, minute=30))

        gt1 = GameTime(year=1492, month=3, day=15, hour=10, minute=0)
        event1 = TimelineEvent(
            game_time=gt1,
            real_session=2,
            description="Saved event",
            location="Castle",
            characters_involved=["Alice", "Bob"]
        )
        tracker1.add_event(event1)
        tracker1.save()

        # Load into new tracker
        tracker2 = TimelineTracker(tmp_path)
        current = tracker2.get_current_time()
        assert current.year == 1492
        assert current.month == 3
        assert current.day == 15
        assert current.hour == 14
        assert current.minute == 30

        assert tracker2.event_count == 1
        events = tracker2.get_events_at(gt1)
        assert len(events) == 1
        assert events[0].description == "Saved event"
        assert events[0].location == "Castle"


class TestAnchoredFlag:
    """Persisted anchoring marker for the DM2-6 migration convention."""

    def test_fresh_tracker_is_unanchored(self, tmp_path):
        tracker = TimelineTracker(tmp_path)
        assert tracker.anchored is False

    def test_anchored_flag_round_trips_through_save_and_load(self, tmp_path):
        tracker = TimelineTracker(tmp_path)
        tracker.anchored = True
        tracker.save()
        reloaded = TimelineTracker(tmp_path)
        assert reloaded.anchored is True

    def test_timeline_file_without_flag_loads_unanchored(self, tmp_path):
        tracker = TimelineTracker(tmp_path)
        tracker.save()
        path = tmp_path / "timeline.json"
        data = json.loads(path.read_text())
        del data["anchored"]
        path.write_text(json.dumps(data))
        reloaded = TimelineTracker(tmp_path)
        assert reloaded.anchored is False


class TestEventsAccessor:
    def test_events_returns_chronological_copy(self, tmp_path):
        tracker = TimelineTracker(tmp_path)
        late = TimelineEvent(game_time=GameTime(day=3), real_session=1, description="late")
        early = TimelineEvent(game_time=GameTime(day=1), real_session=1, description="early")
        tracker.add_event(late)
        tracker.add_event(early)
        events = tracker.events
        assert [e.description for e in events] == ["early", "late"]
        events.clear()
        assert tracker.event_count == 2  # accessor returns a copy


class TestDayRelativeHelpers:
    """Day-relative formatter and Day-N mapping from the DM2-6 spike."""

    def test_epoch_formats_as_day_one_morning(self):
        assert format_day_relative(GameTime()) == "Day 1, morning (08:00)"

    def test_next_day_dawn(self):
        assert format_day_relative(GameTime(day=2, hour=5, minute=30)) == "Day 2, dawn (05:30)"

    def test_early_hours_do_not_shift_the_day(self):
        # Hour earlier than the epoch's 08:00 must not push the day count back
        assert format_day_relative(GameTime(hour=0)) == "Day 1, deep night (00:00)"

    def test_year_rollover_keeps_counting_days(self):
        # 12 months x 30 days: Day 361 = year+1, month 1, day 1
        assert format_day_relative(GameTime(year=1493)) == "Day 361, morning (08:00)"

    def test_day_number_to_game_time_maps_day_two(self):
        gt = day_number_to_game_time(2, hour=5, minute=30)
        assert (gt.year, gt.month, gt.day, gt.hour, gt.minute) == (1492, 1, 2, 5, 30)

    def test_day_number_to_game_time_rolls_months(self):
        # Spike example: Day 45 -> month 2, day 15 (not day=45, which would fail validation)
        gt = day_number_to_game_time(45, hour=6)
        assert (gt.month, gt.day, gt.hour, gt.minute) == (2, 15, 6, 0)

    def test_day_number_defaults_to_epoch_morning(self):
        gt = day_number_to_game_time(1)
        assert (gt.hour, gt.minute) == (8, 0)

    def test_day_number_round_trips_with_formatter(self):
        assert format_day_relative(day_number_to_game_time(45, hour=6)) == "Day 45, dawn (06:00)"
