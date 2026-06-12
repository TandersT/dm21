"""
Timeline tracking for the Claudmaster AI DM system.

This module provides tools for tracking in-game time progression and events
on a campaign timeline. It enables temporal consistency checking and travel
time calculations.

Key components:
- GameTime: In-game time representation with year, month, day, hour, minute, round
- TimeUnit: Enumeration of time units (round, minute, hour, day, week, month)
- TimelineEvent: An event that occurred at a specific game time
- TimelineTracker: Manager for the campaign timeline and time progression
"""

import json
import logging
from pathlib import Path
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field
from enum import Enum

logger = logging.getLogger("dm20-protocol")


class TimeUnit(str, Enum):
    """Time units for game time progression."""
    ROUND = "round"      # 6 seconds
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class GameTime(BaseModel):
    """
    In-game time representation.

    Uses a simplified calendar system with 12 months of 30 days each.
    Rounds represent combat turns (approximately 6 seconds each).

    Attributes:
        year: In-game year (default: 1492, Forgotten Realms standard)
        month: Month of the year (1-12)
        day: Day of the month (1-30)
        hour: Hour of the day (0-23)
        minute: Minute of the hour (0-59)
        round: Combat round counter (6 seconds each)
    """
    year: int = Field(default=1492, description="In-game year")
    month: int = Field(default=1, ge=1, le=12, description="Month (1-12)")
    day: int = Field(default=1, ge=1, le=30, description="Day of month (1-30)")
    hour: int = Field(default=8, ge=0, le=23, description="Hour (0-23)")
    minute: int = Field(default=0, ge=0, le=59, description="Minute (0-59)")
    round: int = Field(default=0, ge=0, description="Combat round (6 seconds each)")

    def advance(self, amount: int, unit: TimeUnit) -> "GameTime":
        """
        Return new GameTime advanced by specified amount.

        Args:
            amount: How much to advance
            unit: Unit of time (round, minute, hour, etc.)

        Returns:
            New GameTime instance with advanced time
        """
        # Convert everything to minutes for calculation
        total_minutes = self._to_total_minutes()

        if unit == TimeUnit.ROUND:
            total_minutes += amount  # 1 round ≈ 1 minute for simplicity in tracking
        elif unit == TimeUnit.MINUTE:
            total_minutes += amount
        elif unit == TimeUnit.HOUR:
            total_minutes += amount * 60
        elif unit == TimeUnit.DAY:
            total_minutes += amount * 24 * 60
        elif unit == TimeUnit.WEEK:
            total_minutes += amount * 7 * 24 * 60
        elif unit == TimeUnit.MONTH:
            total_minutes += amount * 30 * 24 * 60

        return GameTime._from_total_minutes(total_minutes, self.year)

    def _to_total_minutes(self) -> int:
        """Convert to total minutes from year start."""
        return (
            (self.month - 1) * 30 * 24 * 60 +
            (self.day - 1) * 24 * 60 +
            self.hour * 60 +
            self.minute
        )

    @staticmethod
    def _from_total_minutes(total: int, base_year: int) -> "GameTime":
        """
        Create GameTime from total minutes.

        Args:
            total: Total minutes since start of base_year
            base_year: Starting year

        Returns:
            New GameTime instance
        """
        minutes_per_year = 12 * 30 * 24 * 60

        year = base_year + total // minutes_per_year
        remaining = total % minutes_per_year

        month = remaining // (30 * 24 * 60) + 1
        remaining = remaining % (30 * 24 * 60)

        day = remaining // (24 * 60) + 1
        remaining = remaining % (24 * 60)

        hour = remaining // 60
        minute = remaining % 60

        return GameTime(year=year, month=month, day=day, hour=hour, minute=minute, round=0)

    def to_string(self, format: str = "full") -> str:
        """
        Format game time for display.

        Args:
            format: Format style ("full", "short", or "time_only")

        Returns:
            Formatted time string
        """
        if format == "full":
            return f"Year {self.year}, Month {self.month}, Day {self.day}, {self.hour:02d}:{self.minute:02d}"
        elif format == "short":
            return f"Y{self.year} M{self.month} D{self.day} {self.hour:02d}:{self.minute:02d}"
        elif format == "time_only":
            return f"{self.hour:02d}:{self.minute:02d}"
        return self.to_string("full")

    def __lt__(self, other: "GameTime") -> bool:
        """Less than comparison."""
        return self._to_total_minutes() < other._to_total_minutes()

    def __le__(self, other: "GameTime") -> bool:
        """Less than or equal comparison."""
        return self._to_total_minutes() <= other._to_total_minutes()

    def __gt__(self, other: "GameTime") -> bool:
        """Greater than comparison."""
        return self._to_total_minutes() > other._to_total_minutes()

    def __ge__(self, other: "GameTime") -> bool:
        """Greater than or equal comparison."""
        return self._to_total_minutes() >= other._to_total_minutes()


