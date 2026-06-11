"""
Session recap generation for narrative continuity.

This module provides tools for generating "Previously on..." style recaps
to help resume gameplay sessions with context and continuity.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dm20_protocol.claudmaster.consistency.fact_database import FactDatabase
    from dm20_protocol.claudmaster.consistency.npc_knowledge import NPCKnowledgeTracker
    from dm20_protocol.claudmaster.consistency.timeline import TimelineTracker
    from dm20_protocol.models import AdventureEvent

from dm20_protocol.claudmaster.consistency.models import Fact, FactCategory

logger = logging.getLogger("dm20-protocol")

# Accepted values for generate_recap's length/style parameters. Callers
# validating user input should check against these.
RECAP_LENGTHS = ("brief", "standard", "detailed")
RECAP_STYLES = ("narrative", "bullet", "mixed")


@dataclass
class StoryThread:
    """
    An unresolved plot thread in the campaign.

    Story threads represent ongoing narrative elements that haven't been
    resolved, such as active quests, open mysteries, or pending conflicts.

    Attributes:
        thread_id: Unique identifier for the thread
        description: Human-readable description of the thread
        related_fact_ids: IDs of facts related to this thread
        started_session: Session number when thread began
        importance: Importance score (0.0-1.0)
    """
    thread_id: str
    description: str
    related_fact_ids: list[str] = field(default_factory=list)
    started_session: int = 1
    importance: float = 1.0


@dataclass
class QuestSummary:
    """
    Summary of an active quest.

    Attributes:
        quest_name: Name of the quest
        status: Current status (active, completed, failed)
        key_objectives: List of main objectives
        progress_notes: Brief notes on progress
    """
    quest_name: str
    status: str
    key_objectives: list[str] = field(default_factory=list)
    progress_notes: str = ""


@dataclass
class SessionRecap:
    """
    Complete recap for session continuity.

    Provides a comprehensive overview of the campaign state to help
    players and DM resume gameplay with full context.

    Attributes:
        previously_on: Narrative "Previously on..." summary
        key_events: List of important events that occurred
        active_quests: List of active quest summaries
        unresolved_threads: List of unresolved story threads (as strings)
        current_situation: Description of current party situation
        party_status: Brief party condition summary
        npc_reminders: Important NPC relationship reminders
        suggested_hooks: Where to pick up the story next
        verbatim_events: The session's journal events, untruncated, sorted by
            importance (desc) then timestamp — exact established detail the
            summary sections must not contradict
    """
    previously_on: str = ""
    key_events: list[str] = field(default_factory=list)
    active_quests: list[QuestSummary] = field(default_factory=list)
    unresolved_threads: list[str] = field(default_factory=list)
    current_situation: str = ""
    party_status: str = ""
    npc_reminders: list[str] = field(default_factory=list)
    suggested_hooks: list[str] = field(default_factory=list)
    verbatim_events: list[AdventureEvent] = field(default_factory=list)


class SessionRecapGenerator:
    """
    Generates narrative recaps for session continuity.

    The SessionRecapGenerator analyzes facts, NPC interactions, and timeline
    events to create comprehensive session recaps in various styles and lengths.

    Attributes:
        facts: Fact database for querying campaign facts
        npc_tracker: Optional NPC knowledge tracker for relationship context
        timeline: Optional timeline tracker for temporal context
    """

    def __init__(
        self,
        fact_database: FactDatabase,
        npc_tracker: NPCKnowledgeTracker | None = None,
        timeline: TimelineTracker | None = None
    ) -> None:
        """
        Initialize the recap generator.

        Args:
            fact_database: Fact database for querying campaign facts
            npc_tracker: Optional NPC knowledge tracker
            timeline: Optional timeline tracker
        """
        self.facts = fact_database
        self.npc_tracker = npc_tracker
        self.timeline = timeline

    def generate_recap(
        self,
        session_number: int,
        length: str = "standard",
        style: str = "narrative",
        events: list[AdventureEvent] | None = None,
    ) -> SessionRecap:
        """
        Generate a session recap.

        Creates a comprehensive recap of the specified session with
        configurable length and style.

        Args:
            session_number: Session number to generate recap for
            length: Recap length - "brief", "standard", or "detailed"
            style: Presentation style - "narrative", "bullet", or "mixed"
            events: The session's journal events, injected by the caller
                (this layer is storage-free). Carried verbatim on the recap,
                sorted by importance (desc) then timestamp.

        Returns:
            Complete SessionRecap object
        """
        logger.info(
            f"Generating {length} {style} recap for session {session_number}"
        )

        # Gather key events
        key_events_facts = self.get_key_events(session_number)
        key_events = [fact.content for fact in key_events_facts]

        # Build narrative summary
        previously_on = self._build_previously_on(key_events_facts, length, style)

        # Get active story threads
        active_threads = self.get_active_threads(session_number)
        unresolved_threads = [thread.description for thread in active_threads]

        # Get current situation
        current_situation = self.get_situation_summary(session_number)

        # Get active quests
        active_quests = self._get_active_quests(session_number)

        # Get NPC reminders if tracker available
        npc_reminders = self._get_npc_reminders(session_number)

        # Generate suggested hooks
        suggested_hooks = self._generate_hooks(active_threads, key_events_facts)

        # Build party status
        party_status = self._get_party_status(session_number)

        # Carry the session's journal events verbatim: most important first,
        # chronological within equal importance
        verbatim_events = sorted(
            events or [], key=lambda e: (-e.importance, e.timestamp)
        )

        recap = SessionRecap(
            previously_on=previously_on,
            key_events=key_events,
            active_quests=active_quests,
            unresolved_threads=unresolved_threads,
            current_situation=current_situation,
            party_status=party_status,
            npc_reminders=npc_reminders,
            suggested_hooks=suggested_hooks,
            verbatim_events=verbatim_events,
        )

        logger.info(
            f"Generated recap with {len(key_events)} events, "
            f"{len(active_quests)} quests, {len(unresolved_threads)} threads"
        )

        return recap

    def get_key_events(self, session_number: int, limit: int = 5) -> list[Fact]:
        """
        Extract most important events from a session.

        Queries the fact database for events from the specified session,
        sorted by relevance score.

        Args:
            session_number: Session to query
            limit: Maximum number of events to return

        Returns:
            List of Fact objects sorted by relevance (highest first)
        """
        # Query event facts from this session
        events = self.facts.query_facts(
            category=FactCategory.EVENT,
            session=session_number,
            limit=limit * 2  # Get extra to filter
        )

        # Sort by relevance (already done by query_facts)
        # and take top N
        return events[:limit]

    def get_active_threads(self, session_number: int) -> list[StoryThread]:
        """
        Identify unresolved plot threads.

        Finds quest-category facts that don't have resolution indicators
        (tags like "completed", "resolved", "failed") and groups them into
        story threads.

        Args:
            session_number: Session to query up to (inclusive)

        Returns:
            List of StoryThread objects for unresolved threads
        """
        # Query all quest facts up to this session
        all_quests = self.facts.query_facts(
            category=FactCategory.QUEST,
            limit=100
        )

        # Filter to quests from sessions <= session_number
        relevant_quests = [
            q for q in all_quests
            if q.session_number <= session_number
        ]

        # Group by related_facts to identify threads
        threads: dict[str, list[Fact]] = {}
        standalone_quests: list[Fact] = []

        for quest in relevant_quests:
            # Check if quest is resolved
            resolution_tags = {"completed", "resolved", "failed", "abandoned"}
            if any(tag in quest.tags for tag in resolution_tags):
                continue  # Skip resolved quests

            # Group by related facts or keep standalone
            if quest.related_facts:
                # Use first related fact as thread key
                thread_key = quest.related_facts[0]
                if thread_key not in threads:
                    threads[thread_key] = []
                threads[thread_key].append(quest)
            else:
                standalone_quests.append(quest)

        # Build StoryThread objects
        story_threads: list[StoryThread] = []

        # Add grouped threads
        for thread_key, quest_facts in threads.items():
            # Find earliest session
            started = min(q.session_number for q in quest_facts)
            # Use highest relevance as importance
            importance = max(q.relevance_score for q in quest_facts)
            # Combine content for description
            description = "; ".join(q.content for q in quest_facts)

            thread = StoryThread(
                thread_id=thread_key,
                description=description,
                related_fact_ids=[q.id for q in quest_facts],
                started_session=started,
                importance=importance
            )
            story_threads.append(thread)

        # Add standalone quests as individual threads
        for quest in standalone_quests:
            thread = StoryThread(
                thread_id=quest.id,
                description=quest.content,
                related_fact_ids=[quest.id],
                started_session=quest.session_number,
                importance=quest.relevance_score
            )
            story_threads.append(thread)

        # Sort by importance
        story_threads.sort(key=lambda t: t.importance, reverse=True)

        return story_threads

    def get_situation_summary(self, session_number: int) -> str:
        """
        Describe current party situation based on latest facts.

        Queries location facts from the session to determine where
        the party is and what they're doing.

        Args:
            session_number: Session to query

        Returns:
            String describing current party situation
        """
        # Query location facts from this session
        locations = self.facts.query_facts(
            category=FactCategory.LOCATION,
            session=session_number,
            limit=5
        )

        if not locations:
            # Fall back to recent event facts
            recent_events = self.facts.query_facts(
                category=FactCategory.EVENT,
                session=session_number,
                limit=3
            )
            if recent_events:
                # Use most recent event as situation
                return recent_events[0].content
            return "The party's current situation is unclear."

        # Use most relevant/recent location
        latest_location = locations[0]
        return f"The party is at {latest_location.content}"

    def _build_previously_on(
        self,
        events: list[Fact],
        length: str,
        style: str
    ) -> str:
        """
        Build the 'Previously on...' narrative from key events.

        Args:
            events: List of event facts to summarize
            length: "brief", "standard", or "detailed"
            style: "narrative", "bullet", or "mixed"

        Returns:
            Formatted narrative string
        """
        if not events:
            return "The adventure continues..."

        # Determine how many events to include based on length
        event_limits = {
            "brief": 2,
            "standard": 3,
            "detailed": 5
        }
        limit = event_limits.get(length, 3)
        selected_events = events[:limit]

        if style == "bullet":
            # Bullet point format
            bullets = [f"- {event.content}" for event in selected_events]
            return "\n".join(bullets)

        elif style == "narrative":
            # Flowing prose format
            if length == "brief":
                # Very concise: ~75 words
                if len(selected_events) == 1:
                    return f"Previously: {selected_events[0].content}."
                else:
                    parts = " ".join(e.content for e in selected_events)
                    return f"Previously: {parts}"

            elif length == "standard":
                # Standard: ~200 words
                intro = "Previously on this adventure: "
                parts = []
                for i, event in enumerate(selected_events):
                    if i == 0:
                        parts.append(event.content)
                    elif i == len(selected_events) - 1:
                        parts.append(f"Finally, {event.content.lower()}")
                    else:
                        parts.append(f"Then, {event.content.lower()}")
                return intro + ". ".join(parts) + "."

            else:  # detailed
                # Detailed: ~400 words
                intro = "When we last left our heroes: "
                narrative_parts = []
                for i, event in enumerate(selected_events):
                    content = event.content
                    if i > 0:
                        content = content[0].lower() + content[1:]
                    narrative_parts.append(content)

                # Connect with varied transitions
                transitions = ["Subsequently", "Following this", "Afterward", "Then"]
                formatted_parts = [narrative_parts[0]]
                for i, part in enumerate(narrative_parts[1:], 1):
                    if i < len(transitions):
                        formatted_parts.append(f"{transitions[i-1]}, {part}")
                    else:
                        formatted_parts.append(f"Finally, {part}")

                return intro + ". ".join(formatted_parts) + "."

        else:  # mixed
            # Narrative intro + bullet details
            intro = "Previously on this adventure:\n\n"
            bullets = [f"- {event.content}" for event in selected_events]
            return intro + "\n".join(bullets)

    def _get_npc_reminders(self, session_number: int) -> list[str]:
        """
        Get NPC interaction reminders from the session.

        Args:
            session_number: Session to query

        Returns:
            List of reminder strings about NPC interactions
        """
        if not self.npc_tracker:
            return []

        reminders: list[str] = []

        # Query NPC facts from this session
        npc_facts = self.facts.query_facts(
            category=FactCategory.NPC,
            session=session_number,
            limit=10
        )

        # Get NPC IDs from the facts
        npc_ids = {fact.id for fact in npc_facts if fact.id}

        # Get interactions for each NPC
        for npc_id in npc_ids:
            interactions = self.npc_tracker.get_interactions(
                npc_id=npc_id,
                session=session_number
            )

            if interactions:
                # Find the NPC fact to get name
                npc_fact = self.facts.get_fact(npc_id)
                npc_name = npc_fact.content if npc_fact else "Unknown NPC"

                # Summarize interactions
                interaction_types = [i.interaction_type for i in interactions]
                if interaction_types:
                    types_str = ", ".join(set(interaction_types))
                    reminders.append(
                        f"{npc_name}: {types_str} ({len(interactions)} interaction(s))"
                    )

        return reminders[:5]  # Limit to top 5

    def _get_active_quests(self, session_number: int) -> list[QuestSummary]:
        """
        Get summaries of active quests.

        Args:
            session_number: Session to query up to

        Returns:
            List of QuestSummary objects
        """
        # Get active threads (which are based on quests)
        threads = self.get_active_threads(session_number)

        quests: list[QuestSummary] = []

        for thread in threads[:5]:  # Limit to 5 most important
            # Extract quest name from description (first sentence or up to 50 chars)
            quest_name = thread.description.split(".")[0][:50]
            if len(thread.description.split(".")[0]) > 50:
                quest_name += "..."

            # Determine status (all are active if they're in active threads)
            status = "active"

            # Get related facts to build objectives
            objectives: list[str] = []
            for fact_id in thread.related_fact_ids[:3]:  # Max 3 objectives
                fact = self.facts.get_fact(fact_id)
                if fact:
                    # Use tags as objectives if available
                    if fact.tags:
                        objectives.extend(fact.tags[:2])
                    elif len(objectives) < 3:
                        # Otherwise use content snippet
                        objectives.append(fact.content[:40] + "...")

            # Build progress notes
            progress_notes = f"Started in session {thread.started_session}"

            quest = QuestSummary(
                quest_name=quest_name,
                status=status,
                key_objectives=objectives,
                progress_notes=progress_notes
            )
            quests.append(quest)

        return quests

    def _generate_hooks(
        self,
        threads: list[StoryThread],
        recent_events: list[Fact]
    ) -> list[str]:
        """
        Generate suggested hooks for resuming play.

        Args:
            threads: Active story threads
            recent_events: Recent event facts

        Returns:
            List of suggested hooks
        """
        hooks: list[str] = []

        # Add hooks from unresolved threads
        for thread in threads[:3]:  # Top 3 most important
            hook = f"Continue investigating: {thread.description[:60]}"
            if len(thread.description) > 60:
                hook += "..."
            hooks.append(hook)

        # Add hooks from recent events
        for event in recent_events[:2]:
            if "unresolved" in event.tags or "cliffhanger" in event.tags:
                hooks.append(f"Resolve: {event.content[:60]}...")

        # Generic fallback if no specific hooks
        if not hooks:
            hooks.append("Continue exploring the current area")
            hooks.append("Follow up on recent discoveries")

        return hooks[:5]  # Limit to 5 hooks

    def _get_party_status(self, session_number: int) -> str:
        """
        Get party status summary.

        Args:
            session_number: Session to query

        Returns:
            Brief party status string
        """
        # Query recent events for status indicators
        events = self.facts.query_facts(
            category=FactCategory.EVENT,
            session=session_number,
            limit=10
        )

        # Look for status-related tags
        status_tags = []
        for event in events:
            relevant_tags = [
                tag for tag in event.tags
                if tag in {"combat", "injured", "resting", "traveling", "safe", "danger"}
            ]
            status_tags.extend(relevant_tags)

        if not status_tags:
            return "Party status: Ready for adventure"

        # Build status from tags
        if "danger" in status_tags or "combat" in status_tags:
            return "Party status: In danger or combat"
        elif "injured" in status_tags:
            return "Party status: Recovering from injuries"
        elif "resting" in status_tags:
            return "Party status: Resting"
        elif "traveling" in status_tags:
            return "Party status: Traveling"
        elif "safe" in status_tags:
            return "Party status: Safe and ready"
        else:
            return "Party status: Ready for adventure"


__all__ = [
    "SessionRecapGenerator",
    "SessionRecap",
    "StoryThread",
    "QuestSummary",
    "RECAP_LENGTHS",
    "RECAP_STYLES",
]
