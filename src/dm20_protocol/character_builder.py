"""Character Builder — auto-populate characters from rulebook data.

Given a class, race, background, and level, the builder reads from the
RulebookManager to populate a complete Character with saving throws,
proficiencies, starting equipment, features, HP, spell slots, and more.
"""

from __future__ import annotations

import json
from typing import Any

from .models import (
    AbilityScore,
    Character,
    CharacterClass,
    Feature,
    Item,
    Race,
    Spell,
)
from .rulebooks.manager import RulebookManager
from .srd_spell_slots import caster_type_for_class, slots_for_caster_type
from .rulebooks.models import (
    BackgroundDefinition,
    ClassDefinition,
    RaceDefinition,
)


# Standard Array values per PHB
STANDARD_ARRAY = [15, 14, 13, 12, 10, 8]

# Point Buy costs per PHB
POINT_BUY_COSTS = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
POINT_BUY_BUDGET = 27

# Ability score abbreviation → full name mapping
ABILITY_ABBREV = {
    "STR": "strength",
    "DEX": "dexterity",
    "CON": "constitution",
    "INT": "intelligence",
    "WIS": "wisdom",
    "CHA": "charisma",
}

ALL_ABILITIES = list(ABILITY_ABBREV.values())


def _normalize_index(name: str) -> str:
    """Convert user-facing name to rulebook index format (lowercase, hyphenated)."""
    return name.strip().lower().replace(" ", "-").replace("_", "-")


def _infer_item_type(name: str) -> str:
    """Infer item type from equipment name using keyword matching."""
    lower = name.lower()

    # Weapons
    weapon_keywords = {
        "sword", "axe", "mace", "dagger", "bow", "crossbow", "spear",
        "halberd", "pike", "rapier", "scimitar", "warhammer", "maul",
        "glaive", "javelin", "handaxe", "trident", "whip", "club",
        "greatclub", "sling", "dart", "quarterstaff", "lance",
        "morningstar", "flail", "greataxe", "greatsword", "longsword",
        "shortsword", "longbow", "shortbow",
    }
    if any(kw in lower for kw in weapon_keywords):
        return "weapon"

    # Armor
    armor_keywords = {
        "armor", "mail", "plate", "leather", "hide", "breastplate",
        "half plate", "studded", "padded", "scale",
    }
    if any(kw in lower for kw in armor_keywords):
        return "armor"

    # Shield
    if "shield" in lower:
        return "shield"

    # Tools
    tool_keywords = {
        "tools", "kit", "supplies", "instrument", "set",
        "thieves", "herbalism", "poisoner", "tinker",
    }
    if any(kw in lower for kw in tool_keywords):
        return "tool"

    # Packs
    if "pack" in lower:
        return "gear"

    # Focus / holy symbol / arcane focus
    focus_keywords = {"focus", "holy symbol", "totem", "component pouch", "spellbook"}
    if any(kw in lower for kw in focus_keywords):
        return "focus"

    return "misc"


class CharacterBuilderError(Exception):
    """Raised when the builder cannot create a character."""