class TimelineEvent(BaseModel):
    """
    An event on the campaign timeline.

    Events track what happened, when it happened, and who was involved,
    enabling temporal consistency checking and narrative tracking.

    Attributes:
        id: Unique identifier, auto-generated if empty
        game_time: When the event occurred in game time
        real_session: Real-world session number
        description: What happened
        location: Where it happened (optional)
        characters_involved: Names of characters involved
        fact_ids: Related fact IDs from the fact database
    """
    id: str = Field(default="", description="Event ID, auto-generated if empty")
    game_time: GameTime = Field(description="When the event occurred in game time")
    real_session: int = Field(ge=1, description="Real-world session number")
    description: str = Field(description="What happened")
    location: Optional[str] = Field(default=None, description="Where it happened")
    characters_involved: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)


# Time of day descriptions for narrative purposes
TIME_OF_DAY = {
    (0, 5): "deep night",
    (5, 7): "dawn",
    (7, 12): "morning",
    (12, 14): "midday",
    (14, 17): "afternoon",
    (17, 19): "evening",
    (19, 21): "dusk",
    (21, 24): "night",
}

def time_of_day(hour: int) -> str:
    """Narrative description for an hour of day ("dawn", "morning", ...)."""
    for (start, end), description in TIME_OF_DAY.items():
        if start <= hour < end:
            return description
    return "night"


def format_day_relative(time: GameTime, epoch: GameTime | None = None) -> str:
    """Format a GameTime relative to the campaign epoch: "Day 2, dawn (05:30)".

    Day math uses date components only, so times earlier in the day than the
    epoch's 08:00 stay on the same day. Epoch defaults to GameTime() defaults
    (the campaign's Day 1, per the DM2-6 date-model spike).
    """
    epoch = epoch or GameTime()
    days = (
        (time.year - epoch.year) * 12 * 30
        + (time.month - epoch.month) * 30
        + (time.day - epoch.day)
    )
    return f"Day {days + 1}, {time_of_day(time.hour)} ({time.hour:02d}:{time.minute:02d})"


def day_number_to_game_time(day: int, hour: int = 8, minute: int = 0) -> GameTime:
    """Map a campaign day number (Day 1 = epoch) to a GameTime.

    Day N = epoch advanced by N-1 days, with the time of day applied on top —
    rolls through months/years via GameTime.advance, so Day 45 lands on
    month 2, day 15 instead of failing the day<=30 validation.
    """
    base = GameTime().advance(day - 1, TimeUnit.DAY)
    return GameTime(year=base.year, month=base.month, day=base.day, hour=hour, minute=minute)


# Travel times in minutes (walking speed ~3mph)
DEFAULT_TRAVEL_SPEEDS: dict[str, float] = {
    "walking": 3.0,      # miles per hour
    "riding": 8.0,
    "forced_march": 4.0,
    "cart": 2.0,
    "ship": 5.0,
}


