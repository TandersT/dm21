# DM2-11 Timeline Tracker Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `TimelineTracker` into the play loop: journal writes stamp `TimelineEvent`s engine-side, the DM gets set/advance/query time tools, and temporal-order validation surfaces in tool responses.

**Architecture:** Tracker cached on `DnDStorage` mirroring the fact-graph lifecycle (split campaigns only, `None` degrade). Tool-layer stamping hook in `add_event` mirroring `_ingest_to_fact_graph`. Three new MCP tools (`set_game_time`, `advance_game_time`, `get_timeline`) keep all calendar math engine-side; prose `current_date_in_game` stays the display authority per the DM2-6 spike.

**Tech Stack:** Python 3.12, pydantic, FastMCP, pytest (`uv run pytest`). Repo root = worktree root; run all commands from there.

**Spec:** `docs/superpowers/specs/2026-06-12-dm2-11-timeline-wiring-design.md`

---

### Task 1: `timeline.py` — anchored flag, events accessor, day-relative helpers

**Files:**
- Modify: `src/dm20_protocol/claudmaster/consistency/timeline.py`
- Test: `tests/claudmaster/test_timeline.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/claudmaster/test_timeline.py`. Extend the existing import block at the top of the file to:

```python
from dm20_protocol.claudmaster.consistency.timeline import (
    GameTime,
    TimeUnit,
    TimelineEvent,
    TimelineTracker,
    TIME_OF_DAY,
    day_number_to_game_time,
    format_day_relative,
)
```

Also ensure `import json` is present at the top of the test file. Append at the end:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/claudmaster/test_timeline.py -v -k "Anchored or EventsAccessor or DayRelative"`
Expected: FAIL/ERROR — `ImportError: cannot import name 'day_number_to_game_time'`

- [ ] **Step 3: Implement in `timeline.py`**

3a. After the `TIME_OF_DAY` dict (line ~195), add module-level helpers:

```python
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
```

3b. In `TimelineTracker.__init__`, before `self.load()`:

```python
        self._anchored = False
```

3c. After `get_current_time` (or near the `event_count` property), add:

```python
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
```

3d. In `save()`, add to the `data` dict (after `"current_time"`):

```python
            "anchored": self._anchored,
```

3e. In `load()`, inside the `try` block (after `self._current_time = ...`):

```python
            self._anchored = bool(data.get("anchored", False))
```

3f. Replace the body of `get_time_of_day` to reuse the module helper:

```python
    def get_time_of_day(self) -> str:
        """
        Get narrative description of current time of day.

        Returns:
            Description like "dawn", "morning", "night", etc.
        """
        return time_of_day(self._current_time.hour)
```

3g. Add `"time_of_day"`, `"format_day_relative"`, `"day_number_to_game_time"` to `__all__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/claudmaster/test_timeline.py -v`
Expected: all PASS (including the pre-existing GameTime/Tracker tests — `get_time_of_day` behavior is unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/dm20_protocol/claudmaster/consistency/timeline.py tests/claudmaster/test_timeline.py
git commit -m "feat(DM2-11): anchored marker and day-relative helpers on timeline"
```

---

### Task 2: `storage.py` — timeline tracker lifecycle

**Files:**
- Modify: `src/dm20_protocol/storage.py` (init ~line 100, create_campaign ~line 508, load_campaign ~line 581, delete_campaign ~line 638, new loader next to `_load_fact_graph` ~line 1467)
- Test: `tests/test_timeline_lifecycle.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_timeline_lifecycle.py`:

```python
"""
Tests for the storage-held timeline tracker lifecycle (DM2-11).

DnDStorage caches a per-campaign TimelineTracker, mirroring the fact-graph /
DiscoveryTracker precedent: loaded on campaign create/load, cleared on delete,
degrading to None on failure. New split campaigns are born anchored at the
epoch (a fresh campaign's Day 1 is a genuine anchor, per the DM2-6 spike);
campaigns without a timeline.json load unanchored.
"""

from pathlib import Path

import pytest

from dm20_protocol.storage import DnDStorage


@pytest.fixture
def storage(tmp_path: Path) -> DnDStorage:
    return DnDStorage(data_dir=tmp_path / "data")


class TestTimelineLifecycle:
    def test_accessor_none_without_campaign(self, storage: DnDStorage):
        assert storage.timeline_tracker is None

    def test_created_campaign_is_born_anchored_at_epoch(self, storage: DnDStorage):
        storage.create_campaign(name="Test", description="d")
        tracker = storage.timeline_tracker
        assert tracker is not None
        assert tracker.anchored is True
        epoch = tracker.get_current_time()
        assert (epoch.year, epoch.month, epoch.day) == (1492, 1, 1)

    def test_born_anchored_persists_to_disk(self, storage: DnDStorage, tmp_path: Path):
        storage.create_campaign(name="Test", description="d")
        fresh = DnDStorage(data_dir=tmp_path / "data")
        fresh.load_campaign("Test")
        assert fresh.timeline_tracker is not None
        assert fresh.timeline_tracker.anchored is True

    def test_campaign_without_timeline_file_loads_unanchored(
        self, storage: DnDStorage, tmp_path: Path
    ):
        storage.create_campaign(name="Test", description="d")
        timeline_file = storage.timeline_tracker.campaign_path / "timeline.json"
        timeline_file.unlink()

        fresh = DnDStorage(data_dir=tmp_path / "data")
        fresh.load_campaign("Test")
        assert fresh.timeline_tracker is not None
        assert fresh.timeline_tracker.anchored is False

    def test_switch_campaign_swaps_tracker(self, storage: DnDStorage):
        storage.create_campaign(name="One", description="d")
        tracker_one = storage.timeline_tracker
        storage.create_campaign(name="Two", description="d")
        assert storage.timeline_tracker is not tracker_one

    def test_cleared_on_delete_of_active_campaign(self, storage: DnDStorage):
        storage.create_campaign(name="Test", description="d")
        storage.delete_campaign("Test")
        assert storage.timeline_tracker is None

    def test_failure_degrades_to_none(self, storage: DnDStorage, tmp_path: Path, monkeypatch):
        storage.create_campaign(name="Test", description="d")

        def boom(*args, **kwargs):
            raise RuntimeError("timeline store unavailable")

        import dm20_protocol.claudmaster.consistency.timeline as timeline_module

        monkeypatch.setattr(timeline_module.TimelineTracker, "load", boom)
        fresh = DnDStorage(data_dir=tmp_path / "data")
        fresh.load_campaign("Test")
        assert fresh.timeline_tracker is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_timeline_lifecycle.py -v`
Expected: FAIL — `AttributeError: 'DnDStorage' object has no attribute 'timeline_tracker'`

- [ ] **Step 3: Implement in `storage.py`**

3a. In `DnDStorage.__init__`, next to `self._fact_db = None` (~line 100):

```python
        self._timeline_tracker = None
```

3b. After `_load_fact_graph` (~line 1467), add (mirror its structure exactly):

```python
    # ------------------------------------------------------------------
    # Timeline Tracker Management
    # ------------------------------------------------------------------

    @property
    def timeline_tracker(self):
        """Get the TimelineTracker for the current campaign.

        Returns:
            TimelineTracker instance if a split campaign is loaded and the
            timeline loaded successfully, None otherwise.
        """
        return self._timeline_tracker

    def _load_timeline_tracker(self) -> None:
        """Load or initialize the timeline tracker for the current campaign.

        Only applicable to split storage campaigns. On failure the accessor
        degrades to None — timeline problems must never break the primary
        journal/entity write path.
        """
        self._timeline_tracker = None

        if self._current_format != StorageFormat.SPLIT or not self._current_campaign:
            return

        campaign_dir = self._split_backend._get_campaign_dir(self._current_campaign.name)
        try:
            from .claudmaster.consistency.timeline import TimelineTracker

            self._timeline_tracker = TimelineTracker(campaign_dir)
            logger.info(
                f"Loaded timeline for campaign '{self._current_campaign.name}' "
                f"({self._timeline_tracker.event_count} events, "
                f"{'anchored' if self._timeline_tracker.anchored else 'unanchored'})"
            )
        except Exception as e:
            logger.warning(f"Failed to load timeline tracker: {e}")
            self._timeline_tracker = None
```

