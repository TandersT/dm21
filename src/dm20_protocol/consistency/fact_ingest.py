"""
FactIngest: idempotent ingestion from journal/entity stores into the fact graph.

This adapter is the single write pipeline into the fact graph (FactDatabase +
NPCKnowledgeTracker). It is used both live — dual-write from the MCP tools
after each journal/entity write — and retroactively via the sync_facts tool,
which replays the existing journal and sweeps campaign entities.

Core properties:

- Deterministic fact ids (``evt_<event.id>``, NPC fact id == NPC entity id,
  ``loc_<location.id>``, ``quest_<quest.id>``) make re-ingestion converge
  instead of duplicating. NPC id equality is required by
  ``SessionRecapGenerator._get_npc_reminders``, which joins NPC facts to
  tracker interactions by id.
- Merge-preserve upsert: fields derivable from the journal/entity stores
  (content, category, relevance for events, quest resolution tags) are
  refreshed; everything attached by other subsystems and NOT derivable
  (``party_known`` tags, ``related_facts`` links, first-established
  session/timestamp) is preserved.
- Ingest methods mutate in memory only; callers invoke :meth:`FactIngest.save`
  once per tool invocation (``FactDatabase.add_fact`` does not autosave).

Note: importing this module imports the ``claudmaster`` package. Callers in
core modules (main.py, storage.py) import it function-locally, matching the
existing lazy-claudmaster convention.
"""

import logging
from typing import Mapping

from dm20_protocol.claudmaster.consistency.fact_database import FactDatabase
from dm20_protocol.claudmaster.consistency.models import (
    Fact,
    FactCategory,
    PlayerInteraction,
)
from dm20_protocol.claudmaster.consistency.npc_knowledge import NPCKnowledgeTracker
from dm20_protocol.models import AdventureEvent, EventType, Location, NPC, Quest

logger = logging.getLogger("dm20-protocol")

# Quest-status tags managed by ingestion. On quest re-ingest, managed tags that
# are no longer derived from the quest status are removed (a quest flipped back
# to active sheds its stale resolution tag); all other tags are preserved.
# get_active_threads() filters unresolved threads on these tags.
QUEST_RESOLUTION_TAGS = frozenset({"completed", "failed"})

_EVENT_CATEGORY_OVERRIDES = {
    EventType.QUEST: FactCategory.QUEST,
    EventType.WORLD: FactCategory.WORLD,
}

_INTERACTION_TYPE_OVERRIDES = {
    EventType.COMBAT: "combat",
    EventType.ROLEPLAY: "conversation",
    EventType.SOCIAL: "conversation",
}


