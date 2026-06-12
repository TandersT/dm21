"""Tests for CharacterBuilder (Issue #100).

Tests cover:
- Ability score methods (standard_array, point_buy, manual)
- Racial bonus application
- HP calculation
- Feature collection from class/race/background
- Spell slot calculation
- Error handling (missing rulebook, invalid input)
- Integration with SRD data
"""

import pytest
from unittest.mock import MagicMock

from dm20_protocol.character_builder import (
    CharacterBuilder,
    CharacterBuilderError,
    STANDARD_ARRAY,
    POINT_BUY_COSTS,
    POINT_BUY_BUDGET,
    _normalize_index,
)
from dm20_protocol.models import AbilityScore, Feature
from dm20_protocol.rulebooks.models import (
    AbilityBonus,
    BackgroundDefinition,
    BackgroundFeature,
    ClassDefinition,
    ClassLevelInfo,
    RaceDefinition,
    RacialTrait,
    SpellcastingInfo,
)


# ---------------------------------------------------------------------------
# Fixtures — mock rulebook definitions
# ---------------------------------------------------------------------------

def make_fighter_def(**overrides) -> ClassDefinition:
    defaults = dict(
        index="fighter",
        name="Fighter",
        source="srd-2014",
        hit_die=10,
        proficiencies=["All armor", "Shields", "Simple weapons", "Martial weapons"],
        proficiency_choices={
            "desc": "Choose two skills",
            "choose": 2,
            "type": "proficiencies",
            "from": {
                "option_set_type": "options_array",
                "options": [
                    {"option_type": "reference", "item": {"index": "skill-athletics", "name": "Skill: Athletics"}},
                    {"option_type": "reference", "item": {"index": "skill-intimidation", "name": "Skill: Intimidation"}},
                    {"option_type": "reference", "item": {"index": "skill-perception", "name": "Skill: Perception"}},
                ],
            },
        },
        saving_throws=["STR", "CON"],
        starting_equipment=["Chain mail", "Shield"],
        starting_equipment_options=[],
        spellcasting=None,
        class_levels={
            1: ClassLevelInfo(level=1, proficiency_bonus=2, features=["Fighting Style", "Second Wind"]),
            2: ClassLevelInfo(level=2, proficiency_bonus=2, features=["Action Surge"]),
            3: ClassLevelInfo(level=3, proficiency_bonus=2, features=["Martial Archetype"]),
            5: ClassLevelInfo(level=5, proficiency_bonus=3, features=["Extra Attack"]),
        },
        subclasses=["champion"],
        subclass_level=3,
    )
    defaults.update(overrides)
    return ClassDefinition(**defaults)


def make_wizard_def(**overrides) -> ClassDefinition:
    defaults = dict(
        index="wizard",
        name="Wizard",
        source="srd-2014",
        hit_die=6,
        proficiencies=["Daggers", "Darts", "Slings", "Quarterstaffs", "Light crossbows"],
        proficiency_choices={
            "choose": 2,
            "from": {
                "option_set_type": "options_array",
                "options": [
                    {"option_type": "reference", "item": {"index": "skill-arcana", "name": "Skill: Arcana"}},
                    {"option_type": "reference", "item": {"index": "skill-history", "name": "Skill: History"}},
                    {"option_type": "reference", "item": {"index": "skill-investigation", "name": "Skill: Investigation"}},
                ],
            },
        },
        saving_throws=["INT", "WIS"],
        starting_equipment=["Spellbook", "Component pouch"],
        starting_equipment_options=[],
        spellcasting=SpellcastingInfo(
            level=1,
            spellcasting_ability="INT",
            caster_type="full",
            cantrips_known=[3, 3, 3, 4, 4, 4, 4, 4, 4, 5],
            spells_known=None,
            spell_slots={
                1: [2, 0, 0, 0, 0, 0, 0, 0, 0],
                2: [3, 0, 0, 0, 0, 0, 0, 0, 0],
                3: [4, 2, 0, 0, 0, 0, 0, 0, 0],
                5: [4, 3, 2, 0, 0, 0, 0, 0, 0],
            },
        ),
        class_levels={
            1: ClassLevelInfo(level=1, proficiency_bonus=2, features=["Spellcasting", "Arcane Recovery"]),
            2: ClassLevelInfo(level=2, proficiency_bonus=2, features=["Arcane Tradition"]),
        },
        subclasses=["evocation"],
        subclass_level=2,
    )
    defaults.update(overrides)
    return ClassDefinition(**defaults)