3c. In `create_campaign`, after `self._load_fact_graph()` (~line 508):

```python
        # Initialize timeline for the new campaign — born anchored at the
        # epoch: a fresh campaign's Day 1 is a genuine anchor (DM2-6 spike)
        self._load_timeline_tracker()
        if self._timeline_tracker is not None:
            self._timeline_tracker.anchored = True
            self._timeline_tracker.save()
```

3d. In `load_campaign`, after `self._load_fact_graph()` (~line 581):

```python
        # Load timeline tracker (split campaigns only)
        self._load_timeline_tracker()
```

3e. In `delete_campaign`'s active-campaign clear block, next to `self._npc_knowledge_tracker = None` (~line 640):

```python
            self._timeline_tracker = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_timeline_lifecycle.py tests/test_fact_graph_lifecycle.py -v`
Expected: all PASS (fact-graph lifecycle untouched).

- [ ] **Step 5: Commit**

```bash
git add src/dm20_protocol/storage.py tests/test_timeline_lifecycle.py
git commit -m "feat(DM2-11): per-campaign timeline tracker lifecycle on DnDStorage"
```

---

### Task 3: `main.py` — time tools (`set_game_time`, `advance_game_time`, `get_timeline`)

**Files:**
- Modify: `src/dm20_protocol/main.py` (imports ~line 19; new tools after `get_game_state`, ~line 1762)
- Test: `tests/test_timeline_wiring.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_timeline_wiring.py` (fixtures follow `tests/test_fact_dual_write.py`: tools exercised via `.fn` with module-level storage swapped):

```python
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
```

