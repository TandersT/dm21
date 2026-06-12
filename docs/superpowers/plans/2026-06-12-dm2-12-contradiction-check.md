# DM2-12 Contradiction Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the dormant `ContradictionDetector` as DM-facing MCP tooling: a read-only pre-narration `check_consistency` tool and a persisting `resolve_contradiction` tool.

**Architecture:** The detector joins the fact-graph lifecycle on `DnDStorage` (sharing the live `FactDatabase` instance). A new non-registering check mode parks detections in an in-memory pending dict; resolution moves a pending detection to the registered list with the chosen strategy and saves — disk gets exactly the contradictions the DM acted on. A persona bullet activates the check in the action loop.

**Tech Stack:** Python 3.11+, FastMCP tools in `src/dm20_protocol/main.py`, pydantic models, pytest.

**Spec:** `docs/superpowers/specs/2026-06-12-dm2-12-contradiction-check-design.md`

---

### Task 1: Pending mode in ContradictionDetector

**Files:**
- Modify: `src/dm20_protocol/claudmaster/consistency/contradiction.py`
- Test: `tests/claudmaster/test_contradiction.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/claudmaster/test_contradiction.py`:

```python
class TestPendingChecks:
    """Non-registering check mode and pending resolution (DM2-12)."""

    def _seed_conflicting_fact(self, fact_db):
        fact_db.add_fact(Fact(
            id="fact_donavich",
            category=FactCategory.NPC,
            content="Father Donavich is alive and hiding in the church",
            session_number=1,
        ))

    def test_register_false_keeps_registered_list_empty(self, detector, fact_db):
        self._seed_conflicting_fact(fact_db)
        detected = detector.check_statement(
            "Father Donavich is dead in the church", 2, register=False
        )
        assert len(detected) == 1
        assert detector.get_all_contradictions() == []

    def test_register_false_parks_detections_in_pending(self, detector, fact_db):
        self._seed_conflicting_fact(fact_db)
        detected = detector.check_statement(
            "Father Donavich is dead in the church", 2, register=False
        )
        assert detector._pending[detected[0].id] is detected[0]

    def test_register_default_still_registers(self, detector, fact_db):
        self._seed_conflicting_fact(fact_db)
        detector.check_statement("Father Donavich is dead in the church", 2)
        assert len(detector.get_all_contradictions()) == 1
        assert detector._pending == {}

    def test_resolve_pending_moves_to_registered_with_strategy_and_notes(self, detector, fact_db):
        self._seed_conflicting_fact(fact_db)
        detected = detector.check_statement(
            "Father Donavich is dead in the church", 2, register=False
        )
        cid = detected[0].id
        assert detector.resolve(cid, ResolutionStrategy.RETCON, "He died offscreen") is True
        assert cid not in detector._pending
        registered = detector.get_all_contradictions()
        assert len(registered) == 1
        assert registered[0].resolved is True
        assert registered[0].resolution == ResolutionStrategy.RETCON
        assert registered[0].resolution_notes == "He died offscreen"

    def test_save_excludes_pending(self, detector, fact_db, temp_campaign_path):
        self._seed_conflicting_fact(fact_db)
        detector.check_statement(
            "Father Donavich is dead in the church", 2, register=False
        )
        detector.save()
        reloaded = ContradictionDetector(fact_db, campaign_path=temp_campaign_path)
        assert reloaded.get_all_contradictions() == []

    def test_resolved_pending_survives_save_load_roundtrip(self, detector, fact_db, temp_campaign_path):
        self._seed_conflicting_fact(fact_db)
        detected = detector.check_statement(
            "Father Donavich is dead in the church", 2, register=False
        )
        detector.resolve(detected[0].id, ResolutionStrategy.FLAG_FOR_DM)
        detector.save()
        reloaded = ContradictionDetector(fact_db, campaign_path=temp_campaign_path)
        contradictions = reloaded.get_all_contradictions()
        assert len(contradictions) == 1
        assert contradictions[0].id == detected[0].id
        assert contradictions[0].resolution == ResolutionStrategy.FLAG_FOR_DM
```

Detection math for the seed pair: common keywords {father, donavich, church} (>= 2) and the
negation pair alive/dead -> detected, severity MAJOR (negation, no numeric).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/claudmaster/test_contradiction.py::TestPendingChecks -v`
Expected: FAIL — `check_statement() got an unexpected keyword argument 'register'`

- [ ] **Step 3: Implement pending mode**

In `src/dm20_protocol/claudmaster/consistency/contradiction.py`:

a) In `__init__`, after `self._contradictions: list[Contradiction] = []`:

```python
        # Detections from non-registering checks, keyed by contradiction id.
        # In-memory only: never serialized, dies with the instance.
        self._pending: dict[str, Contradiction] = {}
