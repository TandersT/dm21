# DM2-17 — Implementation plan

**Spec:** `docs/superpowers/specs/dm2-17-spellcaster-spell-slots.md`

TDD throughout: red test first for each task, then the minimal implementation.

## Task 1 — SRD slot progression module

New file: `src/dm20_protocol/srd_spell_slots.py`
New tests: `tests/test_srd_spell_slots.py`

- `slots_for_caster_type(caster_type: str, level: int) -> dict[int, int]` —
  supports `"full"`, `"half"`, `"third"`, `"pact"` (same literals as
  `SpellcastingInfo.caster_type`). Returns `{spell_level: max_slots}`; `{}` when
  the type/level grants none.
- `slots_for_class(class_name: str, level: int) -> dict[int, int]` —
  case-insensitive class-name → caster-type mapping, then delegates. Unknown
  names return `{}`.
- Tests: known PHB checkpoints (wizard 1 → {1:2}, wizard 5 → {1:4,2:3,3:2},
  cleric 20 ninth-level slot, paladin 1 → {}, paladin 5 → {1:4,2:2},
  warlock 1 → {1:1}, warlock 5 → {3:2}, warlock 17 → {5:4}, fighter → {},
  unknown name → {}).

## Task 2 — Shared heal helper + model validator (Q1)

File: `src/dm20_protocol/models.py`
Tests: `tests/test_character_model_v2.py` (new test class)

- Public method `Character.heal_missing_spell_slots() -> bool`: fires only when
  `spell_slots` is empty AND `spells_known` contains a leveled (`level > 0`)
  spell; derives slots from `srd_spell_slots.slots_for_class(classes[0].name,
  classes[0].level)`; returns True iff it populated something.
- `@model_validator(mode="after")` calling the helper (precedent:
  `_compute_proficiency_bonus` mode-after / `_migrate_character_class`
  migration-validator).
- Tests: broken-caster dict (Broden-shaped: sorcerer 1, leveled spells, empty
  slots) heals on `model_validate`; cantrip-only character untouched;
  non-caster untouched; populated slots untouched (rulebook data wins).

## Task 3 — Builder fallback (Q2 / AC1 gap)

File: `src/dm20_protocol/character_builder.py`
Tests: `tests/test_character_builder.py`

- In `create_character` step 6: when `class_def.spellcasting` is present but
  `_get_spell_slots` returns `{}`, fall back to the SRD table — first by class
  name, then by `class_def.spellcasting.caster_type`.
- Guard: don't override a legitimate empty result (half caster at level 1 gets
  `{}` from both paths — consistent).
- Test: class def with `spellcasting` info but no `spell_slots` table yields
  populated slots at the right level.

## Task 4 — Inline repair in `_use_spell_slot_logic` (Q3)

File: `src/dm20_protocol/main.py`
Tests: `tests/test_utility_tools.py` (extend `TestUseSpellSlotLogic`)

- When `spell_slots` is empty, attempt `character.heal_missing_spell_slots()`.
- Healed + slot available → consume and append the repair note
  " (spell slots were missing and have been repaired)".
- Not healable + no slots at all → non-caster error naming the cause ("doesn't
  appear to be a spellcaster — no leveled spells known").
- Existing behaviors unchanged: invalid level bounds, "no level N spell slots"
  (slots exist but not at N), "no ... remaining (0/X)".
- Tests: broken caster repair-and-consume (note present, `spell_slots_used`
  incremented, slots persisted on model); non-caster clear error; healed
  character requesting a too-high slot level still errors.

## Verification

1. Each task: red test → implementation → green.
2. Full scopes: `uv run pytest tests/test_srd_spell_slots.py
   tests/test_character_model_v2.py tests/test_character_builder.py
   tests/test_utility_tools.py`
3. Adjacent surfaces touched by the validator: `uv run pytest
   tests/test_storage.py tests/test_main.py tests/test_multiclass.py
   tests/test_level_up_engine.py tests/test_character_v2_e2e.py`