class TimelineTracker:
    """
    Tracks in-game time progression and event timeline.

    The TimelineTracker maintains the current game time and a chronological
    record of events. It can validate temporal consistency, calculate travel
    times, and determine time of day for narrative purposes.

    Attributes:
        campaign_path: Path to campaign directory for persistence
        _current_time: Current in-game time
        _events: List of timeline events
        _calendar: Calendar configuration
    """

    def __init__(self, campaign_path: Path):
        """
        Initialize the timeline tracker.

        Args:
            campaign_path: Path to the campaign directory
        """
        self.campaign_path = Path(campaign_path)
        self._current_time = GameTime()
        self._events: list[TimelineEvent] = []
        self._calendar = {"months_per_year": 12, "days_per_month": 30, "hours_per_day": 24}
        self._anchored = False
        self.campaign_path.mkdir(parents=True, exist_ok=True)
        self.load()

    def get_current_time(self) -> GameTime:
        """
        Get the current game time.

        Returns:
            Current GameTime instance
        """
        return self._current_time

    @property
    def anchored(self) -> bool:
        """Whether the clock has been explicitly anchored (DM2-6 migration marker)."""
        return self._anchored

    @anchored.setter
    def anchored(self, value: bool) -> None:
        self._anchored = bool(value)

    @property
    def events(self) -> list[TimelineEvent]:
        """Chronological copy of the timeline events."""
        return list(self._events)

    def advance_time(self, amount: int, unit: TimeUnit) -> GameTime:
        """
        Advance the current game time.

        Args:
            amount: How much to advance
            unit: Unit of time

        Returns:
            New current time
        """
        self._current_time = self._current_time.advance(amount, unit)
        return self._current_time

    def set_time(self, game_time: GameTime) -> None:
        """
        Set the current game time directly.

        Args:
            game_time: New game time to set
        """
        self._current_time = game_time

    def add_event(self, event: TimelineEvent) -> str:
        """
        Add an event to the timeline.

        Events are automatically sorted chronologically after addition.

        Args:
            event: The event to add

        Returns:
            The event's ID (auto-generated if not provided)
        """
        if not event.id:
            event.id = f"evt_{uuid4().hex[:8]}"
        self._events.append(event)
        self._events.sort(key=lambda e: e.game_time._to_total_minutes())
        return event.id

    def get_events_at(self, game_time: GameTime) -> list[TimelineEvent]:
        """
        Get all events that occurred at a specific game time.

        Args:
            game_time: The time to query

        Returns:
            List of events at that time
        """
        target = game_time._to_total_minutes()
        return [e for e in self._events if e.game_time._to_total_minutes() == target]

    def get_events_between(self, start: GameTime, end: GameTime) -> list[TimelineEvent]:
        """
        Get all events between two times (inclusive).

        Args:
            start: Start time
            end: End time

        Returns:
            List of events in the time range
        """
        s = start._to_total_minutes()
        e = end._to_total_minutes()
        return [ev for ev in self._events if s <= ev.game_time._to_total_minutes() <= e]

    def validate_temporal_order(self, new_event: TimelineEvent) -> tuple[bool, Optional[str]]:
        """
        Check if an event's time is consistent with the timeline.

        Validates that characters aren't in multiple locations at the same time.

        Args:
            new_event: The event to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not self._events:
            return (True, None)

        # Check for events by same characters at same time in different locations
        same_time_events = self.get_events_at(new_event.game_time)
        for existing in same_time_events:
            if (new_event.location and existing.location and
                new_event.location != existing.location):
                common = set(new_event.characters_involved) & set(existing.characters_involved)
                if common:
                    return (
                        False,
                        f"Characters {common} cannot be at '{new_event.location}' "
                        f"and '{existing.location}' at the same time"
                    )
        return (True, None)

    def calculate_travel_time(
        self, distance_miles: float, travel_method: str = "walking"
    ) -> int:
        """
        Calculate travel time in minutes.

        Args:
            distance_miles: Distance to travel in miles
            travel_method: Method of travel (walking, riding, etc.)

        Returns:
            Travel time in minutes
        """
        speed = DEFAULT_TRAVEL_SPEEDS.get(travel_method, 3.0)
        hours = distance_miles / speed
        return int(hours * 60)

    def get_time_of_day(self) -> str:
        """
        Get narrative description of current time of day.

        Returns:
            Description like "dawn", "morning", "night", etc.
        """
        return time_of_day(self._current_time.hour)

    @property
    def event_count(self) -> int:
        """
        Get the total number of events in the timeline.

        Returns:
            Number of events
        """
        return len(self._events)

    def save(self) -> None:
        """Persist timeline to timeline.json."""
        data = {
            "version": "1.0",
            "current_time": self._current_time.model_dump(),
            "anchored": self._anchored,
            "events": [e.model_dump() for e in self._events],
            "calendar": self._calendar,
        }
        path = self.campaign_path / "timeline.json"
        path.write_text(json.dumps(data, indent=2, default=str))
        logger.info(f"Saved timeline with {len(self._events)} events to {path}")

    def load(self) -> None:
        """
        Load timeline from timeline.json.

        If the file doesn't exist, initializes with default values.
        If the file is corrupt, logs a warning and starts fresh.
        """
        path = self.campaign_path / "timeline.json"
        if not path.exists():
            logger.debug(f"No existing timeline at {path}, starting fresh")
            return
        try:
            data = json.loads(path.read_text())
            self._current_time = GameTime(**data.get("current_time", {}))
            self._anchored = bool(data.get("anchored", False))
            self._events = [TimelineEvent(**e) for e in data.get("events", [])]
            self._calendar = data.get("calendar", self._calendar)
            logger.info(f"Loaded timeline with {len(self._events)} events from {path}")
        except Exception as e:
            logger.warning(f"Failed to load timeline: {e}")


__all__ = [
    "TimeUnit",
    "GameTime",
    "TimelineEvent",
    "TimelineTracker",
    "TIME_OF_DAY",
    "DEFAULT_TRAVEL_SPEEDS",
    "time_of_day",
    "format_day_relative",
    "day_number_to_game_time",
]