```

b) `check_statement` — add the keyword param and route the append:

```python
    def check_statement(
        self,
        statement: str,
        session_number: int,
        category: FactCategory | None = None,
        related_tags: list[str] | None = None,
        register: bool = True,
    ) -> list[Contradiction]:
```

Add to the docstring args: `register: When False, detections are parked in the in-memory
pending buffer (resolvable via resolve()) instead of the registered list.`

Replace `self._contradictions.append(contradiction)` (inside the detection loop) with:

```python
                if register:
                    self._contradictions.append(contradiction)
                else:
                    self._pending[contradiction.id] = contradiction
```

c) `resolve` — pending-first lookup. Insert at the top of the method body (before the
existing loop over `self._contradictions`):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/claudmaster/test_contradiction.py -v`
Expected: all PASS (existing classes + TestPendingChecks)

- [ ] **Step 5: Commit**

```bash
git add src/dm20_protocol/claudmaster/consistency/contradiction.py tests/claudmaster/test_contradiction.py
git commit -m "feat(DM2-12): add non-registering check mode + pending resolution to ContradictionDetector"
```

---

### Task 2: Detector joins the fact-graph lifecycle on DnDStorage

**Files:**
- Modify: `src/dm20_protocol/storage.py` (`__init__` ~line 100, delete-clear block ~line 650, `_load_fact_graph` ~line 1445, new property after `party_knowledge`)
- Test: `tests/test_contradiction_check_wiring.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_contradiction_check_wiring.py`:

```python
"""
Tests for the DM-facing contradiction check wiring (DM2-12).

check_consistency / resolve_contradiction tools, the storage-held detector
lifecycle, and the no-side-effects guarantee of the check path. Tools are
exercised via the underlying functions (`.fn`) with the module-level storage
swapped, following tests/test_timeline_wiring.py.
"""

from pathlib import Path

import pytest

from dm20_protocol.claudmaster.consistency.contradiction import ContradictionDetector
from dm20_protocol.claudmaster.consistency.models import (
    Fact,
    FactCategory,
    ResolutionStrategy,
)
from dm20_protocol.storage import DnDStorage

STATEMENT = "Father Donavich is dead in the church"


@pytest.fixture
def storage(tmp_path: Path) -> DnDStorage:
    s = DnDStorage(data_dir=tmp_path / "data")
    s.create_campaign(name="Contradiction Test", description="d", dm_name="DM")
    return s


@pytest.fixture
def m(storage: DnDStorage):
    """dm20_protocol.main with its module-level storage swapped for the test."""
    from dm20_protocol import main as m

    original = m.storage
    m.storage = storage
    yield m
    m.storage = original


def _seed_fact(storage: DnDStorage) -> None:
    storage.fact_db.add_fact(Fact(
        id="fact_donavich",
        category=FactCategory.NPC,
        content="Father Donavich is alive and hiding in the church",
        session_number=1,
    ))
    storage.fact_db.save()


# ── Detector lifecycle ──────────────────────────────────────────────


class TestDetectorLifecycle:
    def test_detector_loaded_for_split_campaign(self, storage):
        assert storage.contradiction_detector is not None

    def test_detector_shares_live_fact_db_instance(self, storage):
        assert storage.contradiction_detector._fact_db is storage.fact_db
```

