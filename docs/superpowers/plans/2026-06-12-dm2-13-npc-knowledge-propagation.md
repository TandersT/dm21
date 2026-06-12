# DM2-13 NPC Knowledge Propagation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Activate the dormant NPC knowledge surface: reveal facts to NPCs with source+confidence, propagate knowledge NPC→NPC with confidence decay, and query who-knows-what in both directions.

**Architecture:** Third instance of the DM2-11/12 activation shape, minus the storage work (the `NPCKnowledgeTracker` already rides the fact-graph lifecycle). Two signature extensions on the tracker (`reveal_to_npc` confidence, `propagate_knowledge` decay + return value), a shared content-derived fact-id helper extracted from `record_party_fact`, three MCP tools in `main.py`, and persona Continuity Protocol bullets.

**Tech Stack:** Python 3.11+, FastMCP, pydantic, pytest. Spec: `docs/superpowers/specs/2026-06-12-dm2-13-npc-knowledge-propagation-design.md`.

All paths relative to the worktree root `/home/sta-aurocon/source/repos/dm20-protocol/worktrees/sta-dm2-13-npc-knowledge-propagation`. Run tests with `uv run pytest` from the worktree root.

---

### Task 1: Tracker primitives — reveal confidence + propagation decay

**Files:**
- Modify: `src/dm20_protocol/claudmaster/consistency/npc_knowledge.py` (reveal_to_npc ~line 133, propagate_knowledge ~line 161, `__all__` at bottom)
- Test: `tests/claudmaster/test_npc_knowledge.py` (append new class at end of file)

- [ ] **Step 1: Write the failing tests**

Append to `tests/claudmaster/test_npc_knowledge.py` (the file already imports `pytest`, `FactDatabase`, `KnowledgeSource`, `NPCKnowledgeTracker` at the top — no import changes needed):

```python
class TestConfidenceDecay:
    """Reveal confidence pass-through and propagation decay (DM2-13)."""

    def _tracker(self, tmp_path):
        campaign_path = tmp_path / "campaign"
        fact_db = FactDatabase(campaign_path)
        return NPCKnowledgeTracker(fact_db, campaign_path)

    def test_reveal_to_npc_confidence_passthrough(self, tmp_path):
        tracker = self._tracker(tmp_path)
        tracker.reveal_to_npc(
            "barkeep", "fact_001", revealed_by="Aldric", session=1, confidence=0.5
        )
        entry = tracker.get_npc_knowledge("barkeep")[0]
        assert entry.confidence == 0.5
        assert entry.source == KnowledgeSource.TOLD_BY_PLAYER
        assert entry.source_entity == "Aldric"

    def test_reveal_to_npc_defaults_to_certain(self, tmp_path):
        tracker = self._tracker(tmp_path)
        tracker.reveal_to_npc("barkeep", "fact_001", revealed_by="Aldric", session=1)
        assert tracker.get_npc_knowledge("barkeep")[0].confidence == 1.0

    def test_propagate_applies_decay_to_sender_confidence(self, tmp_path):
        tracker = self._tracker(tmp_path)
        tracker.add_knowledge(
            "aragorn", "fact_001", KnowledgeSource.WITNESSED, 1, confidence=1.0
        )
        tracker.propagate_knowledge("aragorn", "boromir", ["fact_001"], session=2)
        assert tracker.get_npc_knowledge("boromir")[0].confidence == pytest.approx(0.75)

    def test_propagate_decay_compounds_over_hops(self, tmp_path):
        tracker = self._tracker(tmp_path)
        tracker.add_knowledge("a", "fact_001", KnowledgeSource.WITNESSED, 1, confidence=1.0)
        tracker.propagate_knowledge("a", "b", ["fact_001"], session=2)
        tracker.propagate_knowledge("b", "c", ["fact_001"], session=3)
        assert tracker.get_npc_knowledge("c")[0].confidence == pytest.approx(0.5625)

    def test_propagate_custom_decay(self, tmp_path):
        tracker = self._tracker(tmp_path)
        tracker.add_knowledge("a", "fact_001", KnowledgeSource.WITNESSED, 1, confidence=0.8)
        tracker.propagate_knowledge("a", "b", ["fact_001"], session=2, decay=0.5)
        assert tracker.get_npc_knowledge("b")[0].confidence == pytest.approx(0.4)

    def test_propagate_returns_propagated_fact_ids(self, tmp_path):
        tracker = self._tracker(tmp_path)
        tracker.add_knowledge("a", "fact_001", KnowledgeSource.WITNESSED, 1)
        tracker.add_knowledge("a", "fact_002", KnowledgeSource.WITNESSED, 1)
        result = tracker.propagate_knowledge(
            "a", "b", ["fact_001", "fact_002"], session=2
        )
        assert result == ["fact_001", "fact_002"]

    def test_propagate_return_excludes_skips(self, tmp_path):
        tracker = self._tracker(tmp_path)
        tracker.add_knowledge("a", "fact_001", KnowledgeSource.WITNESSED, 1)
        tracker.add_knowledge("b", "fact_001", KnowledgeSource.WITNESSED, 1)
        # b already knows fact_001; a doesn't know fact_999
        result = tracker.propagate_knowledge(
            "a", "b", ["fact_001", "fact_999"], session=2
        )
        assert result == []
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `uv run pytest tests/claudmaster/test_npc_knowledge.py::TestConfidenceDecay -v`
Expected: FAIL — `reveal_to_npc() got an unexpected keyword argument 'confidence'`, decay assertions failing with 1.0, return-value assertions failing with None.

- [ ] **Step 3: Implement the tracker changes**

In `src/dm20_protocol/claudmaster/consistency/npc_knowledge.py`:

(a) After `logger = logging.getLogger("dm20-protocol")` add:

```python
# Confidence multiplier applied per propagation hop: one hop takes a certain
# fact (1.0) to secondhand (0.75), two hops to rumor-grade (~0.56) — matching
# the 1.0-certain / 0.5-rumor scale on KnowledgeEntry.
DEFAULT_PROPAGATION_DECAY = 0.75
```

(b) Replace `reveal_to_npc` entirely:

```python
    def reveal_to_npc(
        self,
        npc_id: str,
        fact_id: str,
        revealed_by: str,
        session: int,
        confidence: float = 1.0
    ) -> None:
        """
        Record that information was revealed to an NPC by a player.

        This is a convenience wrapper around add_knowledge with
        source=TOLD_BY_PLAYER.

        Args:
            npc_id: The NPC's identifier
            fact_id: ID of the fact being revealed
            revealed_by: Player character name who revealed the information
            session: Session number when revelation occurred
            confidence: Certainty level (1.0 certain, 0.5 rumor)
        """
        self.add_knowledge(
            npc_id=npc_id,
            fact_id=fact_id,
            source=KnowledgeSource.TOLD_BY_PLAYER,
            session=session,
            confidence=confidence,
            source_entity=revealed_by
        )