(The two `TestGetTimeline` range tests also exercise the Task 4 stamping hook; they will only pass after Task 4. Run them with `-k "not range_query and not single_day"` in this task and re-run fully in Task 4.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_timeline_wiring.py -v -k "SetGameTime or AdvanceGameTime or GetTimeline"`
Expected: FAIL — `AttributeError: module 'dm20_protocol.main' has no attribute 'set_game_time'`

- [ ] **Step 3: Implement the three tools in `main.py`**

3a. Add to the imports (after `from .storage import DnDStorage`, ~line 19):

```python
from .claudmaster.consistency.timeline import (
    TimelineEvent,
    TimeUnit,
    day_number_to_game_time,
    format_day_relative,
)
```

(`TimelineEvent` is used by Task 4's stamping hook; importing it now is fine.)

3b. After the `get_game_state` tool and before `_prefetch_state_update` (~line 1762), add:

```python
_TIMELINE_UNAVAILABLE = (
    "Timeline unavailable for this campaign (legacy format or failed to load). "
    "Structured time tracking requires a split-format campaign."
)


@mcp.tool
def set_game_time(
    day: Annotated[int, Field(description="Campaign day number (Day 1 = campaign start); the engine maps it onto the calendar — do not convert to months/years yourself", ge=1)],
    hour: Annotated[int, Field(description="Hour of day (0-23)", ge=0, le=23)] = 8,
    minute: Annotated[int, Field(description="Minute (0-59)", ge=0, le=59)] = 0,
    date_display: Annotated[str | None, Field(description="Narrative date for display (e.g. 'Dawn — first morning in Barovia'); derived from the structured time if omitted")] = None,
) -> str:
    """Set the campaign timeline clock to a specific day and time. Anchors the timeline."""
    tracker = storage.timeline_tracker
    if tracker is None:
        return _TIMELINE_UNAVAILABLE

    new_time = day_number_to_game_time(day, hour=hour, minute=minute)
    tracker.set_time(new_time)
    tracker.anchored = True
    tracker.save()

    display = date_display or format_day_relative(new_time)
    storage.update_game_state(current_date_in_game=display)
    return (
        f"Timeline clock set to {format_day_relative(new_time)}. "
        f"In-game date display: '{display}'"
    )


@mcp.tool
def advance_game_time(
    amount: Annotated[int, Field(description="How much time passes", ge=1)],
    unit: Annotated[Literal["round", "minute", "hour", "day", "week", "month"], Field(description="Time unit")],
    date_display: Annotated[str | None, Field(description="Narrative date for display; derived from the new structured time if omitted")] = None,
) -> str:
    """Advance the campaign timeline clock (travel, rests, scene transitions)."""
    tracker = storage.timeline_tracker
    if tracker is None:
        return _TIMELINE_UNAVAILABLE
    if not tracker.anchored:
        return (
            "Timeline clock is not anchored yet — anchor it first with set_game_time "
            "(e.g. set_game_time(day=2, hour=6) for 'Day 2, dawn')."
        )

    old_time = tracker.get_current_time()
    new_time = tracker.advance_time(amount, TimeUnit(unit))
    tracker.save()

    display = date_display or format_day_relative(new_time)
    storage.update_game_state(current_date_in_game=display)
    return (
        f"Timeline clock advanced: {format_day_relative(old_time)} → "
        f"{format_day_relative(new_time)}. In-game date display: '{display}'"
    )


@mcp.tool
def get_timeline(
    from_day: Annotated[int | None, Field(description="Start of a day range to query (campaign day number)", ge=1)] = None,
    to_day: Annotated[int | None, Field(description="End of the day range (defaults to from_day, i.e. a single day)", ge=1)] = None,
    limit: Annotated[int, Field(description="Max recent events to show when no range is given", ge=1)] = 10,
) -> str:
    """Show the campaign timeline clock and query events at or between game days."""
    tracker = storage.timeline_tracker
    if tracker is None:
        return _TIMELINE_UNAVAILABLE

    current = tracker.get_current_time()
    anchored_text = (
        "anchored"
        if tracker.anchored
        else "NOT anchored — anchor with set_game_time before logging events"
    )
    lines = [
        "**Campaign Timeline**",
        f"**Clock:** {format_day_relative(current)} ({anchored_text})",
        f"**Events recorded:** {tracker.event_count}",
        "",
    ]

    if from_day is not None:
        end_day = to_day if to_day is not None else from_day
        start = day_number_to_game_time(from_day, hour=0, minute=0)
        end = day_number_to_game_time(end_day, hour=23, minute=59)
        events = tracker.get_events_between(start, end)
        lines.append(
            f"**Events on Day {from_day}:**"
            if end_day == from_day
            else f"**Events from Day {from_day} to Day {end_day}:**"
        )
    else:
        events = tracker.events[-limit:]
        lines.append(f"**Most recent events (up to {limit}):**")

    if not events:
        lines.append("(none)")
    for e in events:
        location_text = f" — {e.location}" if e.location else ""
        chars_text = f" [{', '.join(e.characters_involved)}]" if e.characters_involved else ""
        lines.append(
            f"- {format_day_relative(e.game_time)}: {e.description}"
            f"{location_text}{chars_text} (session {e.real_session})"
        )

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_timeline_wiring.py -v -k "not range_query and not single_day"`
Expected: PASS (range/single-day queries need Task 4's stamping hook).

- [ ] **Step 5: Commit**

```bash
git add src/dm20_protocol/main.py tests/test_timeline_wiring.py
git commit -m "feat(DM2-11): set/advance/query MCP tools for the campaign timeline"
```

---

### Task 4: `main.py` — journal stamping hook, prose-only nudge, clock in game state

**Files:**
- Modify: `src/dm20_protocol/main.py` (`add_event` ~line 2820, `update_game_state` ~line 1691, `get_game_state` ~line 1721, helper next to `_ingest_to_fact_graph` ~line 1399)
- Test: `tests/test_timeline_wiring.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_timeline_wiring.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_timeline_wiring.py -v -k "JournalStamping or ProseOnlyNudge or GameStateClock"`
Expected: FAIL — stamping assertions (`event_count == 0`), missing nudge/clock strings.

- [ ] **Step 3: Implement in `main.py`**

3a. After `_ingest_to_fact_graph` (~line 1419), add:

```python
def _stamp_timeline(event: AdventureEvent) -> str:
    """Best-effort timeline stamp for a journal write.

    The journal write has already succeeded when this runs; timeline failures
    are logged and swallowed so they never break the primary write. The stamp
    carries the tracker's current_time at write time — the engine, not the
    LLM, supplies the GameTime (DM2-6 date-model spike).

    Returns:
        Suffix for the tool response ("" when the timeline is unavailable).
    """
    try:
        tracker = storage.timeline_tracker
        if tracker is None:
            return ""
        if not tracker.anchored:
            return (
                "\n⏳ Not stamped on the timeline — the clock is unanchored. "
                "Anchor it with set_game_time first."
            )

        timeline_event = TimelineEvent(
            id=f"tl_{event.id}",
            game_time=tracker.get_current_time(),
            real_session=event.session_number or _current_session_number(),
            description=event.description,
            location=event.location,
            characters_involved=list(event.characters_involved),
            fact_ids=[f"evt_{event.id}"],
        )
        is_valid, error = tracker.validate_temporal_order(timeline_event)
        tracker.add_event(timeline_event)
        tracker.save()

        suffix = f"\n🕐 Timeline: {format_day_relative(timeline_event.game_time)}"
        if not is_valid:
            suffix += (
                f"\n⚠️ Temporal conflict: {error}. If time has passed since the "
                "last event, advance the clock with advance_game_time."
            )
        return suffix
    except Exception as e:
        logger.warning(f"Timeline stamping failed (primary write unaffected): {e}")
        return ""
```

(`TimelineEvent` comes from the module-level import added in Task 3.)

3b. In `add_event` (~line 2845), replace the tail:

```python
    storage.add_event(event)
    _ingest_to_fact_graph(
        lambda ingest: ingest.ingest_event(
            event,
            npcs_by_name=_registered_npcs_by_name(),
            default_session=_current_session_number(),
        )
    )
    return f"Added {event_type.lower()} event: '{resolved_title}'" + _stamp_timeline(event)
```

3c. In `update_game_state` (~line 1717), replace the tail:

```python
    storage.update_game_state(**kwargs)
    response = "Updated game state"
    if current_date_in_game is not None and storage.timeline_tracker is not None:
        response += (
            "\nNote: the timeline clock did not advance — the in-game date prose "
            "is display-only. Use advance_game_time or set_game_time to move "
            "structured time."
        )
    return response
```

(`set_game_time`/`advance_game_time` write the display via `storage.update_game_state` directly, so they never trigger this nudge.)

3d. In `get_game_state` (~line 1746), before the `state_info = f"""...` block, add:

```python
    timeline_line = ""
    tracker = storage.timeline_tracker
    if tracker is not None:
        anchored_text = "anchored" if tracker.anchored else "not anchored"
        timeline_line = (
            f"\n**Timeline Clock:** "
            f"{format_day_relative(tracker.get_current_time())} ({anchored_text})"
        )
```

and change the Date line of the f-string from:

```
**Date (In-Game):** {game_state.current_date_in_game or 'Unknown'}
```

to:

```
**Date (In-Game):** {game_state.current_date_in_game or 'Unknown'}{timeline_line}
```

- [ ] **Step 4: Run the full wiring suite plus neighbors**

Run: `uv run pytest tests/test_timeline_wiring.py tests/test_timeline_lifecycle.py tests/claudmaster/test_timeline.py tests/test_fact_dual_write.py -v`
Expected: all PASS, including the Task 3 range-query tests that needed stamping.

- [ ] **Step 5: Commit**

```bash
git add src/dm20_protocol/main.py tests/test_timeline_wiring.py
git commit -m "feat(DM2-11): stamp journal writes on the timeline and surface the clock"
```

---

### Task 5: Prompt activation (`/dm:start` anchoring step, persona PERSIST)

**Files:**
- Modify: `.claude/commands/dm/start.md` (allowed-tools line 4; resume flow "Step 4. Check for Existing Session")
- Modify: `.claude/dm-persona.md` (PERSIST list ~line 43; Rest pattern ~line 64)

- [ ] **Step 1: Update `start.md` allowed-tools**

In the `allowed-tools:` front-matter line, append after `mcp__dm20-protocol__get_events`:

```
, mcp__dm20-protocol__set_game_time, mcp__dm20-protocol__get_timeline
```

- [ ] **Step 2: Insert the anchoring step in the resume flow**

In section "### 4. Check for Existing Session", under "**If resuming (previous session exists):**", insert a new item after item 4 (the recap fallback) and renumber the current items 5-8 to 6-9:

```markdown
5. **Anchor the timeline clock (one-time, before any event writes):** `get_game_state` includes a `Timeline Clock` line. If it says **not anchored**:
   - If the in-game date matches "Day N" prose (e.g. "Day 2, early morning"), call `set_game_time(day=N)`, adding `hour` if the prose implies a time of day (dawn ≈ 6, morning ≈ 9, midday ≈ 12, evening ≈ 18, night ≈ 22).
   - Otherwise estimate the campaign day from the recap/session notes, or ask the player, then call `set_game_time`.
   - If there is no in-game date at all, call `set_game_time(day=1)`.
   - If already anchored (or the clock line is absent), skip this step. Never call `add_event` before the clock is anchored — unanchored writes are not stamped on the timeline.
```

- [ ] **Step 3: Update the persona's PERSIST step and Rest pattern**

In `.claude/dm-persona.md`, in the "### 4. PERSIST" list, after the `update_game_state` line, add:

```markdown
- `advance_game_time` -- move the timeline clock when narrative time passes (travel, rests, scene transitions). The in-game date prose is display-only; it never moves the clock. If `add_event` returns a temporal-conflict warning, advance the clock before logging further events.
```

In the "**Rest**" tool-usage pattern line, change:

```markdown
**Rest**: `get_character` -> `update_character` (restore HP, spell slots per rest rules) -> `add_event` -> narrate rest scene
```

to:

```markdown
**Rest**: `get_character` -> `update_character` (restore HP, spell slots per rest rules) -> `advance_game_time` (long rest: 8 hours; short rest: 1 hour) -> `add_event` -> narrate rest scene
```

- [ ] **Step 4: Verify**

Read both files back; confirm the resume flow numbering is consistent (items 1-9), the allowed-tools line parses (comma-separated, no trailing comma), and no other sections reference the old numbering.

- [ ] **Step 5: Commit**

```bash
git add .claude/commands/dm/start.md .claude/dm-persona.md
git commit -m "feat(DM2-11): prompt activation for timeline anchoring and clock advancement"
```

---

### Task 6: Focused regression pass

- [ ] **Step 1: Run the focused suites for everything this branch touched**

Run: `uv run pytest tests/test_timeline_wiring.py tests/test_timeline_lifecycle.py tests/claudmaster/test_timeline.py tests/test_fact_dual_write.py tests/test_fact_graph_lifecycle.py tests/test_main.py tests/test_storage.py -v`
Expected: all PASS. (The repo's *full* pytest suite has ~143 pre-existing interaction failures on main — judge only these focused suites.)

- [ ] **Step 2: Fix anything red, re-run, commit fixes if needed**
