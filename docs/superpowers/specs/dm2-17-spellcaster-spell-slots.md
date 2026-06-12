# DM2-17 — Fix spellcasters created without spell slots

**Ticket:** [DM2-17](https://linear.app/dm21/issue/DM2-17/fix-spellcasters-created-without-spell-slots)
**Size:** Standard
**Base:** sta/dm2-16-adventure-parser-tests

## Problem

A spellcasting character can end up with empty `spell_slots` / `spell_slots_used`
(`{}`), so consuming a slot for a leveled spell fails during play. Observed with
Broden Arolio (Curse of Strahd): 6 `spells_known` incl. Mage Armor and Shield,
`spellcasting_ability: charisma`, zero slots.

Root causes (verified):

- `character_builder.py` (`_get_spell_slots`) silently yields `{}` when the loaded
  rulebook class definition has no `spellcasting.spell_slots` table.
- The `Character` model defaults both slot dicts to `{}` (`models.py:365-366`) and
  nothing repairs them on load.
- `_use_spell_slot_logic` (`main.py:1038`) returns the same opaque error for broken
  casters (leveled spells, no slots) and true non-casters.
- Broden's data is runtime-only (not in the repo), so the repair must be product
  behavior — not a one-off data fix.

## Decisions (pinned in design review)

**Q1 — Repair mechanism: model-level self-heal.** A Pydantic `@model_validator` on
`Character` (following the existing `_migrate_character_class` migration-validator
precedent) populates missing `spell_slots` when the character knows leveled spells
but `spell_slots` is empty. The heal fires at load/construction and persists on the
next save.

**Q2 — Slot data source: built-in SRD progression table.** New small module
`src/dm20_protocol/srd_spell_slots.py`: full/half/third caster progressions plus
warlock pact magic, keyed by class name (with a caster-type fallback for unknown
class names). Rulebook `class_def.spellcasting` slot data still wins when present —
the table is the fallback. This also closes the AC1 builder gap: when a loaded
rulebook class has `spellcasting` info but no slot table, the builder falls back to
the SRD table instead of silently yielding `{}`.

**Q3 — `use_spell_slot` with no slots: auto-repair inline, then consume and
report.** (User's explicit pick over the recommended diagnose-only error.)
`_use_spell_slot_logic` detects the broken-caster signature (leveled `spells_known`
+ empty `spell_slots`), repairs via the SAME shared heal helper the Q1 validator
uses (single source of heal logic), then consumes the slot and reports success with
a repair note: "(spell slots were missing and have been repaired)". A true
non-caster (no leveled spells) gets a clear, actionable error instead.

The three pins compose: the validator heals at load; `use_spell_slot` heals inline
as belt-and-braces if a broken character somehow reaches consumption unhealed; both
use the shared SRD-table-backed helper.

### Alternatives considered (resolved by the user in design review)

- Q1: repair via a storage-layer migration pass or an explicit `repair_character`
  MCP tool — rejected in favor of the model validator (matches the existing
  `_migrate_character_class` precedent; zero new surface).
- Q2: require a loaded rulebook for slot data — rejected: rulebook data is
  runtime-fetched, so load-time repair cannot assume one is loaded.
- Q3: diagnose-only error ("no slots — character data may be broken, run X") —
  was the recommendation; the user explicitly chose inline auto-repair instead.

## Design details

- **Multiclass:** follow existing builder behavior — derive from the primary class
  (`classes[0]`) name and its level.
- **Heal trigger signature:** `spell_slots == {}` AND at least one spell in
  `spells_known` with `level > 0`. Characters with populated slots, or with only
  cantrips, are untouched. `spell_slots_used` is left as-is (defaults `{}` = all
  slots available).
- **SRD progressions:** full-caster table (levels 1-20, PHB ch. 10); half casters
  use the full table at `ceil(level / 2)` with no slots at level 1; third casters
  use it at `ceil(level / 3)` with no slots before level 3; warlock pact magic
  gives N slots of a single pact level.
- **Class mapping:** full = bard/cleric/druid/sorcerer/wizard; half =
  paladin/ranger/artificer; pact = warlock. Unknown class names yield `{}` from the
  class-name lookup; the builder additionally falls back to
  `class_def.spellcasting.caster_type` (the rulebook says it's a caster even if the
  name is unrecognized, e.g. homebrew).

## Acceptance criteria (from the ticket)

- [ ] Creating or importing a spellcaster yields populated `spell_slots` for its
      class and level
- [ ] Existing characters with known spells but empty slots are repaired (Broden
      Arolio in Curse of Strahd works)
- [ ] Consuming a slot with none available returns a clear, actionable error to
      the DM
- [ ] Narrative slot tracking and model state agree after a leveled cast (follows
      from AC1-AC3: the tool already returns remaining/max for the narrative to
      echo)