```

(c) Replace `propagate_knowledge` entirely:

```python
    def propagate_knowledge(
        self,
        from_npc: str,
        to_npc: str,
        fact_ids: list[str],
        session: int,
        decay: float = DEFAULT_PROPAGATION_DECAY
    ) -> list[str]:
        """
        Transfer knowledge from one NPC to another with confidence decay.

        Only facts that from_npc actually knows will be propagated.
        Facts already known by to_npc are skipped. The receiving NPC's
        confidence is the sender's confidence multiplied by decay — an NPC
        cannot transmit more certainty than they hold.

        Args:
            from_npc: NPC ID who is sharing the knowledge
            to_npc: NPC ID who is receiving the knowledge
            fact_ids: List of fact IDs to propagate
            session: Session number when propagation occurred
            decay: Confidence multiplier per hop (default 0.75)

        Returns:
            List of fact IDs actually propagated to to_npc.
        """
        sender_confidence = {
            entry.fact_id: entry.confidence
            for entry in self.get_npc_knowledge(from_npc)
        }

        propagated: list[str] = []
        for fact_id in fact_ids:
            if fact_id not in sender_confidence:
                logger.debug(
                    f"Skipping propagation of {fact_id} from {from_npc} to {to_npc}: "
                    f"{from_npc} doesn't know this fact"
                )
                continue
            if self.npc_knows_fact(to_npc, fact_id):
                logger.debug(
                    f"Skipping propagation of {fact_id} from {from_npc} to {to_npc}: "
                    f"{to_npc} already knows this fact"
                )
                continue

            self.add_knowledge(
                npc_id=to_npc,
                fact_id=fact_id,
                source=KnowledgeSource.TOLD_BY_NPC,
                session=session,
                confidence=sender_confidence[fact_id] * decay,
                source_entity=from_npc
            )
            propagated.append(fact_id)

        logger.debug(
            f"Propagated {len(propagated)} fact(s) from {from_npc} to {to_npc} "
            f"(session {session}, decay {decay})"
        )
        return propagated
```

(d) Update `__all__` at the bottom:

```python
__all__ = [
    "DEFAULT_PROPAGATION_DECAY",
    "NPCKnowledgeTracker",
]
```

- [ ] **Step 4: Run the full module suite**

Run: `uv run pytest tests/claudmaster/test_npc_knowledge.py -v`
Expected: ALL PASS (the pre-existing propagate tests don't pin the received confidence, so decay is non-breaking).

- [ ] **Step 5: Commit**

```bash
git add src/dm20_protocol/claudmaster/consistency/npc_knowledge.py tests/claudmaster/test_npc_knowledge.py
git commit -m "feat(DM2-13): confidence pass-through on reveal, decay + return value on propagate"
```

---

### Task 2: Shared content-derived fact-id helpers in main.py

**Files:**
- Modify: `src/dm20_protocol/main.py` (add helpers after `_resolve_npc` ~line 1402; replace inline formula in `record_party_fact` ~line 4972)
- Test: existing `tests/test_knowledge_write_tools.py` (pinned-formula test guards the extraction)

- [ ] **Step 1: Add the helpers**

In `src/dm20_protocol/main.py`, directly after the `_resolve_npc` function body (before `_ingest_to_fact_graph`), add:

```python
def _content_fact_id(content: str) -> str:
    """Deterministic content-derived fact id (pinned formula, DM2-7).

    Identical content (case/whitespace-insensitive) converges on the same
    fact node across record_party_fact and the NPC knowledge tools.
    """
    normalized = content.strip().lower()
    return f"pfact_{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:12]}"


def _resolve_fact_id(fact_ref: str) -> str | None:
    """Resolve a fact reference (existing fact id or fact content) to a fact id.

    Returns the id of an existing fact — the literal id if it exists, else
    the content-derived id if that exists — or None when neither does.
    """
    fact_db = storage.fact_db
    if fact_db is None:
        return None
    if fact_db.get_fact(fact_ref) is not None:
        return fact_ref
    content_id = _content_fact_id(fact_ref)
    if fact_db.get_fact(content_id) is not None:
        return content_id
    return None
