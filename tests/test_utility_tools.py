"""Tests for character utility tools: spell slots, rests, death saves, update_character list ops."""

import pytest

from dm20_protocol.models import (
    AbilityScore,
    Character,
    CharacterClass,
    Item,
    Race,
    Spell,
)
from dm20_protocol.main import (
    _parse_json_list,
    _use_spell_slot_logic,
    _add_spell_logic,
    _remove_spell_logic,
    _long_rest_logic,
    _short_rest_logic,
    _add_death_save_logic,
    _LIST_OPERATIONS,
)


# ─── Helpers ───────────────────────────────────────────────────────────


def make_character(
    name: str = "Elara",
    level: int = 5,
    class_name: str = "Wizard",
    hit_dice: str = "5d6",
    hit_dice_type: str = "d6",
    hp_max: int = 28,
    hp_current: int = 28,
    spell_slots: dict[int, int] | None = None,
    spell_slots_used: dict[int, int] | None = None,
    spells_known: list[Spell] | None = None,
    conditions: list[str] | None = None,
    con_score: int = 12,
    hit_dice_remaining: str | None = None,
) -> Character:
    """Create a test character with spellcasting setup."""
    return Character(
        name=name,
        character_class=CharacterClass(
            name=class_name, level=level, hit_dice=hit_dice
        ),
        race=Race(name="Elf"),
        hit_points_max=hp_max,
        hit_points_current=hp_current,
        hit_dice_type=hit_dice_type,
        hit_dice_remaining=hit_dice_remaining or hit_dice,
        spell_slots=spell_slots or {},
        spell_slots_used=spell_slots_used or {},
        spells_known=spells_known or [],
        conditions=conditions or [],
        abilities={
            "strength": AbilityScore(score=10),
            "dexterity": AbilityScore(score=14),
            "constitution": AbilityScore(score=con_score),
            "intelligence": AbilityScore(score=18),
            "wisdom": AbilityScore(score=12),
            "charisma": AbilityScore(score=8),
        },
    )


def make_spell(
    name: str = "Fireball",
    level: int = 3,
    school: str = "evocation",
) -> Spell:
    """Create a test spell."""
    return Spell(
        name=name,
        level=level,
        school=school,
        casting_time="1 action",
        range=150,
        duration="instantaneous",
        components=["V", "S", "M"],
        description=f"A {name} spell.",
    )


# ─── Test: _parse_json_list ──────────────────────────────────────────


class TestParseJsonList:

    def test_json_array(self):
        assert _parse_json_list('["poisoned", "prone"]') == ["poisoned", "prone"]

    def test_single_json_string(self):
        assert _parse_json_list('"poisoned"') == ["poisoned"]

    def test_comma_separated_fallback(self):
        assert _parse_json_list("poisoned, prone") == ["poisoned", "prone"]

    def test_single_value_no_json(self):
        assert _parse_json_list("poisoned") == ["poisoned"]

    def test_empty_json_array(self):
        assert _parse_json_list("[]") == []

    def test_numeric_items(self):
        assert _parse_json_list("[1, 2, 3]") == ["1", "2", "3"]

    def test_whitespace_handling(self):
        assert _parse_json_list("  fire,  ice  , water ") == ["fire", "ice", "water"]


# ─── Test: _use_spell_slot_logic ─────────────────────────────────────