def make_human_def(**overrides) -> RaceDefinition:
    defaults = dict(
        index="human",
        name="Human",
        source="srd-2014",
        speed=30,
        ability_bonuses=[
            AbilityBonus(ability_score="STR", bonus=1),
            AbilityBonus(ability_score="DEX", bonus=1),
            AbilityBonus(ability_score="CON", bonus=1),
            AbilityBonus(ability_score="INT", bonus=1),
            AbilityBonus(ability_score="WIS", bonus=1),
            AbilityBonus(ability_score="CHA", bonus=1),
        ],
        languages=["Common"],
        traits=[],
    )
    defaults.update(overrides)
    return RaceDefinition(**defaults)


def make_elf_def(**overrides) -> RaceDefinition:
    defaults = dict(
        index="elf",
        name="Elf",
        source="srd-2014",
        speed=30,
        ability_bonuses=[AbilityBonus(ability_score="DEX", bonus=2)],
        languages=["Common", "Elvish"],
        traits=[
            RacialTrait(index="darkvision", name="Darkvision", desc=["You can see in dim light within 60 feet."]),
            RacialTrait(index="fey-ancestry", name="Fey Ancestry", desc=["You have advantage on saves against being charmed."]),
            RacialTrait(index="trance", name="Trance", desc=["Elves don't need to sleep."]),
        ],
    )
    defaults.update(overrides)
    return RaceDefinition(**defaults)


def make_acolyte_def(**overrides) -> BackgroundDefinition:
    defaults = dict(
        index="acolyte",
        name="Acolyte",
        source="srd-2014",
        starting_proficiencies=["Skill: Insight", "Skill: Religion"],
        starting_equipment=["Holy symbol", "Prayer book", "5 sticks of incense"],
        starting_equipment_options=[],
        feature=BackgroundFeature(name="Shelter of the Faithful", desc=["You can find shelter at a temple."]),
    )
    defaults.update(overrides)
    return BackgroundDefinition(**defaults)


def make_mock_manager(
    class_def=None,
    race_def=None,
    bg_def=None,
) -> MagicMock:
    """Create a mock RulebookManager with configurable return values."""
    manager = MagicMock()
    manager.get_class.return_value = class_def
    manager.get_race.return_value = race_def
    manager.get_background.return_value = bg_def
    return manager


# ---------------------------------------------------------------------------
# Normalize Index
# ---------------------------------------------------------------------------

class TestNormalizeIndex:
    def test_lowercase(self):
        assert _normalize_index("Fighter") == "fighter"

    def test_spaces_to_hyphens(self):
        assert _normalize_index("Wood Elf") == "wood-elf"

    def test_underscores_to_hyphens(self):
        assert _normalize_index("Half_Orc") == "half-orc"

    def test_strip_whitespace(self):
        assert _normalize_index("  Dwarf  ") == "dwarf"

    def test_already_normalized(self):
        assert _normalize_index("hill-dwarf") == "hill-dwarf"


# ---------------------------------------------------------------------------
# Ability Score Methods
# ---------------------------------------------------------------------------

