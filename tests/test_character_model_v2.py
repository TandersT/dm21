"""Tests for Character Model v2 extensions (Issue #99).

Tests cover:
- Feature model creation and validation
- New Character fields with defaults
- Computed proficiency_bonus from level
- Backward compatibility with v1 character JSON
"""

import json
import pytest
from dm20_protocol.models import (
    Character,
    CharacterClass,
    Race,
    Feature,
    AbilityScore,
    Item,
    Spell,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_character(**overrides) -> Character:
    """Create a minimal Character with sensible defaults."""
    defaults = {
        "name": "Test Hero",
        "character_class": CharacterClass(name="Fighter", level=1, hit_dice="1d10"),
        "race": Race(name="Human"),
    }
    defaults.update(overrides)
    return Character(**defaults)


# A v1-style character JSON: only fields that existed before v2 extension.
V1_CHARACTER_JSON = {
    "id": "abc12345",
    "name": "Old Warrior",
    "player_name": "Alice",
    "character_class": {"name": "Fighter", "level": 5, "hit_dice": "1d10", "subclass": None},
    "race": {"name": "Human", "subrace": None, "traits": []},
    "background": "Soldier",
    "alignment": "Lawful Good",
    "description": "A grizzled veteran.",
    "bio": "Fought in many wars.",
    "abilities": {
        "strength": {"score": 16},
        "dexterity": {"score": 12},
        "constitution": {"score": 14},
        "intelligence": {"score": 10},
        "wisdom": {"score": 13},
        "charisma": {"score": 8},
    },
    "armor_class": 18,
    "hit_points_max": 44,
    "hit_points_current": 44,
    "temporary_hit_points": 0,
    "hit_dice_remaining": "5d10",
    "death_saves_success": 0,
    "death_saves_failure": 0,
    "proficiency_bonus": 2,  # Stored as 2 in v1, should be recalculated to 3
    "skill_proficiencies": ["Athletics", "Intimidation"],
    "saving_throw_proficiencies": ["STR", "CON"],
    "inventory": [],
    "equipment": {
        "weapon_main": None,
        "weapon_off": None,
        "armor": None,
        "shield": None,
    },
    "spellcasting_ability": None,
    "spell_slots": {},
    "spell_slots_used": {},
    "spells_known": [],
    "features_and_traits": ["Second Wind", "Action Surge", "Extra Attack"],
    "languages": ["Common"],
    "inspiration": False,
    "notes": "",
    "created_at": "2026-01-01T00:00:00",
    "updated_at": "2026-01-01T00:00:00",
}


# ---------------------------------------------------------------------------
# Feature Model Tests
# ---------------------------------------------------------------------------

class TestFeatureModel:
    def test_create_feature_minimal(self):
        f = Feature(name="Darkvision", source="Elf")
        assert f.name == "Darkvision"
        assert f.source == "Elf"
        assert f.description == ""
        assert f.level_gained == 1

    def test_create_feature_full(self):
        f = Feature(
            name="Favored Enemy",
            source="Ranger 1",
            description="You have advantage on Wisdom (Survival) checks to track your favored enemies.",
            level_gained=1,
        )
        assert f.name == "Favored Enemy"
        assert f.source == "Ranger 1"
        assert f.level_gained == 1
        assert "advantage" in f.description

    def test_feature_serialization_roundtrip(self):
        f = Feature(name="Action Surge", source="Fighter 2", level_gained=2)
        data = f.model_dump()
        f2 = Feature(**data)
        assert f == f2

    def test_feature_json_roundtrip(self):
        f = Feature(name="Cunning Action", source="Rogue 2", level_gained=2)
        json_str = f.model_dump_json()
        f2 = Feature.model_validate_json(json_str)
        assert f == f2


# ---------------------------------------------------------------------------
# Character New Fields Tests
# ---------------------------------------------------------------------------

class TestCharacterNewFields:
    def test_default_experience_points(self):
        c = make_character()
        assert c.experience_points == 0

    def test_default_speed(self):
        c = make_character()
        assert c.speed == 30

    def test_default_conditions(self):
        c = make_character()
        assert c.conditions == []

    def test_default_tool_proficiencies(self):
        c = make_character()
        assert c.tool_proficiencies == []

    def test_default_features(self):
        c = make_character()
        assert c.features == []

    def test_default_hit_dice_type(self):
        c = make_character()
        assert c.hit_dice_type == "d8"

    def test_set_experience_points(self):
        c = make_character(experience_points=300)
        assert c.experience_points == 300

    def test_set_speed(self):
        c = make_character(speed=35)
        assert c.speed == 35

    def test_set_conditions(self):
        c = make_character(conditions=["poisoned", "prone"])
        assert c.conditions == ["poisoned", "prone"]

    def test_set_tool_proficiencies(self):
        c = make_character(tool_proficiencies=["Thieves' Tools", "Smith's Tools"])
        assert len(c.tool_proficiencies) == 2

    def test_set_features(self):
        features = [
            Feature(name="Darkvision", source="Elf"),
            Feature(name="Favored Enemy", source="Ranger 1"),
        ]
        c = make_character(features=features)
        assert len(c.features) == 2
        assert c.features[0].name == "Darkvision"

    def test_features_and_traits_still_works(self):
        """Legacy field should still be usable alongside new features field."""
        c = make_character(
            features_and_traits=["Second Wind", "Action Surge"],
            features=[Feature(name="Second Wind", source="Fighter 1")],
        )
        assert len(c.features_and_traits) == 2
        assert len(c.features) == 1


# ---------------------------------------------------------------------------
# Proficiency Bonus Computation Tests
# ---------------------------------------------------------------------------

class TestProficiencyBonus:
    """Proficiency bonus = 2 + (level - 1) // 4"""

    @pytest.mark.parametrize(
        "level, expected_bonus",
        [
            (1, 2), (2, 2), (3, 2), (4, 2),
            (5, 3), (6, 3), (7, 3), (8, 3),
            (9, 4), (10, 4), (11, 4), (12, 4),
            (13, 5), (14, 5), (15, 5), (16, 5),
            (17, 6), (18, 6), (19, 6), (20, 6),
        ],
    )
    def test_proficiency_bonus_at_level(self, level, expected_bonus):
        c = make_character(
            character_class=CharacterClass(name="Fighter", level=level, hit_dice="1d10")
        )
        assert c.proficiency_bonus == expected_bonus

    def test_proficiency_bonus_overrides_stored_value(self):
        """Even if proficiency_bonus is explicitly set, the validator recalculates it."""
        c = make_character(
            character_class=CharacterClass(name="Fighter", level=9, hit_dice="1d10"),
            proficiency_bonus=2,  # Wrong value for level 9
        )
        assert c.proficiency_bonus == 4  # Corrected by validator


# ---------------------------------------------------------------------------
# Backward Compatibility Tests
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def test_load_v1_character_json(self):
        """V1 character JSON (without new fields) should load without errors."""
        c = Character(**V1_CHARACTER_JSON)
        assert c.name == "Old Warrior"
        assert c.character_class.level == 5

    def test_v1_new_fields_get_defaults(self):
        """New fields should have sensible defaults when loaded from v1 JSON."""
        c = Character(**V1_CHARACTER_JSON)
        assert c.experience_points == 0
        assert c.speed == 30
        assert c.conditions == []
        assert c.tool_proficiencies == []
        assert c.features == []
        assert c.hit_dice_type == "d8"

    def test_v1_proficiency_bonus_recalculated(self):
        """V1 character at level 5 should get proficiency bonus recalculated to 3."""
        c = Character(**V1_CHARACTER_JSON)
        assert c.proficiency_bonus == 3  # Level 5 = +3, not +2

    def test_v1_existing_fields_preserved(self):
        """All v1 fields should be preserved when loaded."""
        c = Character(**V1_CHARACTER_JSON)
        assert c.player_name == "Alice"
        assert c.background == "Soldier"
        assert c.alignment == "Lawful Good"
        assert c.armor_class == 18
        assert c.hit_points_max == 44
        assert c.skill_proficiencies == ["Athletics", "Intimidation"]
        assert c.saving_throw_proficiencies == ["STR", "CON"]
        assert "Second Wind" in c.features_and_traits
        assert c.languages == ["Common"]

    def test_v1_serialization_roundtrip(self):
        """V1 → Character → JSON → Character should work without data loss."""
        c1 = Character(**V1_CHARACTER_JSON)
        json_str = c1.model_dump_json()
        c2 = Character.model_validate_json(json_str)
        assert c1.name == c2.name
        assert c1.character_class.level == c2.character_class.level
        assert c1.proficiency_bonus == c2.proficiency_bonus
        assert c1.features == c2.features  # Both empty

    def test_v2_character_with_features_serialization(self):
        """V2 character with features should serialize and deserialize correctly."""
        features = [
            Feature(name="Darkvision", source="Elf"),
            Feature(name="Extra Attack", source="Fighter 5", level_gained=5),
        ]
        c1 = make_character(
            character_class=CharacterClass(name="Fighter", level=5, hit_dice="1d10"),
            features=features,
            experience_points=6500,
            speed=30,
            conditions=["blessed"],
            tool_proficiencies=["Smith's Tools"],
        )
        json_str = c1.model_dump_json()
        c2 = Character.model_validate_json(json_str)
        assert len(c2.features) == 2
        assert c2.features[1].name == "Extra Attack"
        assert c2.experience_points == 6500
        assert c2.conditions == ["blessed"]
        assert c2.tool_proficiencies == ["Smith's Tools"]


# ---------------------------------------------------------------------------
# Spell Slot Self-Heal Tests (DM2-17)
# ---------------------------------------------------------------------------

def _spell(name: str, level: int) -> dict:
    """Minimal spell dict for model_validate payloads."""
    return {
        "name": name,
        "level": level,
        "school": "abjuration",
        "casting_time": "1 action",
        "duration": "instantaneous",
        "components": ["V", "S"],
        "description": f"A {name} spell.",
    }


class TestSpellSlotSelfHeal:
    def test_broken_caster_heals_on_load(self):
        """A caster with leveled spells but empty slots gets SRD slots on load."""
        data = dict(
            V1_CHARACTER_JSON,
            character_class={"name": "Sorcerer", "level": 1, "hit_dice": "1d6", "subclass": None},
            spellcasting_ability="charisma",
            spells_known=[_spell("Mage Armor", 1), _spell("Shield", 1)],
        )
        char = Character.model_validate(data)
        assert char.spell_slots == {1: 2}
        assert char.spell_slots_used == {}

    def test_cantrip_only_caster_untouched(self):
        data = dict(
            V1_CHARACTER_JSON,
            character_class={"name": "Wizard", "level": 1, "hit_dice": "1d6", "subclass": None},
            spells_known=[_spell("Fire Bolt", 0)],
        )
        char = Character.model_validate(data)
        assert char.spell_slots == {}

    def test_non_caster_untouched(self):
        char = Character.model_validate(dict(V1_CHARACTER_JSON))
        assert char.spell_slots == {}

    def test_populated_slots_untouched(self):
        """Existing (e.g. rulebook-derived) slot data is never overridden."""
        data = dict(
            V1_CHARACTER_JSON,
            character_class={"name": "Wizard", "level": 5, "hit_dice": "1d6", "subclass": None},
            spell_slots={1: 99},
            spells_known=[_spell("Magic Missile", 1)],
        )
        char = Character.model_validate(data)
        assert char.spell_slots == {1: 99}

    def test_unknown_class_not_healed(self):
        data = dict(
            V1_CHARACTER_JSON,
            character_class={"name": "Homebrewmancer", "level": 3, "hit_dice": "1d8", "subclass": None},
            spells_known=[_spell("Magic Missile", 1)],
        )
        char = Character.model_validate(data)
        assert char.spell_slots == {}

    def test_heal_helper_returns_whether_it_repaired(self):
        char = make_character(
            character_class=CharacterClass(name="Wizard", level=5, hit_dice="1d6"),
        )
        char.spells_known = [Spell.model_validate(_spell("Fireball", 3))]
        char.spell_slots = {}
        assert char.heal_missing_spell_slots() is True
        assert char.spell_slots == {1: 4, 2: 3, 3: 2}
        # Second call is a no-op.
        assert char.heal_missing_spell_slots() is False

    def test_multiclass_heals_from_primary_class(self):
        data = dict(
            V1_CHARACTER_JSON,
            spells_known=[_spell("Cure Wounds", 1)],
        )
        data.pop("character_class")
        data["classes"] = [
            {"name": "Cleric", "level": 3, "hit_dice": "1d8", "subclass": None},
            {"name": "Fighter", "level": 2, "hit_dice": "1d10", "subclass": None},
        ]
        char = Character.model_validate(data)
        assert char.spell_slots == {1: 4, 2: 2}