class TestUseSpellSlotLogic:

    def test_use_available_slot(self):
        char = make_character(spell_slots={1: 4, 2: 3, 3: 2})
        result = _use_spell_slot_logic(char, 1)
        assert "✅" in result
        assert "3/4 remaining" in result
        assert char.spell_slots_used[1] == 1

    def test_use_last_slot(self):
        char = make_character(
            spell_slots={1: 4}, spell_slots_used={1: 3}
        )
        result = _use_spell_slot_logic(char, 1)
        assert "✅" in result
        assert "0/4 remaining" in result
        assert char.spell_slots_used[1] == 4

    def test_no_slots_remaining(self):
        char = make_character(
            spell_slots={1: 4}, spell_slots_used={1: 4}
        )
        result = _use_spell_slot_logic(char, 1)
        assert "❌" in result
        assert "no level 1 spell slots remaining" in result

    def test_no_slots_at_level(self):
        char = make_character(spell_slots={1: 4})
        result = _use_spell_slot_logic(char, 5)
        assert "❌" in result
        assert "no level 5 spell slots" in result

    def test_invalid_level_too_low(self):
        char = make_character()
        result = _use_spell_slot_logic(char, 0)
        assert "❌" in result

    def test_invalid_level_too_high(self):
        char = make_character()
        result = _use_spell_slot_logic(char, 10)
        assert "❌" in result

    def test_multiple_uses(self):
        char = make_character(spell_slots={2: 3})
        _use_spell_slot_logic(char, 2)
        _use_spell_slot_logic(char, 2)
        result = _use_spell_slot_logic(char, 2)
        assert "0/3 remaining" in result
        assert char.spell_slots_used[2] == 3

        # Fourth use should fail
        result = _use_spell_slot_logic(char, 2)
        assert "❌" in result

    def test_broken_caster_auto_repairs_and_consumes(self):
        """Leveled spells known + empty slots: repair via SRD, consume, note it."""
        char = make_character(spells_known=[make_spell("Fireball", 3)])
        char.spell_slots = {}  # simulate a broken persisted character
        result = _use_spell_slot_logic(char, 1)
        assert "✅" in result
        assert "3/4 remaining" in result
        assert "spell slots were missing and have been repaired" in result
        assert char.spell_slots == {1: 4, 2: 3, 3: 2}
        assert char.spell_slots_used[1] == 1

    def test_non_caster_gets_clear_error(self):
        char = make_character()  # no spells known, no slots
        result = _use_spell_slot_logic(char, 1)
        assert "❌" in result
        assert "don't appear to be a spellcaster" in result
        assert char.spell_slots == {}

    def test_repaired_caster_still_lacks_requested_level(self):
        """Repair happens, but a too-high slot level still errors clearly."""
        char = make_character(
            level=1, hit_dice="1d6", spells_known=[make_spell("Mage Armor", 1)]
        )
        char.spell_slots = {}
        result = _use_spell_slot_logic(char, 5)
        assert "❌" in result
        assert "no level 5 spell slots" in result
        assert char.spell_slots == {1: 2}
        assert char.spell_slots_used == {}

    def test_unrepairable_caster_gets_actionable_error(self):
        """Unknown class: no SRD progression — point the DM at a manual fix."""
        char = make_character(
            class_name="Homebrewmancer",
            spells_known=[make_spell("Mage Armor", 1)],
        )
        char.spell_slots = {}
        result = _use_spell_slot_logic(char, 1)
        assert "❌" in result
        assert "update_character" in result


# ─── Test: _add_spell_logic ──────────────────────────────────────────


class TestAddSpellLogic:

    def test_add_new_spell(self):
        char = make_character()
        spell = make_spell("Fireball", level=3)
        result = _add_spell_logic(char, spell)
        assert "✅" in result
        assert "Fireball" in result
        assert len(char.spells_known) == 1
        assert char.spells_known[0].name == "Fireball"

    def test_add_duplicate_spell(self):
        existing = make_spell("Fireball")
        char = make_character(spells_known=[existing])
        new_spell = make_spell("Fireball")
        result = _add_spell_logic(char, new_spell)
        assert "already knows" in result
        assert len(char.spells_known) == 1

    def test_add_duplicate_case_insensitive(self):
        existing = make_spell("Fireball")
        char = make_character(spells_known=[existing])
        new_spell = make_spell("fireball")
        result = _add_spell_logic(char, new_spell)
        assert "already knows" in result

    def test_add_multiple_different_spells(self):
        char = make_character()
        _add_spell_logic(char, make_spell("Fireball", level=3))
        _add_spell_logic(char, make_spell("Shield", level=1))
        _add_spell_logic(char, make_spell("Mage Armor", level=1))
        assert len(char.spells_known) == 3


# ─── Test: _remove_spell_logic ───────────────────────────────────────


class TestRemoveSpellLogic:

    def test_remove_by_name(self):
        spell = make_spell("Fireball")
        char = make_character(spells_known=[spell])
        result = _remove_spell_logic(char, "Fireball")
        assert "✅" in result
        assert "Removed Fireball" in result
        assert len(char.spells_known) == 0

    def test_remove_by_name_case_insensitive(self):
        spell = make_spell("Fireball")
        char = make_character(spells_known=[spell])
        result = _remove_spell_logic(char, "fireball")
        assert "✅" in result
        assert len(char.spells_known) == 0

    def test_remove_by_id(self):
        spell = make_spell("Fireball")
        spell_id = spell.id
        char = make_character(spells_known=[spell])
        result = _remove_spell_logic(char, spell_id)
        assert "✅" in result
        assert len(char.spells_known) == 0

    def test_remove_not_found(self):
        char = make_character(spells_known=[make_spell("Fireball")])
        result = _remove_spell_logic(char, "Ice Storm")
        assert "❌" in result
        assert len(char.spells_known) == 1

    def test_remove_preserves_other_spells(self):
        fireball = make_spell("Fireball")
        shield = make_spell("Shield", level=1)
        char = make_character(spells_known=[fireball, shield])
        _remove_spell_logic(char, "Fireball")
        assert len(char.spells_known) == 1
        assert char.spells_known[0].name == "Shield"


# ─── Test: _long_rest_logic ──────────────────────────────────────────