class TestAbilityScoreMethods:
    def setup_method(self):
        self.manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=make_human_def(),
        )
        self.builder = CharacterBuilder(self.manager)

    def test_manual_mode_default(self):
        char = self.builder.build("Test", "Fighter", "Human", 1)
        # Default manual scores = 10 + 1 (human racial) = 11 each
        for ability in char.abilities.values():
            assert ability.score == 11

    def test_manual_mode_custom_scores(self):
        char = self.builder.build(
            "Test", "Fighter", "Human", 1,
            strength=16, dexterity=14, constitution=14,
            intelligence=10, wisdom=12, charisma=8,
        )
        # Scores + 1 (human racial bonus)
        assert char.abilities["strength"].score == 17
        assert char.abilities["dexterity"].score == 15
        assert char.abilities["charisma"].score == 9

    def test_standard_array(self):
        assignments = {
            "strength": 15, "dexterity": 14, "constitution": 13,
            "intelligence": 12, "wisdom": 10, "charisma": 8,
        }
        char = self.builder.build(
            "Test", "Fighter", "Human", 1,
            ability_method="standard_array",
            ability_assignments=assignments,
        )
        # 15 + 1 (human) = 16 STR
        assert char.abilities["strength"].score == 16
        assert char.abilities["charisma"].score == 9  # 8 + 1

    def test_standard_array_wrong_values(self):
        assignments = {
            "strength": 15, "dexterity": 15, "constitution": 13,
            "intelligence": 12, "wisdom": 10, "charisma": 8,
        }
        with pytest.raises(CharacterBuilderError, match="Standard Array"):
            self.builder.build(
                "Test", "Fighter", "Human", 1,
                ability_method="standard_array",
                ability_assignments=assignments,
            )

    def test_standard_array_missing_ability(self):
        assignments = {
            "strength": 15, "dexterity": 14, "constitution": 13,
            "intelligence": 12, "wisdom": 10,
            # Missing charisma — triggers value count mismatch first
        }
        with pytest.raises(CharacterBuilderError, match="Standard Array"):
            self.builder.build(
                "Test", "Fighter", "Human", 1,
                ability_method="standard_array",
                ability_assignments=assignments,
            )

    def test_standard_array_no_assignments(self):
        with pytest.raises(CharacterBuilderError, match="requires ability_assignments"):
            self.builder.build(
                "Test", "Fighter", "Human", 1,
                ability_method="standard_array",
            )

    def test_point_buy_valid(self):
        # 15 + 15 + 15 + 8 + 8 + 8 = 9+9+9+0+0+0 = 27 ✓
        assignments = {
            "strength": 15, "dexterity": 15, "constitution": 15,
            "intelligence": 8, "wisdom": 8, "charisma": 8,
        }
        char = self.builder.build(
            "Test", "Fighter", "Human", 1,
            ability_method="point_buy",
            ability_assignments=assignments,
        )
        assert char.abilities["strength"].score == 16  # 15 + 1 human

    def test_point_buy_over_budget(self):
        assignments = {
            "strength": 15, "dexterity": 15, "constitution": 15,
            "intelligence": 15, "wisdom": 8, "charisma": 8,
        }  # 9+9+9+9+0+0 = 36 > 27
        with pytest.raises(CharacterBuilderError, match="budget exceeded"):
            self.builder.build(
                "Test", "Fighter", "Human", 1,
                ability_method="point_buy",
                ability_assignments=assignments,
            )

    def test_point_buy_under_budget(self):
        assignments = {
            "strength": 8, "dexterity": 8, "constitution": 8,
            "intelligence": 8, "wisdom": 8, "charisma": 8,
        }  # All 0 cost = 0/27
        with pytest.raises(CharacterBuilderError, match="unspent"):
            self.builder.build(
                "Test", "Fighter", "Human", 1,
                ability_method="point_buy",
                ability_assignments=assignments,
            )

    def test_point_buy_score_out_of_range(self):
        assignments = {
            "strength": 16, "dexterity": 14, "constitution": 14,
            "intelligence": 10, "wisdom": 10, "charisma": 8,
        }
        with pytest.raises(CharacterBuilderError, match="8-15"):
            self.builder.build(
                "Test", "Fighter", "Human", 1,
                ability_method="point_buy",
                ability_assignments=assignments,
            )

    def test_unknown_method(self):
        with pytest.raises(CharacterBuilderError, match="Unknown ability method"):
            self.builder.build(
                "Test", "Fighter", "Human", 1,
                ability_method="dice_roll",
            )


# ---------------------------------------------------------------------------
# Racial Bonuses
# ---------------------------------------------------------------------------

class TestRacialBonuses:
    def test_elf_dex_bonus(self):
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=make_elf_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Fighter", "Elf", 1, dexterity=14)
        assert char.abilities["dexterity"].score == 16  # 14 + 2 elf

    def test_human_all_bonuses(self):
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=make_human_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Fighter", "Human", 1, strength=15)
        assert char.abilities["strength"].score == 16  # 15 + 1 human

    def test_bonus_capped_at_30(self):
        race_def = make_elf_def(
            ability_bonuses=[AbilityBonus(ability_score="DEX", bonus=2)]
        )
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=race_def,
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Fighter", "Elf", 1, dexterity=29)
        assert char.abilities["dexterity"].score == 30  # Capped


# ---------------------------------------------------------------------------
# HP Calculation
# ---------------------------------------------------------------------------