class CharacterBuilder:
    """Build a fully populated Character from rulebook definitions."""

    def __init__(self, rulebook_manager: RulebookManager) -> None:
        self.rm = rulebook_manager

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        name: str,
        class_name: str,
        race_name: str,
        level: int,
        *,
        background: str | None = None,
        subclass: str | None = None,
        subrace: str | None = None,
        ability_method: str = "manual",
        ability_assignments: dict[str, int] | None = None,
        player_name: str | None = None,
        alignment: str | None = None,
        description: str | None = None,
        bio: str | None = None,
        # Raw ability scores for manual mode (current behavior)
        strength: int = 10,
        dexterity: int = 10,
        constitution: int = 10,
        intelligence: int = 10,
        wisdom: int = 10,
        charisma: int = 10,
    ) -> Character:
        """Build a fully populated Character from rulebook data.

        Args:
            name: Character name.
            class_name: Class name (e.g., "Fighter", "Wizard").
            race_name: Race name (e.g., "Human", "Wood Elf").
            level: Character level (1-20).
            background: Background name (e.g., "Acolyte", "Outlander").
            subclass: Subclass name (required if level >= subclass_level).
            subrace: Subrace name (e.g., "Hill Dwarf").
            ability_method: "manual", "standard_array", or "point_buy".
            ability_assignments: For standard_array/point_buy: {"strength": 15, ...}.
            player_name: Player name.
            alignment: Character alignment.
            description: Brief appearance description.
            bio: Character backstory.
            strength..charisma: Raw scores for manual mode.

        Returns:
            A fully populated Character object.

        Raises:
            CharacterBuilderError: If rulebook data is missing or input is invalid.
        """
        # 1. Look up definitions
        class_def = self._get_class(class_name)
        race_def = self._get_race(race_name)
        bg_def = self._get_background(background) if background else None

        # 2. Generate ability scores
        abilities = self._resolve_abilities(
            ability_method,
            ability_assignments,
            strength=strength,
            dexterity=dexterity,
            constitution=constitution,
            intelligence=intelligence,
            wisdom=wisdom,
            charisma=charisma,
        )

        # 3. Apply racial ability bonuses
        abilities = self._apply_racial_bonuses(abilities, race_def)

        # 4. Collect proficiencies, features, equipment, languages
        saving_throws = self._get_saving_throws(class_def)
        skill_profs = self._get_skill_proficiencies(class_def, bg_def)
        tool_profs = self._get_tool_proficiencies(class_def, bg_def)
        languages = self._get_languages(race_def, bg_def)
        features = self._get_features(class_def, race_def, bg_def, level)
        equipment = self._get_starting_equipment(class_def, bg_def)

        # 5. Calculate HP
        con_mod = abilities["constitution"].mod
        hp = self._calculate_hp(class_def.hit_die, level, con_mod)

        # 6. Spellcasting
        spellcasting_ability = None
        spell_slots: dict[int, int] = {}
        if class_def.spellcasting:
            spellcasting_ability = ABILITY_ABBREV.get(
                class_def.spellcasting.spellcasting_ability,
                class_def.spellcasting.spellcasting_ability.lower(),
            )
            spell_slots = self._get_spell_slots(class_def, level)
            if not spell_slots:
                # Rulebook class def lacks slot data for this level; fall back
                # to the SRD progression (class name wins over caster_type).
                caster_type = (
                    caster_type_for_class(class_def.name)
                    or class_def.spellcasting.caster_type
                )
                spell_slots = slots_for_caster_type(caster_type, level)

        # 6b. Starting spells
        starting_spells = self._get_starting_spells(class_def, level)

        # 7. Hit dice
        hit_dice_type = f"d{class_def.hit_die}"

        # 8. Race traits for Race model
        race_traits = [t.name for t in race_def.traits]

        # 9. Build Character
        character = Character(
            name=name,
            player_name=player_name,
            classes=[CharacterClass(
                name=class_def.name,
                level=level,
                hit_dice=f"{level}{hit_dice_type}",
                subclass=subclass,
            )],
            race=Race(
                name=race_def.name,
                subrace=subrace,
                traits=race_traits,
            ),
            background=background,
            alignment=alignment,
            description=description,
            bio=bio,
            abilities=abilities,
            speed=race_def.speed,
            hit_points_max=hp,
            hit_points_current=hp,
            hit_dice_type=hit_dice_type,
            hit_dice_remaining=f"{level}{hit_dice_type}",
            saving_throw_proficiencies=saving_throws,
            skill_proficiencies=skill_profs,
            tool_proficiencies=tool_profs,
            languages=languages,
            features=features,
            features_and_traits=[f.name for f in features],
            inventory=equipment,
            spellcasting_ability=spellcasting_ability,
            spell_slots=spell_slots,
            spells_known=starting_spells,
            experience_points=0,
        )

        return character

    def add_classes(
        self,
        character: Character,
        extra_classes: list[dict],
    ) -> None:
        """Add additional classes to an already-built character (multiclass at creation).

        For each extra class, adds the CharacterClass, level features, and HP.
        Does NOT duplicate starting equipment or saving throw proficiencies
        (those come from the primary class only per D&D 5e multiclass rules).

        Args:
            character: Character to modify (already built with primary class).
            extra_classes: List of dicts with keys: name, level, subclass (optional).

        Raises:
            CharacterBuilderError: If a class is not found or total level exceeds 20.
        """
        for cls_spec in extra_classes:
            cls_name = cls_spec.get("name")
            cls_level = cls_spec.get("level", 1)
            cls_subclass = cls_spec.get("subclass")

            if not cls_name:
                raise CharacterBuilderError("Each additional class must have a 'name'.")

            if character.total_level + cls_level > 20:
                raise CharacterBuilderError(
                    f"Total level would exceed 20: current {character.total_level} "
                    f"+ {cls_name} {cls_level} = {character.total_level + cls_level}"
                )

            class_def = self._get_class(cls_name)
            hit_dice_type = f"d{class_def.hit_die}"

            # Add the class
            character.classes.append(CharacterClass(
                name=class_def.name,
                level=cls_level,
                hit_dice=f"{cls_level}{hit_dice_type}",
                subclass=cls_subclass,
            ))

            # Add HP for secondary class levels (level 1 uses average, not max die)
            con_mod = character.abilities["constitution"].mod
            avg_roll = class_def.hit_die // 2 + 1
            for _ in range(cls_level):
                hp_gain = max(avg_roll + con_mod, 1)
                character.hit_points_max += hp_gain
                character.hit_points_current += hp_gain

            # Add class features for all levels of this class
            for lvl in range(1, cls_level + 1):
                level_info = class_def.class_levels.get(lvl)
                if level_info:
                    for feat_name in level_info.features:
                        desc = level_info.feature_details.get(feat_name, "")
                        character.features.append(Feature(
                            name=feat_name,
                            source=f"{class_def.name} {lvl}",
                            description=desc,
                            level_gained=lvl,
                        ))
                        if feat_name not in character.features_and_traits:
                            character.features_and_traits.append(feat_name)

        # Recalculate proficiency bonus based on new total level
        character.proficiency_bonus = 2 + (character.total_level - 1) // 4

    # ------------------------------------------------------------------
    # Ability Score Methods
    # ------------------------------------------------------------------

    def _resolve_abilities(
        self,
        method: str,
        assignments: dict[str, int] | None,
        **manual_scores: int,
    ) -> dict[str, AbilityScore]:
        """Generate ability scores using the chosen method."""
        if method == "manual":
            return {
                name: AbilityScore(score=manual_scores.get(name, 10))
                for name in ALL_ABILITIES
            }
        elif method == "standard_array":
            return self._standard_array(assignments)
        elif method == "point_buy":
            return self._point_buy(assignments)
        else:
            raise CharacterBuilderError(
                f"Unknown ability method: '{method}'. "
                "Use 'manual', 'standard_array', or 'point_buy'."
            )

    def _standard_array(
        self, assignments: dict[str, int] | None
    ) -> dict[str, AbilityScore]:
        """Assign Standard Array values [15, 14, 13, 12, 10, 8] to abilities."""
        if not assignments:
            raise CharacterBuilderError(
                "standard_array requires ability_assignments: "
                '{"strength": 15, "dexterity": 14, ...}'
            )
        assigned_values = sorted(assignments.values(), reverse=True)
        if assigned_values != sorted(STANDARD_ARRAY, reverse=True):
            raise CharacterBuilderError(
                f"Standard Array values must be exactly {STANDARD_ARRAY} "
                f"(got {list(assignments.values())})"
            )
        if set(assignments.keys()) != set(ALL_ABILITIES):
            missing = set(ALL_ABILITIES) - set(assignments.keys())
            raise CharacterBuilderError(
                f"Must assign all 6 abilities. Missing: {missing}"
            )
        return {
            name: AbilityScore(score=assignments[name]) for name in ALL_ABILITIES
        }

    def _point_buy(
        self, assignments: dict[str, int] | None
    ) -> dict[str, AbilityScore]:
        """Validate and apply Point Buy scores (27 points, PHB costs)."""
        if not assignments:
            raise CharacterBuilderError(
                "point_buy requires ability_assignments: "
                '{"strength": 15, "dexterity": 13, ...}'
            )
        if set(assignments.keys()) != set(ALL_ABILITIES):
            missing = set(ALL_ABILITIES) - set(assignments.keys())
            raise CharacterBuilderError(
                f"Must assign all 6 abilities. Missing: {missing}"
            )
        total_cost = 0
        for ability, score in assignments.items():
            if score < 8 or score > 15:
                raise CharacterBuilderError(
                    f"Point Buy scores must be 8-15 (got {ability}={score})"
                )
            total_cost += POINT_BUY_COSTS[score]

        if total_cost > POINT_BUY_BUDGET:
            raise CharacterBuilderError(
                f"Point Buy budget exceeded: {total_cost}/{POINT_BUY_BUDGET} points"
            )
        if total_cost < POINT_BUY_BUDGET:
            remaining = POINT_BUY_BUDGET - total_cost
            raise CharacterBuilderError(
                f"Point Buy has {remaining} unspent points ({total_cost}/{POINT_BUY_BUDGET})"
            )

        return {
            name: AbilityScore(score=assignments[name]) for name in ALL_ABILITIES
        }

    # ------------------------------------------------------------------
    # Racial Bonuses
    # ------------------------------------------------------------------

    def _apply_racial_bonuses(
        self,
        abilities: dict[str, AbilityScore],
        race_def: RaceDefinition,
    ) -> dict[str, AbilityScore]:
        """Apply racial ability score bonuses."""
        for bonus in race_def.ability_bonuses:
            ability_name = ABILITY_ABBREV.get(
                bonus.ability_score, bonus.ability_score.lower()
            )
            if ability_name in abilities:
                new_score = min(abilities[ability_name].score + bonus.bonus, 30)
                abilities[ability_name] = AbilityScore(score=new_score)
        return abilities

    # ------------------------------------------------------------------
    # Proficiencies
    # ------------------------------------------------------------------

    def _get_saving_throws(self, class_def: ClassDefinition) -> list[str]:
        """Extract saving throw proficiencies from class."""
        return list(class_def.saving_throws)

    def _get_skill_proficiencies(
        self,
        class_def: ClassDefinition,
        bg_def: BackgroundDefinition | None,
    ) -> list[str]:
        """Collect skill proficiencies from class and background."""
        skills: list[str] = []

        # From background (these are usually fixed, e.g., "Skill: Insight")
        if bg_def:
            for prof in bg_def.starting_proficiencies:
                if prof.startswith("Skill: "):
                    skills.append(prof.removeprefix("Skill: "))
                elif "skill" in prof.lower():
                    skills.append(prof)

        # From class proficiency_choices — extract the available options
        # but don't auto-choose (the DM persona handles that)
        if class_def.proficiency_choices:
            choices = class_def.proficiency_choices
            if isinstance(choices, dict) and "from" in choices:
                options = choices["from"]
                if isinstance(options, dict) and "options" in options:
                    available = []
                    for opt in options["options"]:
                        if isinstance(opt, dict) and "item" in opt:
                            item = opt["item"]
                            if isinstance(item, dict):
                                skill_name = item.get("name", "")
                                if skill_name.startswith("Skill: "):
                                    available.append(
                                        skill_name.removeprefix("Skill: ")
                                    )
                    # Auto-pick the first N skills that aren't already from background
                    choose_count = choices.get("choose", 2)
                    for skill in available:
                        if skill not in skills and len(skills) < choose_count + len(
                            [s for s in skills if s not in available]
                        ):
                            skills.append(skill)
                        if len(skills) >= choose_count + len(
                            [s for s in skills if s not in available]
                        ):
                            break

        return skills

    def _get_tool_proficiencies(
        self,
        class_def: ClassDefinition,
        bg_def: BackgroundDefinition | None,
    ) -> list[str]:
        """Collect tool proficiencies from class and background."""
        tools: list[str] = []
        # Class proficiencies that aren't skills, armor, or weapons
        armor_weapon_keywords = {
            "armor",
            "shield",
            "weapon",
            "simple",
            "martial",
            "light",
            "medium",
            "heavy",
        }
        for prof in class_def.proficiencies:
            lower = prof.lower()
            if not any(kw in lower for kw in armor_weapon_keywords) and not lower.startswith("skill"):
                tools.append(prof)

        if bg_def:
            for prof in bg_def.starting_proficiencies:
                if not prof.startswith("Skill: ") and prof not in tools:
                    tools.append(prof)

        return tools

    def _get_languages(
        self,
        race_def: RaceDefinition,
        bg_def: BackgroundDefinition | None,
    ) -> list[str]:
        """Collect languages from race and background."""
        langs = list(race_def.languages)
        # Background language_options are typically "choose N" — skip auto-choice
        return langs

    # ------------------------------------------------------------------
    # Features
    # ------------------------------------------------------------------

    def _get_features(
        self,
        class_def: ClassDefinition,
        race_def: RaceDefinition,
        bg_def: BackgroundDefinition | None,
        level: int,
    ) -> list[Feature]:
        """Collect features from class levels, race traits, and background."""
        features: list[Feature] = []

        # Racial traits
        for trait in race_def.traits:
            desc = " ".join(trait.desc) if trait.desc else ""
            features.append(
                Feature(
                    name=trait.name,
                    source=race_def.name,
                    description=desc,
                    level_gained=1,
                )
            )

        # Background feature
        if bg_def and bg_def.feature:
            desc = " ".join(bg_def.feature.desc) if bg_def.feature.desc else ""
            features.append(
                Feature(
                    name=bg_def.feature.name,
                    source=bg_def.name if bg_def.name else "Background",
                    description=desc,
                    level_gained=1,
                )
            )

        # Class features for each level up to current
        for lvl in range(1, level + 1):
            level_info = class_def.class_levels.get(lvl)
            if level_info:
                for feat_name in level_info.features:
                    desc = level_info.feature_details.get(feat_name, "")
                    features.append(
                        Feature(
                            name=feat_name,
                            source=f"{class_def.name} {lvl}",
                            description=desc,
                            level_gained=lvl,
                        )
                    )

        return features

    # ------------------------------------------------------------------
    # Equipment
    # ------------------------------------------------------------------

    def _get_starting_equipment(
        self,
        class_def: ClassDefinition,
        bg_def: BackgroundDefinition | None,
    ) -> list[Item]:
        """Build starting equipment inventory from class and background.

        Infers item_type from equipment name when possible.
        """
        items: list[Item] = []

        for eq_name in class_def.starting_equipment:
            items.append(Item(name=eq_name, item_type=_infer_item_type(eq_name)))

        if bg_def:
            for eq_name in bg_def.starting_equipment:
                items.append(Item(name=eq_name, item_type=_infer_item_type(eq_name)))

        return items

    # ------------------------------------------------------------------
    # HP Calculation
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_hp(hit_die: int, level: int, con_mod: int) -> int:
        """Calculate max HP: level 1 = max die + CON; levels 2+ = average + CON.

        Uses PHB standard: average = hit_die // 2 + 1.
        Minimum 1 HP per level.
        """
        # Level 1: max hit die + CON modifier
        hp = max(hit_die + con_mod, 1)

        # Levels 2+: average (die/2 + 1) + CON modifier per level
        if level > 1:
            avg_roll = hit_die // 2 + 1
            for _ in range(level - 1):
                hp += max(avg_roll + con_mod, 1)

        return hp

    # ------------------------------------------------------------------
    # Spell Slots
    # ------------------------------------------------------------------

    def _get_spell_slots(
        self, class_def: ClassDefinition, level: int
    ) -> dict[int, int]:
        """Get spell slot maximums for a given class and level.

        Converts SpellcastingInfo.spell_slots[level] (list) to dict[int, int].
        """
        if not class_def.spellcasting or not class_def.spellcasting.spell_slots:
            return {}

        slots_by_level = class_def.spellcasting.spell_slots.get(level)
        if not slots_by_level:
            return {}

        # slots_by_level is a list: [1st_level_slots, 2nd_level_slots, ...]
        result: dict[int, int] = {}
        for spell_level_idx, count in enumerate(slots_by_level):
            spell_level = spell_level_idx + 1  # 1-indexed
            if count > 0:
                result[spell_level] = count

        return result

    # ------------------------------------------------------------------
    # Starting Spells
    # ------------------------------------------------------------------

    def _get_starting_spells(
        self, class_def: ClassDefinition, level: int
    ) -> list[Spell]:
        """Get starting spells for a spellcasting class at the given level.

        Uses class spellcasting info to determine cantrips and spell counts.
        Returns Spell objects with basic info (name, level, school).
        Actual spell selection is deferred to the wizard flow.
        """
        if not class_def.spellcasting:
            return []

        spells: list[Spell] = []
        sc = class_def.spellcasting

        # Cantrips known (from spellcasting info or class_levels)
        cantrips_known = 0
        if sc.cantrips_known and level in sc.cantrips_known:
            cantrips_known = sc.cantrips_known[level]

        # Spells known / prepared count
        spells_known_count = 0
        if sc.spells_known and level in sc.spells_known:
            spells_known_count = sc.spells_known[level]

        # Try to find spells from rulebook data
        class_index = _normalize_index(class_def.name)
        available_spells = (
            self.rm.get_class_spells(class_index)
            if hasattr(self.rm, "get_class_spells")
            else []
        )

        if available_spells:
            # Pick cantrips (level 0)
            cantrip_pool = [s for s in available_spells if s.get("level", -1) == 0]
            for s in cantrip_pool[:cantrips_known]:
                spells.append(Spell(
                    name=s.get("name", "Unknown"),
                    level=0,
                    school=s.get("school", ""),
                    casting_time=s.get("casting_time", "1 action"),
                    duration=s.get("duration", "Instantaneous"),
                    components=s.get("components", []),
                    description=s.get("description", ""),
                    prepared=True,
                ))

            # Pick level 1+ spells up to spells_known_count
            slot_count = len((class_def.spellcasting.spell_slots or {}).get(level, []))
            max_spell_level = max(slot_count, 1)
            spell_pool = [
                s for s in available_spells
                if 0 < s.get("level", 0) <= max_spell_level
            ]
            for s in spell_pool[:spells_known_count]:
                spells.append(Spell(
                    name=s.get("name", "Unknown"),
                    level=s.get("level", 1),
                    school=s.get("school", ""),
                    casting_time=s.get("casting_time", "1 action"),
                    duration=s.get("duration", "Instantaneous"),
                    components=s.get("components", []),
                    description=s.get("description", ""),
                    prepared=True,
                ))

        return spells

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def _get_class(self, name: str) -> ClassDefinition:
        """Look up class definition, raising on not found."""
        index = _normalize_index(name)
        class_def = self.rm.get_class(index)
        if not class_def:
            raise CharacterBuilderError(
                f"Class '{name}' not found in loaded rulebooks. "
                "Make sure a rulebook is loaded: load_rulebook source=\"srd\""
            )
        return class_def

    def _get_race(self, name: str) -> RaceDefinition:
        """Look up race definition, raising on not found."""
        index = _normalize_index(name)
        race_def = self.rm.get_race(index)
        if not race_def:
            raise CharacterBuilderError(
                f"Race '{name}' not found in loaded rulebooks. "
                "Make sure a rulebook is loaded: load_rulebook source=\"srd\""
            )
        return race_def

    def _get_background(self, name: str) -> BackgroundDefinition | None:
        """Look up background definition. Returns None if not found (non-fatal)."""
        index = _normalize_index(name)
        return self.rm.get_background(index)