class TestLongRestLogic:

    def test_basic_long_rest(self):
        char = make_character(
            spell_slots={1: 4, 2: 3},
            spell_slots_used={1: 3, 2: 2},
            hp_current=10,
            hp_max=28,
        )
        result = _long_rest_logic(char)
        assert "✅" in result
        assert "Spell slots restored" in result
        assert "HP restored to 28" in result
        assert char.spell_slots_used == {1: 0, 2: 0}
        assert char.hit_points_current == 28

    def test_long_rest_no_hp_restore(self):
        char = make_character(hp_current=10, hp_max=28)
        result = _long_rest_logic(char, restore_hp=False)
        assert "HP restored" not in result
        assert char.hit_points_current == 10

    def test_long_rest_hit_dice_restore(self):
        """Long rest restores half total hit dice (minimum 1)."""
        char = make_character(
            level=5,
            hit_dice="5d6",
            hit_dice_remaining="1d6",
        )
        result = _long_rest_logic(char)
        assert "Hit dice:" in result
        # Should restore 2 dice (5 // 2 = 2), bringing to 3
        assert char.hit_dice_remaining == "3d6"

    def test_long_rest_hit_dice_no_overcap(self):
        """Hit dice cannot exceed total (class level)."""
        char = make_character(
            level=5,
            hit_dice="5d6",
            hit_dice_remaining="4d6",
        )
        _long_rest_logic(char)
        assert char.hit_dice_remaining == "5d6"

    def test_long_rest_level_1_hit_dice(self):
        """Level 1 character restores minimum 1 hit die."""
        char = make_character(
            level=1,
            hit_dice="1d6",
            hit_dice_remaining="0d6",
        )
        _long_rest_logic(char)
        assert char.hit_dice_remaining == "1d6"

    def test_long_rest_resets_death_saves(self):
        char = make_character()
        char.death_saves_success = 2
        char.death_saves_failure = 1
        result = _long_rest_logic(char)
        assert "Death saves reset" in result
        assert char.death_saves_success == 0
        assert char.death_saves_failure == 0

    def test_long_rest_temp_hp_removed(self):
        char = make_character(hp_max=28, hp_current=20)
        char.temporary_hit_points = 5
        _long_rest_logic(char)
        assert char.temporary_hit_points == 0
        assert char.hit_points_current == 28

    def test_long_rest_no_spell_slots(self):
        """Non-casters: long rest still works, no spell slot message."""
        char = make_character(
            class_name="Fighter",
            spell_slots={},
            hp_current=10,
            hp_max=28,
        )
        result = _long_rest_logic(char)
        assert "Spell slots restored" not in result
        assert "HP restored to 28" in result


# ─── Test: _short_rest_logic ─────────────────────────────────────────


class TestShortRestLogic:

    def test_short_rest_no_dice(self):
        char = make_character()
        result = _short_rest_logic(char, 0)
        assert "✅" in result
        assert "no hit dice spent" in result

    def test_short_rest_spend_dice(self):
        char = make_character(
            hp_current=10,
            hp_max=28,
            con_score=14,  # +2 modifier
            hit_dice_remaining="5d6",
        )
        result = _short_rest_logic(char, 2)
        assert "✅" in result
        assert "spent 2d6" in result
        assert char.hit_points_current > 10  # Should heal at least 2 (min 1 per die)
        assert char.hit_points_current <= 28  # Can't exceed max
        assert char.hit_dice_remaining == "3d6"

    def test_short_rest_no_dice_remaining(self):
        char = make_character(hit_dice_remaining="0d6")
        result = _short_rest_logic(char, 1)
        assert "❌" in result
        assert "no hit dice remaining" in result

    def test_short_rest_clamp_to_available(self):
        """Requesting more dice than available uses only what's left."""
        char = make_character(
            hp_current=10, hp_max=28,
            hit_dice_remaining="2d6",
        )
        result = _short_rest_logic(char, 5)
        assert "spent 2d6" in result
        assert char.hit_dice_remaining == "0d6"

    def test_short_rest_hp_capped_at_max(self):
        char = make_character(
            hp_current=27, hp_max=28,
            con_score=20,  # +5 modifier, high healing
            hit_dice_remaining="5d6",
        )
        _short_rest_logic(char, 3)
        assert char.hit_points_current <= char.hit_points_max

    def test_short_rest_min_1_per_die(self):
        """Each die heals at least 1 HP (even with negative CON mod)."""
        char = make_character(
            hp_current=10, hp_max=28,
            con_score=6,  # -2 modifier
            hit_dice_remaining="5d6",
        )
        old_hp = char.hit_points_current
        _short_rest_logic(char, 1)
        assert char.hit_points_current >= old_hp + 1


# ─── Test: _add_death_save_logic ─────────────────────────────────────