class TestHPCalculation:
    def test_level_1_fighter(self):
        # d10 + CON mod(14) = 10 + 2 = 12
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=make_human_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Fighter", "Human", 1, constitution=14)
        # CON 14 + 1 human = 15, mod = +2
        assert char.hit_points_max == 12  # 10 + 2

    def test_level_1_wizard(self):
        # d6 + CON mod(10) = 6 + 0 = 6
        manager = make_mock_manager(
            class_def=make_wizard_def(),
            race_def=make_human_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Wizard", "Human", 1, constitution=10)
        # CON 10 + 1 human = 11, mod = +0
        assert char.hit_points_max == 6  # 6 + 0

    def test_level_5_fighter(self):
        # Level 1: 10 + 2 = 12
        # Levels 2-5: (5+1+2) * 4 = 32
        # Total: 44
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=make_human_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Fighter", "Human", 5, constitution=14)
        # CON 14 + 1 = 15, mod = +2
        # Level 1: 10 + 2 = 12
        # Levels 2-5: (6 + 2) * 4 = 32
        assert char.hit_points_max == 44

    def test_negative_con_mod_minimum_1(self):
        # CON 6 → mod = -2. Level 1: max(d10 + (-2), 1) = 8
        race_def = RaceDefinition(
            index="human", name="Human", source="srd",
            speed=30, ability_bonuses=[], languages=["Common"], traits=[],
        )
        manager = make_mock_manager(
            class_def=make_wizard_def(),  # d6
            race_def=race_def,
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Wizard", "Human", 1, constitution=6)
        # CON 6, mod = -2. HP = max(6 + (-2), 1) = 4
        assert char.hit_points_max == 4


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

class TestFeatures:
    def test_class_features_level_1(self):
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=make_human_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Fighter", "Human", 1)
        feature_names = [f.name for f in char.features]
        assert "Fighting Style" in feature_names
        assert "Second Wind" in feature_names

    def test_class_features_level_5(self):
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=make_human_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Fighter", "Human", 5)
        feature_names = [f.name for f in char.features]
        assert "Extra Attack" in feature_names
        assert "Action Surge" in feature_names

    def test_racial_traits_as_features(self):
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=make_elf_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Fighter", "Elf", 1)
        feature_names = [f.name for f in char.features]
        assert "Darkvision" in feature_names
        assert "Fey Ancestry" in feature_names
        assert "Trance" in feature_names

    def test_background_feature(self):
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=make_human_def(),
            bg_def=make_acolyte_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Fighter", "Human", 1, background="Acolyte")
        feature_names = [f.name for f in char.features]
        assert "Shelter of the Faithful" in feature_names

    def test_features_and_traits_synced(self):
        """Legacy features_and_traits should mirror structured features."""
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=make_elf_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Fighter", "Elf", 1)
        assert len(char.features_and_traits) == len(char.features)

    def test_feature_source_correct(self):
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=make_elf_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Fighter", "Elf", 1)
        sources = {f.name: f.source for f in char.features}
        assert sources["Darkvision"] == "Elf"
        assert sources["Fighting Style"] == "Fighter 1"


# ---------------------------------------------------------------------------
# Proficiencies & Languages
# ---------------------------------------------------------------------------

class TestProficiencies:
    def test_saving_throws(self):
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=make_human_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Fighter", "Human", 1)
        assert "STR" in char.saving_throw_proficiencies
        assert "CON" in char.saving_throw_proficiencies

    def test_skill_proficiencies_from_background(self):
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=make_human_def(),
            bg_def=make_acolyte_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Fighter", "Human", 1, background="Acolyte")
        assert "Insight" in char.skill_proficiencies
        assert "Religion" in char.skill_proficiencies

    def test_languages_from_race(self):
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=make_elf_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Fighter", "Elf", 1)
        assert "Common" in char.languages
        assert "Elvish" in char.languages


# ---------------------------------------------------------------------------
# Spell Slots
# ---------------------------------------------------------------------------

class TestSpellSlots:
    def test_wizard_level_1_slots(self):
        manager = make_mock_manager(
            class_def=make_wizard_def(),
            race_def=make_human_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Wizard", "Human", 1)
        assert char.spell_slots == {1: 2}
        assert char.spellcasting_ability == "intelligence"

    def test_wizard_level_3_slots(self):
        manager = make_mock_manager(
            class_def=make_wizard_def(),
            race_def=make_human_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Wizard", "Human", 3)
        assert char.spell_slots == {1: 4, 2: 2}

    def test_fighter_no_spells(self):
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=make_human_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Fighter", "Human", 1)
        assert char.spell_slots == {}
        assert char.spellcasting_ability is None

    def test_caster_without_slot_table_falls_back_to_srd(self):
        """A rulebook caster class lacking slot data must not yield empty slots."""
        wizard = make_wizard_def(
            spellcasting=SpellcastingInfo(
                level=1,
                spellcasting_ability="INT",
                caster_type="full",
                spell_slots=None,
            ),
        )
        manager = make_mock_manager(class_def=wizard, race_def=make_human_def())
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Wizard", "Human", 5)
        assert char.spell_slots == {1: 4, 2: 3, 3: 2}

    def test_caster_missing_level_entry_falls_back_to_srd(self):
        """The default wizard def has no level-4 slot entry; SRD fills the gap."""
        manager = make_mock_manager(
            class_def=make_wizard_def(),
            race_def=make_human_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Wizard", "Human", 4)
        assert char.spell_slots == {1: 4, 2: 3}

    def test_unknown_caster_class_uses_rulebook_caster_type(self):
        """Homebrew class name unknown to the SRD table: caster_type decides."""
        homebrew = make_wizard_def(
            index="homebrewmancer",
            name="Homebrewmancer",
            spellcasting=SpellcastingInfo(
                level=1,
                spellcasting_ability="CHA",
                caster_type="half",
                spell_slots=None,
            ),
        )
        manager = make_mock_manager(class_def=homebrew, race_def=make_human_def())
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Homebrewmancer", "Human", 6)
        assert char.spell_slots == {1: 4, 2: 2}

    def test_known_class_name_wins_over_caster_type(self):
        """Paladin level 1 has no slots even if caster_type defaulted to full."""
        paladin = make_wizard_def(
            index="paladin",
            name="Paladin",
            spellcasting=SpellcastingInfo(
                level=2,
                spellcasting_ability="CHA",
                # caster_type defaults to "full" when a rulebook source omits it
                spell_slots=None,
            ),
        )
        manager = make_mock_manager(class_def=paladin, race_def=make_human_def())
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Paladin", "Human", 1)
        assert char.spell_slots == {}


# ---------------------------------------------------------------------------
# Equipment
# ---------------------------------------------------------------------------

class TestEquipment:
    def test_class_starting_equipment(self):
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=make_human_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Fighter", "Human", 1)
        item_names = [i.name for i in char.inventory]
        assert "Chain mail" in item_names
        assert "Shield" in item_names

    def test_background_equipment(self):
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=make_human_def(),
            bg_def=make_acolyte_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Fighter", "Human", 1, background="Acolyte")
        item_names = [i.name for i in char.inventory]
        assert "Holy symbol" in item_names
        assert "Prayer book" in item_names


# ---------------------------------------------------------------------------
# Character Properties
# ---------------------------------------------------------------------------

class TestCharacterProperties:
    def test_speed_from_race(self):
        elf_def = make_elf_def(speed=35)  # Wood Elf speed
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=elf_def,
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Fighter", "Elf", 1)
        assert char.speed == 35

    def test_hit_dice_type(self):
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=make_human_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Fighter", "Human", 1)
        assert char.hit_dice_type == "d10"

    def test_hit_dice_remaining(self):
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=make_human_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Fighter", "Human", 5)
        assert char.hit_dice_remaining == "5d10"

    def test_proficiency_bonus_auto_calculated(self):
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=make_human_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Fighter", "Human", 5)
        assert char.proficiency_bonus == 3  # Level 5 → +3

    def test_race_traits_on_race_model(self):
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=make_elf_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Fighter", "Elf", 1)
        assert "Darkvision" in char.race.traits

    def test_class_hit_dice_string(self):
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=make_human_def(),
        )
        builder = CharacterBuilder(manager)
        char = builder.build("Test", "Fighter", "Human", 3)
        assert char.character_class.hit_dice == "3d10"


# ---------------------------------------------------------------------------
# Error Handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_class_not_found(self):
        manager = make_mock_manager(class_def=None, race_def=make_human_def())
        builder = CharacterBuilder(manager)
        with pytest.raises(CharacterBuilderError, match="Class.*not found"):
            builder.build("Test", "Bard", "Human", 1)

    def test_race_not_found(self):
        manager = make_mock_manager(class_def=make_fighter_def(), race_def=None)
        builder = CharacterBuilder(manager)
        with pytest.raises(CharacterBuilderError, match="Race.*not found"):
            builder.build("Test", "Fighter", "Tiefling", 1)

    def test_background_not_found_is_non_fatal(self):
        """Missing background should not block character creation."""
        manager = make_mock_manager(
            class_def=make_fighter_def(),
            race_def=make_human_def(),
            bg_def=None,
        )
        builder = CharacterBuilder(manager)
        # Should succeed — background is optional
        char = builder.build("Test", "Fighter", "Human", 1, background="Unknown")
        assert char.name == "Test"
