"""
Contradiction detection for the consistency tracking system.

This module provides the ContradictionDetector class, which checks new narrative
statements against established facts to detect inconsistencies. It uses keyword-based
heuristics to identify potential contradictions and suggests resolution strategies.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .fact_database import FactDatabase
from .models import (
    Contradiction,
    ContradictionSeverity,
    ContradictionType,
    FactCategory,
    ResolutionStrategy,
    ResolutionSuggestion,
)
from .npc_knowledge import NPCKnowledgeTracker

logger = logging.getLogger("dm20-protocol")

# Stop words to filter out when extracting keywords
STOP_WORDS = {
    "a", "an", "the", "is", "was", "were", "are", "been", "be", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "should",
    "could", "may", "might", "must", "can", "to", "of", "in", "on", "at",
    "by", "for", "with", "about", "as", "from", "into", "through", "during",
    "before", "after", "above", "below", "between", "under", "again", "further",
    "then", "once", "here", "there", "when", "where", "why", "how", "all",
    "both", "each", "few", "more", "most", "other", "some", "such", "no",
    "nor", "not", "only", "own", "same", "so", "than", "too", "very", "s",
    "t", "just", "now", "d", "ll", "m", "o", "re", "ve", "y", "ain", "aren",
    "couldn", "didn", "doesn", "hadn", "hasn", "haven", "isn", "ma", "mightn",
    "mustn", "needn", "shan", "shouldn", "wasn", "weren", "won", "wouldn"
}

# Negation pairs for contradiction detection
NEGATION_PAIRS = [
    ("alive", "dead"),
    ("is", "is not"),
    ("was", "was not"),
    ("has", "has not"),
    ("will", "will not"),
    ("can", "cannot"),
    ("always", "never"),
    ("present", "absent"),
    ("exists", "does not exist"),
    ("true", "false"),
    ("yes", "no"),
    ("open", "closed"),
    ("active", "inactive"),
]


class ContradictionDetector:
    """Detects contradictions between new statements and established facts."""

    def __init__(
        self,
        fact_database: FactDatabase,
        npc_tracker: NPCKnowledgeTracker | None = None,
        campaign_path: Path | None = None
    ) -> None:
        """
        Initialize the contradiction detector.

        Args:
            fact_database: Reference to the fact database for querying facts
            npc_tracker: Optional NPC knowledge tracker for checking NPC statements
            campaign_path: Path to campaign directory for storing contradictions
        """
        self._fact_db = fact_database
        self._npc_tracker = npc_tracker
        self._contradictions: list[Contradiction] = []
        # Detections from non-registering checks, keyed by contradiction id.
        # In-memory only: never serialized, dies with the instance.
        self._pending: dict[str, Contradiction] = {}
        self._campaign_path = campaign_path or fact_database.campaign_path

        # Ensure the campaign directory exists
        Path(self._campaign_path).mkdir(parents=True, exist_ok=True)

        # Load existing contradictions
        self.load()

    @property
    def _contradictions_path(self) -> Path:
        """Path to the contradictions JSON file."""
        return Path(self._campaign_path) / "contradictions.json"

    def _extract_keywords(self, text: str) -> set[str]:
        """
        Extract keywords from text by removing stop words and normalizing.

        Args:
            text: Text to extract keywords from

        Returns:
            Set of lowercase keywords
        """
        # Convert to lowercase and split on non-alphanumeric characters
        words = re.findall(r'\b[a-z]+\b', text.lower())

        # Filter out stop words and short words
        keywords = {word for word in words if word not in STOP_WORDS and len(word) > 2}

        return keywords

    def _check_negation_conflict(self, keywords1: set[str], keywords2: set[str]) -> bool:
        """
        Check if two keyword sets contain negation pairs.

        Args:
            keywords1: First set of keywords
            keywords2: Second set of keywords

        Returns:
            True if negation pairs are detected
        """
        # Combine keywords into single strings for checking phrases
        text1 = " ".join(sorted(keywords1))
        text2 = " ".join(sorted(keywords2))

        for pos, neg in NEGATION_PAIRS:
            if (pos in text1 and neg in text2) or (neg in text1 and pos in text2):
                return True

        return False

    def _detect_numeric_conflict(self, text1: str, text2: str, common_keywords: set[str]) -> bool:
        """
        Check if two texts have different numbers for the same entity.

        Args:
            text1: First text
            text2: Second text
            common_keywords: Keywords common to both texts

        Returns:
            True if numeric conflict detected
        """
        # Extract numbers from both texts
        numbers1 = set(re.findall(r'\b\d+\b', text1))
        numbers2 = set(re.findall(r'\b\d+\b', text2))

        # If they reference similar entities but different numbers, flag it
        if common_keywords and numbers1 and numbers2 and numbers1 != numbers2:
            return True

        return False

    def _classify_contradiction(
        self,
        statement: str,
        fact_content: str,
        category: FactCategory | None
    ) -> tuple[ContradictionType, ContradictionSeverity]:
        """
        Classify the type and severity of a contradiction.

        Args:
            statement: The new statement
            fact_content: The conflicting fact content
            category: Category of the fact

        Returns:
            Tuple of (ContradictionType, ContradictionSeverity)
        """
        statement_lower = statement.lower()
        fact_lower = fact_content.lower()

        # Determine type based on keywords and category
        if category == FactCategory.NPC:
            contradiction_type = ContradictionType.CHARACTER
        elif category == FactCategory.LOCATION:
            contradiction_type = ContradictionType.SPATIAL
        elif any(word in statement_lower for word in ["before", "after", "when", "during", "time"]):
            contradiction_type = ContradictionType.TEMPORAL
        elif any(word in statement_lower for word in ["cannot", "impossible", "never", "always"]):
            contradiction_type = ContradictionType.LOGICAL
        else:
            contradiction_type = ContradictionType.FACTUAL

        # Determine severity based on negation strength and numeric conflicts
        statement_keywords = self._extract_keywords(statement)
        fact_keywords = self._extract_keywords(fact_content)

        has_negation = self._check_negation_conflict(statement_keywords, fact_keywords)
        has_numeric = self._detect_numeric_conflict(statement, fact_content, statement_keywords & fact_keywords)

        if has_negation and has_numeric:
            severity = ContradictionSeverity.CRITICAL
        elif has_negation:
            severity = ContradictionSeverity.MAJOR
        elif has_numeric:
            severity = ContradictionSeverity.MODERATE
        else:
            severity = ContradictionSeverity.MINOR

        return contradiction_type, severity

    def check_statement(
        self,
        statement: str,
        session_number: int,
        category: FactCategory | None = None,
        related_tags: list[str] | None = None,
        register: bool = True,
    ) -> list[Contradiction]:
        """
        Check a new statement against established facts.

        Strategy:
        1. Query facts by category and tags to narrow search
        2. Compare statement text against fact content using keyword overlap
        3. Detect potential contradictions based on conflicting keywords
        4. Classify contradiction type and severity

        Args:
            statement: The new statement to check
            session_number: Current session number
            category: Optional category to filter facts
            related_tags: Optional tags to filter facts
            register: When False, detections are parked in the in-memory
                pending buffer (resolvable via resolve()) instead of the
                registered list, so a pure check never reaches save()

        Returns:
            List of detected contradictions (may be empty)
        """
        detected: list[Contradiction] = []

        # Extract keywords from the new statement
        statement_keywords = self._extract_keywords(statement)

        if not statement_keywords:
            logger.debug("No keywords extracted from statement, skipping check")
            return detected

        # Query relevant facts
        relevant_facts = self._fact_db.query_facts(
            category=category,
            tags=related_tags,
            limit=100
        )

        logger.debug(
            f"Checking statement against {len(relevant_facts)} facts "
            f"(category={category}, tags={related_tags})"
        )

        # Check each fact for contradictions
        for fact in relevant_facts:
            fact_keywords = self._extract_keywords(fact.content)

            # Calculate keyword overlap
            common_keywords = statement_keywords & fact_keywords
            if len(common_keywords) < 2:
                # Not enough overlap to consider a contradiction
                continue

            # Check for contradiction patterns
            has_negation = self._check_negation_conflict(statement_keywords, fact_keywords)
            has_numeric = self._detect_numeric_conflict(statement, fact.content, common_keywords)

            if has_negation or has_numeric:
                # Contradiction detected
                contradiction_type, severity = self._classify_contradiction(
                    statement, fact.content, category
                )

                contradiction = Contradiction(
                    id=f"ctr_{uuid4().hex[:8]}",
                    contradiction_type=contradiction_type,
                    severity=severity,
                    new_statement=statement,
                    conflicting_fact_ids=[fact.id],
                    detected_at=datetime.now(timezone.utc),
                    session_number=session_number,
                    resolved=False
                )

                if register:
                    self._contradictions.append(contradiction)
                else:
                    self._pending[contradiction.id] = contradiction
                detected.append(contradiction)

                logger.warning(
                    f"Detected {severity.value} {contradiction_type.value} contradiction: "
                    f"'{statement}' vs fact {fact.id}"
                )

        return detected

    def check_npc_statement(
        self,
        npc_id: str,
        statement: str,
        session_number: int
    ) -> list[Contradiction]:
        """
        Check if an NPC statement contradicts their known knowledge.

        Uses NPCKnowledgeTracker to verify NPC could plausibly know this.
        Flags if NPC references facts they shouldn't know.

        Args:
            npc_id: ID of the NPC making the statement
            statement: The statement being made
            session_number: Current session number

        Returns:
            List of detected contradictions (may be empty)
        """
        detected: list[Contradiction] = []

        if not self._npc_tracker:
            logger.debug("No NPC tracker available, skipping NPC knowledge check")
            return detected

        # Extract keywords from the statement
        statement_keywords = self._extract_keywords(statement)

        if not statement_keywords:
            return detected

        # Get NPC's known facts
        npc_knowledge = self._npc_tracker.get_npc_knowledge(npc_id)
        known_fact_ids = {entry.fact_id for entry in npc_knowledge}

        # Query all facts that might be referenced
        all_facts = self._fact_db.query_facts(limit=1000)

        for fact in all_facts:
            # Skip facts the NPC already knows
            if fact.id in known_fact_ids:
                continue

            fact_keywords = self._extract_keywords(fact.content)
            common_keywords = statement_keywords & fact_keywords

            # If there's significant overlap, NPC might be referencing this fact
            if len(common_keywords) >= 3:
                # NPC is referencing a fact they shouldn't know
                contradiction = Contradiction(
                    id=f"ctr_{uuid4().hex[:8]}",
                    contradiction_type=ContradictionType.CHARACTER,
                    severity=ContradictionSeverity.MODERATE,
                    new_statement=f"NPC {npc_id}: {statement}",
                    conflicting_fact_ids=[fact.id],
                    detected_at=datetime.now(timezone.utc),
                    session_number=session_number,
                    resolved=False
                )

                self._contradictions.append(contradiction)
                detected.append(contradiction)

                logger.warning(
                    f"NPC {npc_id} referenced fact {fact.id} they shouldn't know"
                )

        return detected

    def suggest_resolution(self, contradiction: Contradiction) -> list[ResolutionSuggestion]:
        """
        Suggest resolution strategies for a contradiction.

        Rules:
        - MINOR → IGNORE with high confidence
        - MODERATE → EXPLAIN or RETCON with medium confidence
        - MAJOR → FLAG_FOR_DM with high confidence, RETCON with low
        - CRITICAL → FLAG_FOR_DM always

        Args:
            contradiction: The contradiction to resolve

        Returns:
            List of suggested resolution strategies, ordered by confidence
        """
        suggestions: list[ResolutionSuggestion] = []

        if contradiction.severity == ContradictionSeverity.MINOR:
            suggestions.append(ResolutionSuggestion(
                strategy=ResolutionStrategy.IGNORE,
                description="Minor inconsistency, can be safely ignored",
                confidence=0.9,
                side_effects=[]
            ))
            suggestions.append(ResolutionSuggestion(
                strategy=ResolutionStrategy.EXPLAIN,
                description="Provide a narrative explanation if needed",
                confidence=0.6,
                side_effects=["May require additional exposition"]
            ))

        elif contradiction.severity == ContradictionSeverity.MODERATE:
            suggestions.append(ResolutionSuggestion(
                strategy=ResolutionStrategy.EXPLAIN,
                description="Explain the discrepancy within the narrative",
                confidence=0.7,
                side_effects=["May add complexity to the story"]
            ))
            suggestions.append(ResolutionSuggestion(
                strategy=ResolutionStrategy.RETCON,
                description="Update the conflicting fact to match new information",
                confidence=0.6,
                side_effects=["May affect related facts", "Players might notice the change"]
            ))
            suggestions.append(ResolutionSuggestion(
                strategy=ResolutionStrategy.FLAG_FOR_DM,
                description="Let the DM decide how to handle this",
                confidence=0.5,
                side_effects=["Requires DM intervention"]
            ))

        elif contradiction.severity == ContradictionSeverity.MAJOR:
            suggestions.append(ResolutionSuggestion(
                strategy=ResolutionStrategy.FLAG_FOR_DM,
                description="Significant contradiction requiring DM attention",
                confidence=0.9,
                side_effects=["Requires DM intervention"]
            ))
            suggestions.append(ResolutionSuggestion(
                strategy=ResolutionStrategy.RETCON,
                description="Major retcon to fix the contradiction",
                confidence=0.4,
                side_effects=[
                    "May significantly affect story continuity",
                    "Players will likely notice",
                    "May require updating multiple related facts"
                ]
            ))

        else:  # CRITICAL
            suggestions.append(ResolutionSuggestion(
                strategy=ResolutionStrategy.FLAG_FOR_DM,
                description="Critical contradiction requiring immediate DM attention",
                confidence=1.0,
                side_effects=["Requires immediate DM intervention", "May halt session"]
            ))

        # Sort by confidence (highest first)
        suggestions.sort(key=lambda s: s.confidence, reverse=True)

        return suggestions

    def resolve(
        self,
        contradiction_id: str,
        strategy: ResolutionStrategy,
        notes: str | None = None
    ) -> bool:
        """
        Mark a contradiction as resolved.

        Pending detections (from non-registering checks) are looked up first;
        resolving one moves it to the registered list so a subsequent save()
        persists it with the chosen strategy.

        Args:
            contradiction_id: ID of the contradiction to resolve
            strategy: The resolution strategy being used
            notes: Optional notes about the resolution

        Returns:
            True if contradiction was found and resolved, False otherwise
        """
        pending = self._pending.pop(contradiction_id, None)
        if pending is not None:
            pending.resolved = True
            pending.resolution = strategy
            pending.resolution_notes = notes
            self._contradictions.append(pending)
            logger.info(
                f"Resolved pending contradiction {contradiction_id} using {strategy.value}"
            )
            return True

        for contradiction in self._contradictions:
            if contradiction.id == contradiction_id:
                contradiction.resolved = True
                contradiction.resolution = strategy
                contradiction.resolution_notes = notes

                logger.info(
                    f"Resolved contradiction {contradiction_id} using {strategy.value}"
                )
                return True

        logger.warning(f"Contradiction {contradiction_id} not found")
        return False

    def get_unresolved(
        self,
        severity: ContradictionSeverity | None = None
    ) -> list[Contradiction]:
        """
        Get all unresolved contradictions, optionally filtered by severity.

        Args:
            severity: Optional severity level to filter by

        Returns:
            List of unresolved contradictions
        """
        unresolved = [c for c in self._contradictions if not c.resolved]

        if severity is not None:
            unresolved = [c for c in unresolved if c.severity == severity]

        # Sort by severity (critical first) and detection time (newest first)
        severity_order = {
            ContradictionSeverity.CRITICAL: 0,
            ContradictionSeverity.MAJOR: 1,
            ContradictionSeverity.MODERATE: 2,
            ContradictionSeverity.MINOR: 3
        }
        unresolved.sort(key=lambda c: (severity_order[c.severity], -c.detected_at.timestamp()))

        return unresolved

    def get_all_contradictions(self) -> list[Contradiction]:
        """
        Get all contradictions (resolved and unresolved).

        Returns:
            List of all contradictions
        """
        return self._contradictions.copy()

    def save(self) -> None:
        """
        Persist contradictions to contradictions.json.

        Saves all contradictions with metadata about the last update time
        and summary statistics.
        """
        total_detected = len(self._contradictions)
        total_resolved = sum(1 for c in self._contradictions if c.resolved)

        data = {
            "version": "1.0",
            "contradictions": [
                c.model_dump(mode="json") for c in self._contradictions
            ],
            "metadata": {
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "total_detected": total_detected,
                "total_resolved": total_resolved
            }
        }

        with open(self._contradictions_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info(
            f"Saved {total_detected} contradictions "
            f"({total_resolved} resolved) to {self._contradictions_path}"
        )

    def load(self) -> None:
        """
        Load contradictions from contradictions.json.

        Handles missing files gracefully by initializing an empty list.
        If the file is corrupt or invalid, logs an error and starts with
        empty contradictions list.
        """
        if not self._contradictions_path.exists():
            logger.debug(
                f"No existing contradictions at {self._contradictions_path}, starting empty"
            )
            self._contradictions = []
            return

        try:
            with open(self._contradictions_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Validate structure
            if not isinstance(data, dict) or "contradictions" not in data:
                raise ValueError("Invalid contradictions database structure")

            # Load contradictions
            self._contradictions = [
                Contradiction(**c_data) for c_data in data["contradictions"]
            ]

            logger.info(
                f"Loaded {len(self._contradictions)} contradictions "
                f"from {self._contradictions_path}"
            )

        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.error(
                f"Failed to load contradictions from {self._contradictions_path}: {e}"
            )
            logger.warning("Starting with empty contradictions list")
            self._contradictions = []


__all__ = [
    "ContradictionDetector",
]