class TestAddDeathSaveLogic:

    def test_first_success(self):
        char = make_character()
        result = _add_death_save_logic(char, success=True)
        assert "SUCCESS" in result
        assert "1/3 successes" in result
        assert char.death_saves_success == 1

    def test_first_failure(self):
        char = make_character()
        result = _add_death_save_logic(char, success=False)
        assert "FAILURE" in result
        assert "1/3 failures" in result
        assert char.death_saves_failure == 1

    def test_stabilize_at_3_successes(self):
        char = make_character(hp_current=0)
        char.death_saves_success = 2
        char.conditions = ["unconscious"]

        result = _add_death_save_logic(char, success=True)

        assert "stabilized" in result
        assert char.death_saves_success == 0
        assert char.death_saves_failure == 0
        assert char.hit_points_current == 1
        assert "unconscious" not in char.conditions

    def test_death_at_3_failures(self):
        char = make_character(hp_current=0)
        char.death_saves_failure = 2

        result = _add_death_save_logic(char, success=False)

        assert "DIED" in result
        assert "💀" in result
        assert char.death_saves_failure == 3

    def test_mixed_saves(self):
        char = make_character(hp_current=0)

        _add_death_save_logic(char, success=True)   # 1/3 success
        _add_death_save_logic(char, success=False)   # 1/3 failure
        _add_death_save_logic(char, success=True)   # 2/3 success
        _add_death_save_logic(char, success=False)   # 2/3 failure

        assert char.death_saves_success == 2
        assert char.death_saves_failure == 2

        result = _add_death_save_logic(char, success=True)  # 3/3 success → stabilize
        assert "stabilized" in result

    def test_success_caps_at_3(self):
        """Death save success count doesn't exceed 3."""
        char = make_character()
        char.death_saves_success = 2
        _add_death_save_logic(char, success=True)
        # Should stabilize, resetting to 0
        assert char.death_saves_success == 0

    def test_stabilize_without_unconscious_condition(self):
        """Stabilization works even if 'unconscious' isn't in conditions."""
        char = make_character(hp_current=0)
        char.death_saves_success = 2
        result = _add_death_save_logic(char, success=True)
        assert "stabilized" in result
        assert char.hit_points_current == 1


# ─── Test: List Operations Mapping ───────────────────────────────────


class TestListOperationsMapping:
    """Verify the _LIST_OPERATIONS mapping is complete and correct."""

    def test_all_list_fields_have_add_and_remove(self):
        fields_covered = set()
        for param_name, (field_name, op) in _LIST_OPERATIONS.items():
            fields_covered.add(field_name)
            assert op in ("add", "remove")

        # All Character list fields should be mapped
        expected_fields = {
            "conditions",
            "skill_proficiencies",
            "tool_proficiencies",
            "languages",
            "saving_throw_proficiencies",
            "features_and_traits",
        }
        assert fields_covered == expected_fields

    def test_add_remove_pairs_match(self):
        """Each add operation has a matching remove for the same field."""
        add_ops = {v[0] for k, v in _LIST_OPERATIONS.items() if v[1] == "add"}
        remove_ops = {v[0] for k, v in _LIST_OPERATIONS.items() if v[1] == "remove"}
        assert add_ops == remove_ops


# ─── Test: List Add/Remove on Character ──────────────────────────────


class TestListOperationsOnCharacter:
    """Test the list add/remove logic as used in update_character."""

    def test_add_conditions(self):
        char = make_character()
        items = _parse_json_list('["poisoned", "prone"]')
        for item in items:
            if item not in char.conditions:
                char.conditions.append(item)
        assert char.conditions == ["poisoned", "prone"]

    def test_add_duplicate_condition_idempotent(self):
        char = make_character(conditions=["poisoned"])
        items = _parse_json_list('["poisoned", "stunned"]')
        added = []
        for item in items:
            if item not in char.conditions:
                char.conditions.append(item)
                added.append(item)
        assert char.conditions == ["poisoned", "stunned"]
        assert added == ["stunned"]

    def test_remove_conditions(self):
        char = make_character(conditions=["poisoned", "prone", "stunned"])
        items = _parse_json_list('["poisoned", "prone"]')
        for item in items:
            if item in char.conditions:
                char.conditions.remove(item)
        assert char.conditions == ["stunned"]

    def test_remove_nonexistent_condition(self):
        char = make_character(conditions=["poisoned"])
        items = _parse_json_list('["blinded"]')
        not_found = []
        for item in items:
            if item in char.conditions:
                char.conditions.remove(item)
            else:
                not_found.append(item)
        assert not_found == ["blinded"]
        assert char.conditions == ["poisoned"]

    def test_add_languages(self):
        char = make_character()
        char.languages = ["Common", "Elvish"]
        items = _parse_json_list('["Dwarvish", "Draconic"]')
        for item in items:
            if item not in char.languages:
                char.languages.append(item)
        assert char.languages == ["Common", "Elvish", "Dwarvish", "Draconic"]