```

- [ ] **Step 2: Use the helper in record_party_fact**

In `record_party_fact` (~line 4970), replace:

```python
    # Deterministic content-derived id: identical content converges on the
    # same fact, and learn_fact's fact_id dedupe makes repeats a no-op.
    fact_id = f"pfact_{hashlib.sha256(content.lower().encode('utf-8')).hexdigest()[:12]}"
```

with:

```python
    # Deterministic content-derived id: identical content converges on the
    # same fact, and learn_fact's fact_id dedupe makes repeats a no-op.
    fact_id = _content_fact_id(content)
```

(`content` is already stripped at this point; `_content_fact_id` strips again — idempotent, same ids.)

- [ ] **Step 3: Run the guard suite**

Run: `uv run pytest tests/test_knowledge_write_tools.py -v`
Expected: ALL PASS — in particular `test_fact_id_matches_pinned_formula`, `test_case_insensitive_content_converges`, `test_whitespace_normalized_content_converges`.

- [ ] **Step 4: Commit**

```bash
git add src/dm20_protocol/main.py
git commit -m "refactor(DM2-13): extract pinned pfact id formula into _content_fact_id + _resolve_fact_id"
```

---

### Task 3: reveal_fact_to_npc tool

**Files:**
- Modify: `src/dm20_protocol/main.py` (insert constant + tool after `record_npc_interaction`, before `@mcp.tool def sync_facts` ~line 5083)
- Test: `tests/test_npc_knowledge_wiring.py` (new)

- [ ] **Step 1: Create the wiring test file with the reveal tests**

Create `tests/test_npc_knowledge_wiring.py`:

```python
"""
Tests for the NPC knowledge tools (DM2-13).

reveal_fact_to_npc writes KnowledgeEntries with source/confidence (dual fact
resolution: existing id, else the pinned content-derived pfact_ id shared
with record_party_fact); propagate_npc_knowledge transfers entries with
confidence decay; npc_knowledge answers both query directions. Tools are
exercised via the underlying functions (`.fn`) with the module-level storage
swapped, following tests/test_knowledge_write_tools.py.
"""

import hashlib
from pathlib import Path

import pytest

from dm20_protocol.storage import DnDStorage


@pytest.fixture
def storage(tmp_path: Path) -> DnDStorage:
    s = DnDStorage(data_dir=tmp_path / "data")
    s.create_campaign(name="NPC Knowledge Test", description="d", dm_name="DM")
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


# ── reveal_fact_to_npc ──────────────────────────────────────────────