class FactIngest:
    """Maps journal events and campaign entities onto the fact graph."""

    def __init__(
        self,
        fact_db: FactDatabase,
        npc_tracker: NPCKnowledgeTracker | None = None,
    ) -> None:
        """
        Args:
            fact_db: The fact database to upsert facts into.
            npc_tracker: Optional tracker for met-tracking (PlayerInteraction
                records). When None, event ingestion skips interaction recording.
        """
        self._fact_db = fact_db
        self._npc_tracker = npc_tracker

    def ingest_event(
        self,
        event: AdventureEvent,
        npcs_by_name: Mapping[str, NPC] | None = None,
        default_session: int = 1,
    ) -> str:
        """
        Upsert a journal event as a fact and record NPC met-tracking.

        Mapping: quest→QUEST, world→WORLD, else EVENT; relevance =
        importance/5; tags and session carried over (session falls back to
        ``default_session`` when the event has none).

        Args:
            event: The journal event to ingest.
            npcs_by_name: Registered NPCs keyed by name; names in
                ``event.characters_involved`` matching an NPC (case-insensitive)
                get a PlayerInteraction recorded on the tracker.
            default_session: Session to attribute when the event has none.

        Returns:
            The fact id (``evt_<event.id>``).
        """
        session = event.session_number or default_session
        fact_id = self._upsert_fact(
            fact_id=f"evt_{event.id}",
            category=_EVENT_CATEGORY_OVERRIDES.get(event.event_type, FactCategory.EVENT),
            content=event.description,
            session=session,
            relevance=event.importance / 5,
            tags=list(event.tags),
            source="adventure_log",
            timestamp=event.timestamp,
        )
        self._record_npc_interactions(event, npcs_by_name or {}, session)
        return fact_id

    def ingest_npc(self, npc: NPC, session: int = 1) -> str:
        """Upsert an NPC entity as a fact (fact id == NPC entity id)."""
        content = f"{npc.name} — {npc.description}" if npc.description else npc.name
        return self._upsert_fact(
            fact_id=npc.id,
            category=FactCategory.NPC,
            content=content,
            session=session,
            relevance=None,
            tags=[],
            source="campaign",
        )

    def ingest_location(self, location: Location, session: int = 1) -> str:
        """Upsert a location entity as a fact (``loc_<location.id>``)."""
        return self._upsert_fact(
            fact_id=f"loc_{location.id}",
            category=FactCategory.LOCATION,
            content=f"{location.name} ({location.location_type}): {location.description}",
            session=session,
            relevance=None,
            tags=[],
            source="campaign",
        )

    def ingest_quest(self, quest: Quest, session: int = 1) -> str:
        """
        Upsert a quest entity as a fact (``quest_<quest.id>``).

        Resolved statuses (completed/failed) derive the matching resolution
        tag so ``get_active_threads`` filtering works; re-ingestion refreshes
        the managed tags to reflect the current status.
        """
        tags = [quest.status] if quest.status in QUEST_RESOLUTION_TAGS else []
        return self._upsert_fact(
            fact_id=f"quest_{quest.id}",
            category=FactCategory.QUEST,
            content=f"{quest.title}: {quest.description}",
            session=session,
            relevance=None,
            tags=tags,
            source="campaign",
            managed_tags=QUEST_RESOLUTION_TAGS,
        )

    def save(self) -> None:
        """Persist the fact database and (if present) the NPC tracker."""
        self._fact_db.save()
        if self._npc_tracker is not None:
            self._npc_tracker.save()

    def _upsert_fact(
        self,
        *,
        fact_id: str,
        category: FactCategory,
        content: str,
        session: int,
        relevance: float | None,
        tags: list[str],
        source: str,
        timestamp=None,
        managed_tags: frozenset[str] = frozenset(),
    ) -> str:
        """
        Create or merge a fact with the given deterministic id.

        Derived fields (content, category, source, relevance when given, and
        managed tags) are refreshed; non-derivable state (related_facts,
        foreign tags, first-established session/timestamp) is preserved.
        ``relevance=None`` means "not derivable": existing score is kept, new
        facts get the model default (1.0).
        """
        existing = self._fact_db.get_fact(fact_id)
        if existing is None:
            fact = Fact(
                id=fact_id,
                category=category,
                content=content,
                session_number=max(1, session),
                tags=list(tags),
                source=source,
                **({"relevance_score": relevance} if relevance is not None else {}),
                **({"timestamp": timestamp} if timestamp is not None else {}),
            )
            self._fact_db.add_fact(fact)
            return fact_id

        existing.category = category
        existing.content = content
        existing.source = source
        if relevance is not None:
            existing.relevance_score = relevance
        derived = list(tags)
        existing.tags = derived + [
            t for t in existing.tags if t not in derived and t not in managed_tags
        ]
        return fact_id

    def _record_npc_interactions(
        self,
        event: AdventureEvent,
        npcs_by_name: Mapping[str, NPC],
        session: int,
    ) -> None:
        """Record a PlayerInteraction for each registered NPC in the event."""
        if self._npc_tracker is None or not event.characters_involved:
            return

        lookup = {name.lower(): npc for name, npc in npcs_by_name.items()}
        matched: list[NPC] = []
        players: list[str] = []
        for name in event.characters_involved:
            npc = lookup.get(name.lower())
            if npc is not None:
                matched.append(npc)
            else:
                players.append(name)

        if not matched:
            return

        # The embedded event fact id is the idempotency key: PlayerInteraction
        # has no id field, so re-ingestion dedupes on the summary marker.
        summary = f"{event.title} [evt_{event.id}]"
        interaction_type = _INTERACTION_TYPE_OVERRIDES.get(
            event.event_type, event.event_type.value
        )

        for npc in matched:
            already_recorded = any(
                i.summary == summary
                for i in self._npc_tracker.get_interactions(npc.id)
            )
            if already_recorded:
                continue
            self._npc_tracker.record_interaction(
                npc.id,
                PlayerInteraction(
                    session_number=max(1, session),
                    timestamp=event.timestamp,
                    interaction_type=interaction_type,
                    summary=summary,
                    player_characters=players,
                    location=event.location or "",
                ),
            )


__all__ = [
    "FactIngest",
    "QUEST_RESOLUTION_TAGS",
]