(The tool test classes are added in Tasks 3 and 4.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_contradiction_check_wiring.py -v`
Expected: FAIL — `AttributeError: 'DnDStorage' object has no attribute 'contradiction_detector'`

- [ ] **Step 3: Implement the lifecycle wiring**

In `src/dm20_protocol/storage.py`:

a) `__init__` (after `self._timeline_tracker = None`, ~line 103):

```python
        self._contradiction_detector = None
```

b) Delete-campaign clear block (after `self._timeline_tracker = None`, ~line 653):

```python
            self._contradiction_detector = None
```

c) `_load_fact_graph` — add to the reset block at the top, the imports, the construction
(sharing the live `fact_db` instance), and the failure path:

```python
        self._fact_db = None
        self._npc_knowledge_tracker = None
        self._party_knowledge = None
        self._contradiction_detector = None

        if self._current_format != StorageFormat.SPLIT or not self._current_campaign:
            return

        campaign_dir = self._split_backend._get_campaign_dir(self._current_campaign.name)
        try:
            from .claudmaster.consistency.contradiction import ContradictionDetector
            from .claudmaster.consistency.fact_database import FactDatabase
            from .claudmaster.consistency.npc_knowledge import NPCKnowledgeTracker
            from .consistency.party_knowledge import PartyKnowledge

            fact_db = FactDatabase(campaign_dir)
            self._npc_knowledge_tracker = NPCKnowledgeTracker(fact_db, campaign_dir)
            self._party_knowledge = PartyKnowledge(fact_db, campaign_dir)
            self._contradiction_detector = ContradictionDetector(
                fact_db, self._npc_knowledge_tracker, campaign_dir
            )
            self._fact_db = fact_db
            logger.info(
                f"Loaded fact graph for campaign '{self._current_campaign.name}' "
                f"({len(fact_db.facts)} facts)"
            )
        except Exception as e:
            logger.warning(f"Failed to load fact graph: {e}")
            self._fact_db = None
            self._npc_knowledge_tracker = None
            self._party_knowledge = None
            self._contradiction_detector = None
```

Also update the `_load_fact_graph` docstring sentence "Builds the FactDatabase plus the
NPCKnowledgeTracker and PartyKnowledge views over it" to "Builds the FactDatabase plus the
NPCKnowledgeTracker, PartyKnowledge, and ContradictionDetector views over it" and
"On failure all three accessors degrade to None" to "On failure all four accessors degrade
to None".

d) New property after `party_knowledge` (~line 1443):

```python
    @property
    def contradiction_detector(self):
        """Get the ContradictionDetector for the current campaign (or None)."""
        return self._contradiction_detector
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_contradiction_check_wiring.py tests/test_fact_graph_lifecycle.py -v`
Expected: all PASS (lifecycle regressions included)

- [ ] **Step 5: Commit**

```bash
git add src/dm20_protocol/storage.py tests/test_contradiction_check_wiring.py
git commit -m "feat(DM2-12): hold ContradictionDetector on storage with the fact-graph lifecycle"
```

---

### Task 3: check_consistency MCP tool

**Files:**
- Modify: `src/dm20_protocol/main.py` (insert after `get_session_recap`, before the section divider at ~line 5272)
- Test: `tests/test_contradiction_check_wiring.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_contradiction_check_wiring.py`:

```python
# ── check_consistency ───────────────────────────────────────────────


class TestCheckConsistency:
    def test_reports_conflict_with_severity_fact_and_suggestions(self, m, storage):
        _seed_fact(storage)
        result = m.check_consistency.fn(statement=STATEMENT, category="npc")
        assert "ctr_" in result
        assert "major" in result
        assert "character" in result
        assert "Father Donavich is alive and hiding in the church" in result
        assert "flag_for_dm" in result
        assert "resolve_contradiction" in result

    def test_clean_statement_reports_no_conflicts(self, m, storage):
        _seed_fact(storage)
        result = m.check_consistency.fn(
            statement="The party shares a quiet meal at the tavern"
        )
        assert "No conflicts" in result

    def test_check_writes_nothing_to_disk(self, m, storage):
        _seed_fact(storage)
        detector = storage.contradiction_detector
        m.check_consistency.fn(statement=STATEMENT)
        assert not detector._contradictions_path.exists()
        assert detector.get_all_contradictions() == []

    def test_invalid_category_lists_valid_values(self, m, storage):
        result = m.check_consistency.fn(statement=STATEMENT, category="bogus")
        assert "Invalid category" in result
        assert "npc" in result

    def test_empty_statement_rejected(self, m, storage):
        result = m.check_consistency.fn(statement="   ")
        assert "empty" in result.lower()

    def test_detector_unavailable_degrades_with_guidance(self, m, storage):
        storage._contradiction_detector = None
        result = m.check_consistency.fn(statement=STATEMENT)
        assert "unavailable" in result.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_contradiction_check_wiring.py::TestCheckConsistency -v`
Expected: FAIL — `AttributeError: module 'dm20_protocol.main' has no attribute 'check_consistency'`

- [ ] **Step 3: Implement the tool**

In `src/dm20_protocol/main.py`, after the end of `get_session_recap` (before the
`# ----` section divider at ~line 5272):

```python
_CONTRADICTION_UNAVAILABLE = (
    "Contradiction check unavailable: the fact graph could not be loaded "
    "for this campaign (split-format campaigns only)."
)


@mcp.tool
def check_consistency(
    statement: Annotated[str, Field(description="The proposed statement to check against established facts (e.g., 'Father Donavich is dead')")],
    category: Annotated[str | None, Field(description="Optional fact category to narrow the check: event, location, npc, item, quest, world")] = None,
    tags: Annotated[str | None, Field(description="Optional tags to narrow the check (JSON list or comma-separated)")] = None,
) -> str:
    """Check a proposed statement for conflicts with established facts.

    Read-only pre-narration check: compares the statement against the fact
    graph and reports contradictions with severity, the conflicting facts,
    and suggested resolutions ranked by confidence. Nothing is persisted —
    to record a decision about a reported contradiction, call
    resolve_contradiction with its id.
    """
    campaign = storage.get_current_campaign()
    if not campaign:
        return "No active campaign. Load or create a campaign first."

    detector = storage.contradiction_detector
    fact_db = storage.fact_db
    if detector is None or fact_db is None:
        return _CONTRADICTION_UNAVAILABLE

    statement = statement.strip()
    if not statement:
        return "Statement cannot be empty."

    from .claudmaster.consistency.models import FactCategory

    category_enum = None
    if category:
        try:
            category_enum = FactCategory(category)
        except ValueError:
            valid = ", ".join(c.value for c in FactCategory)
            return f"Invalid category '{category}'. Valid categories: {valid}"

    related_tags = _parse_json_list(tags) if tags else None

    detected = detector.check_statement(
        statement,
        _current_session_number(),
        category=category_enum,
        related_tags=related_tags,
        register=False,
    )

    if not detected:
        return (
            f"✅ No conflicts detected: '{statement}' is consistent with the "
            "established facts."
        )

    lines = [
        f"⚠️ {len(detected)} potential contradiction(s) detected for: '{statement}'",
        "",
    ]
    for c in detected:
        lines.append(f"### {c.id} — {c.severity.value} ({c.contradiction_type.value})")
        lines.append("**Conflicts with:**")
        for fact_id in c.conflicting_fact_ids:
            fact = fact_db.get_fact(fact_id)
            if fact is not None:
                lines.append(f"- {fact_id} (session {fact.session_number}): {fact.content}")
            else:
                lines.append(f"- {fact_id}")
        lines.append("**Suggested resolutions:**")
        for s in detector.suggest_resolution(c):
            side = f" Side effects: {'; '.join(s.side_effects)}." if s.side_effects else ""
            lines.append(
                f"- {s.strategy.value} (confidence {s.confidence:.0%}): {s.description}.{side}"
            )
        lines.append("")
    lines.append(
        "Nothing was persisted. To record a decision, call "
        "resolve_contradiction(contradiction_id, strategy) — ids are valid for "
        "this session."
    )
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_contradiction_check_wiring.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/dm20_protocol/main.py tests/test_contradiction_check_wiring.py
git commit -m "feat(DM2-12): check_consistency tool — read-only pre-narration contradiction check"
```

---

### Task 4: resolve_contradiction MCP tool

**Files:**
- Modify: `src/dm20_protocol/main.py` (directly after `check_consistency`)
- Test: `tests/test_contradiction_check_wiring.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_contradiction_check_wiring.py`:

```python
# ── resolve_contradiction ───────────────────────────────────────────


class TestResolveContradiction:
    def _detect(self, m, storage) -> str:
        _seed_fact(storage)
        m.check_consistency.fn(statement=STATEMENT)
        pending_ids = list(storage.contradiction_detector._pending)
        assert len(pending_ids) == 1
        return pending_ids[0]

    def test_resolve_persists_with_strategy_and_notes(self, m, storage):
        cid = self._detect(m, storage)
        result = m.resolve_contradiction.fn(
            contradiction_id=cid, strategy="retcon", notes="He died offscreen"
        )
        assert "persisted" in result
        reloaded = ContradictionDetector(
            storage.fact_db,
            campaign_path=storage.contradiction_detector._campaign_path,
        )
        contradictions = reloaded.get_all_contradictions()
        assert len(contradictions) == 1
        assert contradictions[0].id == cid
        assert contradictions[0].resolved is True
        assert contradictions[0].resolution == ResolutionStrategy.RETCON
        assert contradictions[0].resolution_notes == "He died offscreen"

    def test_flag_alias_maps_to_flag_for_dm(self, m, storage):
        cid = self._detect(m, storage)
        result = m.resolve_contradiction.fn(contradiction_id=cid, strategy="flag")
        assert "flag_for_dm" in result

    def test_retcon_reminds_to_update_the_fact(self, m, storage):
        cid = self._detect(m, storage)
        result = m.resolve_contradiction.fn(contradiction_id=cid, strategy="retcon")
        assert "write tools" in result

    def test_unknown_id_explains_session_scope(self, m, storage):
        result = m.resolve_contradiction.fn(contradiction_id="ctr_nope", strategy="ignore")
        assert "not found" in result
        assert "check_consistency" in result

    def test_invalid_strategy_lists_valid_values(self, m, storage):
        result = m.resolve_contradiction.fn(contradiction_id="ctr_x", strategy="bogus")
        assert "Invalid strategy" in result
        assert "retcon" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_contradiction_check_wiring.py::TestResolveContradiction -v`
Expected: FAIL — `AttributeError: module 'dm20_protocol.main' has no attribute 'resolve_contradiction'`

- [ ] **Step 3: Implement the tool**

In `src/dm20_protocol/main.py`, directly after `check_consistency`:

```python
@mcp.tool
def resolve_contradiction(
    contradiction_id: Annotated[str, Field(description="Contradiction id from check_consistency output (e.g., 'ctr_1a2b3c4d')")],
    strategy: Annotated[str, Field(description="Chosen resolution: retcon, explain, ignore, or flag (escalate to the human DM)")],
    notes: Annotated[str | None, Field(description="Optional notes about the decision")] = None,
) -> str:
    """Persist a detected contradiction with the DM's chosen resolution.

    Records the decision in contradictions.json. Resolution is bookkeeping
    only: a retcon does not edit the conflicting fact — update it via the
    usual write tools afterwards. Contradiction ids come from
    check_consistency and are valid for the current session.
    """
    campaign = storage.get_current_campaign()
    if not campaign:
        return "No active campaign. Load or create a campaign first."

    detector = storage.contradiction_detector
    if detector is None:
        return _CONTRADICTION_UNAVAILABLE

    from .claudmaster.consistency.models import ResolutionStrategy

    normalized = "flag_for_dm" if strategy == "flag" else strategy
    try:
        strategy_enum = ResolutionStrategy(normalized)
    except ValueError:
        valid = ", ".join(s.value for s in ResolutionStrategy)
        return (
            f"Invalid strategy '{strategy}'. Valid strategies: {valid} "
            "(or 'flag' as shorthand for flag_for_dm)"
        )

    if not detector.resolve(contradiction_id, strategy_enum, notes):
        return (
            f"Contradiction '{contradiction_id}' not found. Detected contradictions "
            "are session-scoped — re-run check_consistency to detect it again."
        )

    detector.save()

    reminder = (
        " Remember: resolving records the decision only — apply the retcon by "
        "updating the conflicting fact/journal via the usual write tools."
        if strategy_enum == ResolutionStrategy.RETCON
        else ""
    )
    notes_text = f" Notes: {notes}" if notes else ""
    return (
        f"✅ Contradiction {contradiction_id} resolved as {strategy_enum.value} "
        f"and persisted to contradictions.json.{notes_text}{reminder}"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_contradiction_check_wiring.py tests/claudmaster/test_contradiction.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/dm20_protocol/main.py tests/test_contradiction_check_wiring.py
git commit -m "feat(DM2-12): resolve_contradiction tool — persist DM decisions to contradictions.json"
```

---

### Task 5: Persona activation

**Files:**
- Modify: `.claude/dm-persona.md` (Continuity Protocol section, ~line 71)

- [ ] **Step 1: Add the pre-narration bullet**

In `.claude/dm-persona.md`, in the Continuity Protocol bullet list, after the
`record_npc_interaction` bullet (before the "What does NOT need recording" paragraph), add:

```markdown
- **About to narrate something that asserts established canon** -- a returning NPC's fate, a location's state, a fact the party pinned down: `check_consistency` with the proposed statement first. It is read-only and fast. If it reports conflicts, adjust the narration to match canon -- or, when the divergence is deliberate, record the decision with `resolve_contradiction` (retcon / explain / ignore / flag).
```

- [ ] **Step 2: Commit**

```bash
git add .claude/dm-persona.md
git commit -m "feat(DM2-12): activate the pre-narration consistency check in the DM persona"
```

---

### Task 6: Focused regression sweep

- [ ] **Step 1: Run the focused suites**

Run:
`uv run pytest tests/test_contradiction_check_wiring.py tests/claudmaster/test_contradiction.py tests/test_fact_graph_lifecycle.py tests/test_knowledge_write_tools.py tests/test_timeline_wiring.py tests/test_milestone_seal_continuity_graph.py -v`

Expected: all PASS (no regressions in fact-graph lifecycle, knowledge tools, timeline wiring, or the v1 seal suite). The full pytest suite has ~143 pre-existing interaction failures on main — judge only these focused suites.

- [ ] **Step 2: Commit any stragglers**

Only if uncommitted changes remain from review fixes.