class TestRevealFactToNpc:
    def test_reveals_with_source_and_confidence(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        result = m.reveal_fact_to_npc.fn(
            npc="Barkeep",
            fact="The mill burned down",
            source="rumor",
            confidence=0.5,
        )
        assert "✅" in result

        npc = storage.get_npc("Barkeep")
        entries = storage.npc_knowledge_tracker.get_npc_knowledge(npc.id)
        assert len(entries) == 1
        assert entries[0].confidence == 0.5
        assert entries[0].source.value == "rumor"

    def test_mints_fact_with_pinned_content_id(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        content = "The mill burned down"
        m.reveal_fact_to_npc.fn(npc="Barkeep", fact=content)
        assert storage.fact_db.get_fact(_pfact_id(content)) is not None

    def test_converges_with_record_party_fact_node(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        content = "Strahd cannot enter consecrated ground"
        m.record_party_fact.fn(
            content=content, category="npc", source="Father Lucian", method="told_by_npc"
        )
        facts_before = len(storage.fact_db.facts)

        m.reveal_fact_to_npc.fn(npc="Barkeep", fact=content)
        assert len(storage.fact_db.facts) == facts_before  # no new node

        npc = storage.get_npc("Barkeep")
        entries = storage.npc_knowledge_tracker.get_npc_knowledge(npc.id)
        assert entries[0].fact_id == _pfact_id(content)

    def test_accepts_existing_fact_id(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        content = "The vistani know the way"
        m.record_party_fact.fn(
            content=content, category="world", source="Madam Eva", method="told_by_npc"
        )
        fact_id = _pfact_id(content)

        result = m.reveal_fact_to_npc.fn(npc="Barkeep", fact=fact_id)
        assert "✅" in result
        npc = storage.get_npc("Barkeep")
        assert storage.npc_knowledge_tracker.npc_knows_fact(npc.id, fact_id)

    def test_told_by_player_routes_through_reveal_to_npc(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        m.reveal_fact_to_npc.fn(
            npc="Barkeep",
            fact="We slew the dragon",
            source="told_by_player",
            source_entity="Aldric",
        )
        npc = storage.get_npc("Barkeep")
        entry = storage.npc_knowledge_tracker.get_npc_knowledge(npc.id)[0]
        assert entry.source.value == "told_by_player"
        assert entry.source_entity == "Aldric"

    def test_already_known_is_noop(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        m.reveal_fact_to_npc.fn(npc="Barkeep", fact="The mill burned down", confidence=1.0)
        result = m.reveal_fact_to_npc.fn(
            npc="Barkeep", fact="The mill burned down", confidence=0.5
        )
        assert "already knows" in result

        npc = storage.get_npc("Barkeep")
        entries = storage.npc_knowledge_tracker.get_npc_knowledge(npc.id)
        assert len(entries) == 1
        assert entries[0].confidence == 1.0  # original entry kept

    def test_unknown_npc_rejected(self, m, storage):
        result = m.reveal_fact_to_npc.fn(npc="Strahd", fact="x")
        assert "not found" in result
        assert "create_npc" in result

    def test_invalid_source_lists_valid_values(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        result = m.reveal_fact_to_npc.fn(npc="Barkeep", fact="x", source="gossip")
        assert "Invalid source 'gossip'" in result
        assert "rumor" in result

    def test_invalid_category_lists_valid_values(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        result = m.reveal_fact_to_npc.fn(npc="Barkeep", fact="x", category="rumor")
        assert "Invalid category 'rumor'" in result
        assert "world" in result

    def test_empty_fact_rejected(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        result = m.reveal_fact_to_npc.fn(npc="Barkeep", fact="   ")
        assert "empty" in result.lower()

    def test_session_defaults_to_current(self, m, storage):
        storage.update_game_state(current_session=3)
        m.create_npc.fn(name="Barkeep")
        m.reveal_fact_to_npc.fn(npc="Barkeep", fact="The mill burned down")
        npc = storage.get_npc("Barkeep")
        entry = storage.npc_knowledge_tracker.get_npc_knowledge(npc.id)[0]
        assert entry.acquired_session == 3

    def test_persists_to_disk(self, m, storage, tmp_path):
        m.create_npc.fn(name="Barkeep")
        m.reveal_fact_to_npc.fn(npc="Barkeep", fact="The mill burned down")
        npc = storage.get_npc("Barkeep")

        fresh = DnDStorage(data_dir=tmp_path / "data")
        fresh.load_campaign("NPC Knowledge Test")
        fact_id = _pfact_id("The mill burned down")
        assert fresh.npc_knowledge_tracker.npc_knows_fact(npc.id, fact_id)
        assert fresh.fact_db.get_fact(fact_id) is not None

    def test_unavailable_without_fact_graph(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        storage._fact_db = None
        result = m.reveal_fact_to_npc.fn(npc="Barkeep", fact="x")
        assert "could not be loaded" in result

    def test_requires_campaign(self, m, storage):
        storage._current_campaign = None
        result = m.reveal_fact_to_npc.fn(npc="Barkeep", fact="x")
        assert "No active campaign" in result
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_npc_knowledge_wiring.py -v`
Expected: FAIL — `AttributeError: module 'dm20_protocol.main' has no attribute 'reveal_fact_to_npc'`.

- [ ] **Step 3: Implement the tool**

In `src/dm20_protocol/main.py`, after the closing of `record_npc_interaction` (the `return f"✅ Recorded {interaction_type} interaction ..."` block, ~line 5080) and before `@mcp.tool def sync_facts`, insert:

```python
_NPC_KNOWLEDGE_UNAVAILABLE = (
    "NPC knowledge unavailable: the fact graph could not be loaded "
    "for this campaign (split-format campaigns only)."
)


@mcp.tool
def reveal_fact_to_npc(
    npc: Annotated[str, Field(description="NPC name or ID — the NPC must already exist (create it with create_npc first)")],
    fact: Annotated[str, Field(description="Fact id (from the fact graph) or free-text fact content; unrecognized content mints a new fact")],
    source: Annotated[str, Field(description="How the NPC acquired it: witnessed, told_by_player, told_by_npc, common_knowledge, profession, rumor")] = "told_by_player",
    source_entity: Annotated[str | None, Field(description="Who told them (PC name for told_by_player, NPC name for told_by_npc)")] = None,
    confidence: Annotated[float, Field(description="Certainty: 1.0 certain → 0.5 rumor", ge=0.0, le=1.0)] = 1.0,
    session: Annotated[int | None, Field(description="Session number (defaults to the current session)", ge=1)] = None,
    category: Annotated[str, Field(description="Fact category, used only when minting a new fact: event, location, npc, item, quest, world")] = "world",
) -> str:
    """Reveal a fact to an NPC with a source and confidence.

    Writes a knowledge entry on the NPC so future dialogue can reference it
    (queryable via npc_knowledge). The fact is matched by id first, then by
    content — the same content-derived id as record_party_fact, so party
    facts and NPC rumors converge on one node; unrecognized content mints a
    new fact. If the NPC already knows the fact, nothing changes: the
    original confidence and source are kept.
    """
    campaign = storage.get_current_campaign()
    if not campaign:
        return "No active campaign. Load or create a campaign first."

    fact_db = storage.fact_db
    tracker = storage.npc_knowledge_tracker
    if fact_db is None or tracker is None:
        return _NPC_KNOWLEDGE_UNAVAILABLE

    fact = fact.strip()
    if not fact:
        return "Fact cannot be empty."

    npc_obj = _resolve_npc(npc)
    if npc_obj is None:
        return f"NPC '{npc}' not found. Create the NPC first with create_npc."

    from .claudmaster.consistency.models import Fact, FactCategory, KnowledgeSource

    try:
        source_enum = KnowledgeSource(source)
    except ValueError:
        valid = ", ".join(s.value for s in KnowledgeSource)
        return f"Invalid source '{source}'. Valid sources: {valid}"

    session_number = session or _current_session_number()

    minted = False
    fact_id = _resolve_fact_id(fact)
    if fact_id is None:
        try:
            category_enum = FactCategory(category)
        except ValueError:
            valid = ", ".join(c.value for c in FactCategory)
            return f"Invalid category '{category}'. Valid categories: {valid}"
        fact_id = _content_fact_id(fact)
        fact_db.add_fact(
            Fact(
                id=fact_id,
                category=category_enum,
                content=fact,
                session_number=session_number,
                source=source_entity or source_enum.value,
            )
        )
        minted = True

    if tracker.npc_knows_fact(npc_obj.id, fact_id):
        return (
            f"'{npc_obj.name}' already knows fact {fact_id} — no changes made "
            "(the original confidence and source are kept)."
        )

    if source_enum == KnowledgeSource.TOLD_BY_PLAYER:
        tracker.reveal_to_npc(
            npc_obj.id,
            fact_id,
            revealed_by=source_entity or "party",
            session=session_number,
            confidence=confidence,
        )
    else:
        tracker.add_knowledge(
            npc_obj.id,
            fact_id,
            source=source_enum,
            session=session_number,
            confidence=confidence,
            source_entity=source_entity,
        )

    if minted:
        fact_db.save()
    tracker.save()

    fact_content = fact_db.get_fact(fact_id).content
    minted_note = " (new fact minted)" if minted else ""
    return (
        f"✅ Revealed fact {fact_id} to '{npc_obj.name}' via {source_enum.value} "
        f"(confidence {confidence:.2f}, session {session_number}){minted_note}: {fact_content}"
    )
```

- [ ] **Step 4: Run the reveal tests**

Run: `uv run pytest tests/test_npc_knowledge_wiring.py::TestRevealFactToNpc -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dm20_protocol/main.py tests/test_npc_knowledge_wiring.py
git commit -m "feat(DM2-13): reveal_fact_to_npc tool — facts revealable to NPCs with source and confidence"
```

---

### Task 4: propagate_npc_knowledge tool

**Files:**
- Modify: `src/dm20_protocol/main.py` (insert after `reveal_fact_to_npc`)
- Test: `tests/test_npc_knowledge_wiring.py` (append)

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_npc_knowledge_wiring.py`:

```python
# ── propagate_npc_knowledge ─────────────────────────────────────────


class TestPropagateNpcKnowledge:
    def _setup_two_npcs(self, m, storage):
        m.create_npc.fn(name="Innkeeper")
        m.create_npc.fn(name="Captain")
        m.reveal_fact_to_npc.fn(
            npc="Innkeeper", fact="The mill burned down", source="witnessed"
        )
        return storage.get_npc("Innkeeper"), storage.get_npc("Captain")

    def test_propagates_with_default_decay(self, m, storage):
        innkeeper, captain = self._setup_two_npcs(m, storage)
        result = m.propagate_npc_knowledge.fn(from_npc="Innkeeper", to_npc="Captain")
        assert "✅" in result

        entries = storage.npc_knowledge_tracker.get_npc_knowledge(captain.id)
        assert len(entries) == 1
        assert entries[0].confidence == pytest.approx(0.75)
        assert entries[0].source.value == "told_by_npc"
        assert entries[0].source_entity == innkeeper.id

    def test_custom_decay(self, m, storage):
        _, captain = self._setup_two_npcs(m, storage)
        m.propagate_npc_knowledge.fn(from_npc="Innkeeper", to_npc="Captain", decay=0.5)
        entries = storage.npc_knowledge_tracker.get_npc_knowledge(captain.id)
        assert entries[0].confidence == pytest.approx(0.5)

    def test_two_hops_compound_decay(self, m, storage):
        self._setup_two_npcs(m, storage)
        m.create_npc.fn(name="Guard")
        m.propagate_npc_knowledge.fn(from_npc="Innkeeper", to_npc="Captain")
        m.propagate_npc_knowledge.fn(from_npc="Captain", to_npc="Guard")
        guard = storage.get_npc("Guard")
        entries = storage.npc_knowledge_tracker.get_npc_knowledge(guard.id)
        assert entries[0].confidence == pytest.approx(0.5625)

    def test_explicit_fact_by_content(self, m, storage):
        self._setup_two_npcs(m, storage)
        m.reveal_fact_to_npc.fn(
            npc="Innkeeper", fact="The baron is broke", source="profession"
        )
        m.propagate_npc_knowledge.fn(
            from_npc="Innkeeper", to_npc="Captain", facts='["The baron is broke"]'
        )
        captain = storage.get_npc("Captain")
        entries = storage.npc_knowledge_tracker.get_npc_knowledge(captain.id)
        assert len(entries) == 1
        assert entries[0].fact_id == _pfact_id("The baron is broke")

    def test_receiver_already_knows_reported(self, m, storage):
        self._setup_two_npcs(m, storage)
        m.reveal_fact_to_npc.fn(
            npc="Captain", fact="The mill burned down", source="witnessed"
        )
        result = m.propagate_npc_knowledge.fn(from_npc="Innkeeper", to_npc="Captain")
        assert "already knows" in result

        captain = storage.get_npc("Captain")
        entries = storage.npc_knowledge_tracker.get_npc_knowledge(captain.id)
        assert len(entries) == 1
        assert entries[0].confidence == 1.0  # witnessed entry untouched

    def test_unresolved_fact_reported(self, m, storage):
        self._setup_two_npcs(m, storage)
        result = m.propagate_npc_knowledge.fn(
            from_npc="Innkeeper", to_npc="Captain", facts='["No such fact"]'
        )
        assert "No facts resolved" in result

    def test_sender_without_knowledge(self, m, storage):
        m.create_npc.fn(name="Innkeeper")
        m.create_npc.fn(name="Captain")
        result = m.propagate_npc_knowledge.fn(from_npc="Innkeeper", to_npc="Captain")
        assert "no recorded knowledge" in result

    def test_self_propagation_rejected(self, m, storage):
        m.create_npc.fn(name="Innkeeper")
        result = m.propagate_npc_knowledge.fn(from_npc="Innkeeper", to_npc="Innkeeper")
        assert "themselves" in result

    def test_unknown_npcs_rejected(self, m, storage):
        m.create_npc.fn(name="Innkeeper")
        assert "not found" in m.propagate_npc_knowledge.fn(
            from_npc="Ghost", to_npc="Innkeeper"
        )
        assert "not found" in m.propagate_npc_knowledge.fn(
            from_npc="Innkeeper", to_npc="Ghost"
        )

    def test_persists_to_disk(self, m, storage, tmp_path):
        _, captain = self._setup_two_npcs(m, storage)
        m.propagate_npc_knowledge.fn(from_npc="Innkeeper", to_npc="Captain")

        fresh = DnDStorage(data_dir=tmp_path / "data")
        fresh.load_campaign("NPC Knowledge Test")
        assert len(fresh.npc_knowledge_tracker.get_npc_knowledge(captain.id)) == 1

    def test_unavailable_without_fact_graph(self, m, storage):
        m.create_npc.fn(name="Innkeeper")
        m.create_npc.fn(name="Captain")
        storage._fact_db = None
        result = m.propagate_npc_knowledge.fn(from_npc="Innkeeper", to_npc="Captain")
        assert "could not be loaded" in result

    def test_requires_campaign(self, m, storage):
        storage._current_campaign = None
        result = m.propagate_npc_knowledge.fn(from_npc="A", to_npc="B")
        assert "No active campaign" in result
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_npc_knowledge_wiring.py::TestPropagateNpcKnowledge -v`
Expected: FAIL — `AttributeError: ... no attribute 'propagate_npc_knowledge'`.

- [ ] **Step 3: Implement the tool**

In `src/dm20_protocol/main.py`, directly after `reveal_fact_to_npc`, insert:

```python
@mcp.tool
def propagate_npc_knowledge(
    from_npc: Annotated[str, Field(description="NPC name or ID sharing the knowledge")],
    to_npc: Annotated[str, Field(description="NPC name or ID receiving the knowledge")],
    facts: Annotated[str | None, Field(description="Fact ids or fact content to pass on — JSON array or comma-separated. Omit to share everything the source NPC knows")] = None,
    decay: Annotated[float | None, Field(description="Confidence multiplier per hop, 0–1 (defaults to 0.75: certain → secondhand → rumor)", gt=0.0, le=1.0)] = None,
    session: Annotated[int | None, Field(description="Session number (defaults to the current session)", ge=1)] = None,
) -> str:
    """Propagate knowledge from one NPC to another with confidence decay.

    Models NPCs talking offscreen: the receiver learns the facts at the
    sender's confidence multiplied by the decay factor, so retellings arrive
    as rumors. Only facts the sender actually knows propagate; facts the
    receiver already knows are left untouched.
    """
    campaign = storage.get_current_campaign()
    if not campaign:
        return "No active campaign. Load or create a campaign first."

    fact_db = storage.fact_db
    tracker = storage.npc_knowledge_tracker
    if fact_db is None or tracker is None:
        return _NPC_KNOWLEDGE_UNAVAILABLE

    from_obj = _resolve_npc(from_npc)
    if from_obj is None:
        return f"NPC '{from_npc}' not found. Create the NPC first with create_npc."
    to_obj = _resolve_npc(to_npc)
    if to_obj is None:
        return f"NPC '{to_npc}' not found. Create the NPC first with create_npc."
    if from_obj.id == to_obj.id:
        return f"'{from_obj.name}' cannot propagate knowledge to themselves."

    from .claudmaster.consistency.npc_knowledge import DEFAULT_PROPAGATION_DECAY

    sender_known = {e.fact_id for e in tracker.get_npc_knowledge(from_obj.id)}
    if not sender_known:
        return f"'{from_obj.name}' has no recorded knowledge to share."

    unresolved: list[str] = []
    if facts is None:
        fact_ids = sorted(sender_known)
    else:
        fact_ids = []
        for ref in _parse_json_list(facts):
            if ref in sender_known:
                fact_ids.append(ref)
                continue
            resolved = _resolve_fact_id(ref)
            if resolved is not None:
                fact_ids.append(resolved)
            else:
                unresolved.append(ref)
        fact_ids = list(dict.fromkeys(fact_ids))
        if not fact_ids:
            return (
                "No facts resolved: "
                + ", ".join(f"'{r}'" for r in unresolved)
                + ". Pass fact ids or the exact fact content."
            )

    session_number = session or _current_session_number()
    effective_decay = decay if decay is not None else DEFAULT_PROPAGATION_DECAY
    receiver_known_before = {e.fact_id for e in tracker.get_npc_knowledge(to_obj.id)}

    propagated = tracker.propagate_knowledge(
        from_obj.id, to_obj.id, fact_ids, session_number, decay=effective_decay
    )
    if propagated:
        tracker.save()

    def _fact_line(fact_id: str) -> str:
        fact = fact_db.get_fact(fact_id)
        return fact.content if fact is not None else fact_id

    receiver_entries = {e.fact_id: e for e in tracker.get_npc_knowledge(to_obj.id)}
    lines = []
    if propagated:
        lines.append(
            f"✅ '{from_obj.name}' passed {len(propagated)} fact(s) to '{to_obj.name}' "
            f"(decay {effective_decay:g}, session {session_number}):"
        )
        for fact_id in propagated:
            entry = receiver_entries[fact_id]
            lines.append(
                f"- {fact_id} (confidence {entry.confidence:.2f}): {_fact_line(fact_id)}"
            )
    else:
        lines.append(
            f"No knowledge propagated from '{from_obj.name}' to '{to_obj.name}'."
        )

    already_known = [f for f in fact_ids if f in receiver_known_before]
    not_known = [f for f in fact_ids if f not in sender_known]
    if already_known:
        lines.append(
            f"Skipped — '{to_obj.name}' already knows: " + ", ".join(already_known)
        )
    if not_known:
        lines.append(
            f"Skipped — '{from_obj.name}' doesn't know: " + ", ".join(not_known)
        )
    if unresolved:
        lines.append(
            "Skipped — not found in the fact graph: "
            + ", ".join(f"'{r}'" for r in unresolved)
        )
    return "\n".join(lines)
```

- [ ] **Step 4: Run the propagate tests**

Run: `uv run pytest tests/test_npc_knowledge_wiring.py::TestPropagateNpcKnowledge -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dm20_protocol/main.py tests/test_npc_knowledge_wiring.py
git commit -m "feat(DM2-13): propagate_npc_knowledge tool — NPC-to-NPC transfer with confidence decay"
```

---

### Task 5: npc_knowledge query tool

**Files:**
- Modify: `src/dm20_protocol/main.py` (insert after `propagate_npc_knowledge`)
- Test: `tests/test_npc_knowledge_wiring.py` (append)

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_npc_knowledge_wiring.py`:

```python
# ── npc_knowledge (query) ───────────────────────────────────────────


class TestNpcKnowledgeQuery:
    def test_what_npc_knows(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        m.reveal_fact_to_npc.fn(
            npc="Barkeep", fact="The mill burned down", source="witnessed"
        )
        m.reveal_fact_to_npc.fn(
            npc="Barkeep", fact="The baron is broke", source="rumor", confidence=0.5
        )

        result = m.npc_knowledge.fn(npc="Barkeep")
        assert "What 'Barkeep' knows" in result
        assert "The mill burned down" in result
        assert "The baron is broke" in result
        assert "0.50" in result
        assert "rumor" in result

    def test_who_knows_fact_by_content(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        m.create_npc.fn(name="Captain")
        m.reveal_fact_to_npc.fn(
            npc="Barkeep", fact="The mill burned down", source="witnessed"
        )
        m.propagate_npc_knowledge.fn(from_npc="Barkeep", to_npc="Captain")

        result = m.npc_knowledge.fn(fact="The mill burned down")
        assert "Barkeep" in result
        assert "Captain" in result
        assert "0.75" in result

    def test_who_knows_fact_by_id(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        m.reveal_fact_to_npc.fn(npc="Barkeep", fact="The mill burned down")
        result = m.npc_knowledge.fn(fact=_pfact_id("The mill burned down"))
        assert "Barkeep" in result

    def test_npc_with_no_knowledge(self, m, storage):
        m.create_npc.fn(name="Barkeep")
        result = m.npc_knowledge.fn(npc="Barkeep")
        assert "no recorded knowledge" in result

    def test_fact_nobody_knows(self, m, storage):
        m.record_party_fact.fn(
            content="The sun rises", category="world", source="s", method="observed"
        )
        result = m.npc_knowledge.fn(fact="The sun rises")
        assert "No NPCs know" in result

    def test_unknown_fact(self, m, storage):
        result = m.npc_knowledge.fn(fact="Never recorded")
        assert "not found" in result

    def test_requires_exactly_one_argument(self, m, storage):
        assert "exactly one" in m.npc_knowledge.fn()
        assert "exactly one" in m.npc_knowledge.fn(npc="A", fact="B")

    def test_unknown_npc_rejected(self, m, storage):
        result = m.npc_knowledge.fn(npc="Ghost")
        assert "not found" in result

    def test_unavailable_without_fact_graph(self, m, storage):
        storage._fact_db = None
        result = m.npc_knowledge.fn(npc="Barkeep")
        assert "could not be loaded" in result

    def test_requires_campaign(self, m, storage):
        storage._current_campaign = None
        result = m.npc_knowledge.fn(npc="Barkeep")
        assert "No active campaign" in result
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_npc_knowledge_wiring.py::TestNpcKnowledgeQuery -v`
Expected: FAIL — `AttributeError: ... no attribute 'npc_knowledge'`.

- [ ] **Step 3: Implement the tool**

In `src/dm20_protocol/main.py`, directly after `propagate_npc_knowledge`, insert:

```python
@mcp.tool
def npc_knowledge(
    npc: Annotated[str | None, Field(description="NPC name or ID — list everything this NPC knows")] = None,
    fact: Annotated[str | None, Field(description="Fact id or fact content — list which NPCs know it")] = None,
) -> str:
    """Query NPC knowledge: what an NPC knows, or which NPCs know a fact.

    Read-only. Pass exactly one argument: npc for the NPC's full knowledge
    (facts with confidence, source, and session, plus interaction count),
    or fact for every NPC that holds it and how certain they are.
    """
    campaign = storage.get_current_campaign()
    if not campaign:
        return "No active campaign. Load or create a campaign first."

    fact_db = storage.fact_db
    tracker = storage.npc_knowledge_tracker
    if fact_db is None or tracker is None:
        return _NPC_KNOWLEDGE_UNAVAILABLE

    if (npc is None) == (fact is None):
        return "Provide exactly one of: npc (what they know) or fact (who knows it)."

    names_by_id = {n.id: n.name for n in storage.list_npcs_detailed()}

    if npc is not None:
        npc_obj = _resolve_npc(npc)
        if npc_obj is None:
            return f"NPC '{npc}' not found. Create the NPC first with create_npc."

        context = tracker.get_knowledge_context(npc_obj.id)
        entries = context["knowledge_entries"]
        if not entries:
            return f"'{npc_obj.name}' has no recorded knowledge."

        facts_by_id = {f.id: f for f in context["known_facts"]}
        lines = [
            f"## What '{npc_obj.name}' knows "
            f"({context['fact_count']} fact(s), {context['interaction_count']} interaction(s))\n"
        ]
        for entry in entries:
            known = facts_by_id.get(entry.fact_id)
            content = (
                known.content
                if known is not None
                else f"(fact {entry.fact_id} missing from the fact graph)"
            )
            source_text = entry.source.value
            if entry.source_entity:
                source_name = names_by_id.get(entry.source_entity, entry.source_entity)
                source_text += f" (from {source_name})"
            lines.append(f"### {content}")
            lines.append(f"- **Fact id:** {entry.fact_id}")
            lines.append(f"- **Source:** {source_text}")
            lines.append(f"- **Confidence:** {entry.confidence:.2f}")
            lines.append(f"- **Session:** {entry.acquired_session}")
            lines.append("")
        return "\n".join(lines)

    fact = fact.strip()
    if not fact:
        return "Fact cannot be empty."
    fact_id = _resolve_fact_id(fact)
    if fact_id is None:
        return f"Fact '{fact}' not found in the fact graph."
    fact_obj = fact_db.get_fact(fact_id)

    knower_ids = tracker.query_npcs_who_know(fact_id)
    if not knower_ids:
        return f"No NPCs know fact {fact_id}: {fact_obj.content}"

    lines = [
        f"## NPCs who know: {fact_obj.content}",
        f"({len(knower_ids)} NPC(s), fact {fact_id})\n",
    ]
    for npc_id in knower_ids:
        entry = next(
            (e for e in tracker.get_npc_knowledge(npc_id) if e.fact_id == fact_id),
            None,
        )
        name = names_by_id.get(npc_id, npc_id)
        if entry is None:
            lines.append(f"- **{name}**")
            continue
        source_text = entry.source.value
        if entry.source_entity:
            source_name = names_by_id.get(entry.source_entity, entry.source_entity)
            source_text += f" (from {source_name})"
        lines.append(
            f"- **{name}** — confidence {entry.confidence:.2f}, {source_text}, "
            f"session {entry.acquired_session}"
        )
    return "\n".join(lines)
```

- [ ] **Step 4: Run the full wiring suite**

Run: `uv run pytest tests/test_npc_knowledge_wiring.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dm20_protocol/main.py tests/test_npc_knowledge_wiring.py
git commit -m "feat(DM2-13): npc_knowledge query tool — what an NPC knows / which NPCs know a fact"
```

---

### Task 6: Persona activation

**Files:**
- Modify: `.claude/dm-persona.md` (PERSIST list ~line 48, Continuity Protocol ~line 76)

- [ ] **Step 1: Add the PERSIST line**

In `.claude/dm-persona.md`, after the line:

```
- `record_npc_interaction` -- when an exchange changes the party's relationship with an NPC (see Continuity Protocol)
```

insert:

```
- `reveal_fact_to_npc` / `propagate_npc_knowledge` -- when an NPC learns something or word spreads between NPCs (see Continuity Protocol)
```

- [ ] **Step 2: Add the Continuity Protocol bullets**

In the Continuity Protocol section, after the bullet that begins `- **An interaction changes the party's relationship with an NPC**`, insert:

```
- **An NPC learns something they could act on or repeat** -- the party shares a secret, an NPC witnesses an event, a rumor reaches them: `reveal_fact_to_npc` with the source and a confidence (1.0 certain, 0.5 rumor).
- **NPCs talk offscreen and word spreads** -- `propagate_npc_knowledge` from one NPC to another; confidence decays with each hop, so retellings arrive as rumors. Before roleplaying an NPC referencing established events, check `npc_knowledge(npc=...)` -- NPCs only reference knowledge they hold.
```

- [ ] **Step 3: Commit**

```bash
git add .claude/dm-persona.md
git commit -m "docs(DM2-13): persona activation for NPC knowledge tools"
```

---

### Task 7: Focused verification run

- [ ] **Step 1: Run the focused suites for this diff**

Run:
```bash
uv run pytest tests/claudmaster/test_npc_knowledge.py tests/test_npc_knowledge_wiring.py tests/test_knowledge_write_tools.py tests/test_fact_dual_write.py tests/test_fact_graph_lifecycle.py -v
```
Expected: ALL PASS.

- [ ] **Step 2: Sanity-check server import**

Run: `uv run python -c "import dm20_protocol.main"`
Expected: clean exit, no output.
