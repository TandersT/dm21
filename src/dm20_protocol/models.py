"""
Data models for the D&D MCP Server.
"""

from pathlib import Path
from datetime import datetime
from enum import Enum
from typing import Any, Annotated
from shortuuid import random
from pydantic import BaseModel, Field, model_validator
from logging import Handler

from .logutils import logger
from .srd_spell_slots import slots_for_class


class GameStats(BaseModel):
    """Statistics and metadata about the current campaign, and about the MCP server itself across all campaigns."""
    ctime: datetime = Field(default_factory=datetime.now)
    last_tool_call: datetime | None = None
    tool_calls: int = 0
    errors: int = 0
    campaigns_created: int = 0
    campaigns_loaded: int = 0
    campaign_updates: int = 0
    campaign_deletions: int = 0
    characters_created: int = 0
    character_updates: int = 0
    character_deletions: int = 0
    npcs_created: int = 0
    npc_updates: int = 0
    npc_deletions: int = 0
    locations_created: int = 0
    location_updates: int = 0
    location_deletions: int = 0
    quests_created: int = 0
    quest_updates: int = 0
    quest_deletions: int = 0
    encounters_created: int = 0
    encounters_completed: int = 0
    encounter_updates: int = 0
    encounter_deletions: int = 0
    sessions_created: int = 0
    session_updates: int = 0
    session_deletions: int = 0
    items_given: int = 0
    items_taken: int = 0
    item_updates: int = 0
    item_creations: int = 0
    item_deletions: int = 0
    spells_created: int = 0
    spell_updates: int = 0
    spell_deletions: int = 0
    spells_cast: int = 0
    die_rolls: int = 0
    roll_successes: int = 0
    roll_failures: int = 0
    damage_dealt: int = 0
    damage_taken: int = 0
    death_saves_success: int = 0
    death_saves_failure: int = 0
    ingame_days: int = 0

    def _save_stats(self) -> None:
        # TODO: Implement me
        return None

    def _load_stats(self, stats: dict[str, Any]) -> None:
        # TODO: Implement me
        return None

    def inc(self, field: str, inc: int = 1) -> None:
        """Increment a counter.

        Args:
            field (str): The name of the counter to increment. Can be one of:

            inc (int = 1): The amount to increment the counter by. Defaults to 1.

        **Field** can be any of the following:
            tool_calls
            errors
            campaigns_created
            campaigns_loaded
            campaign_updates
            campaign_deletions
            characters_created
            character_updates
            character_deletions
            npcs_created
            npc_updates
            npc_deletions
            locations_created
            location_updates
            location_deletions
            quests_created
            quest_updates
            quest_deletions
            encounters_created
            encounters_completed
            encounter_updates
            encounter_deletions
            sessions_created
            session_updates
            session_deletions
            items_given
            items_taken
            item_updates
            item_creations
            item_deletions
            spells_created
            spell_updates
            spell_deletions
            spells_cast
            die_rolls
            roll_successes
            roll_failures
            damage_dealt
            damage_taken
            death_saves_success
            death_saves_failure
            ingame_days
        """
        try:
            setattr(self, field, getattr(self, field) + inc)
            self._save_stats()
        except Exception as e:
            logger.error(f"❌ Error incrementing {field} in GameStats: {e}")

class GameStatHandler(Handler):
    """Connects the logging module to the GameStats object."""
    def __init__(self, gamestats: GameStats) -> None:
        super().__init__()
        self.gamestats = gamestats

    def emit(self, record):
        try:
            if "ERROR" in record.levelname:
                self.gamestats.inc("errors")
        except Exception:
            self.handleError(record)

# Load GameStats object and attach logging handler
gamestats = GameStats()
logger.addHandler(GameStatHandler(gamestats))


class AbilityScore(BaseModel):
    """D&D ability score with modifiers."""
    score: int = Field(ge=1, le=30, description="Raw ability score")

    @property
    def mod(self) -> int:
        """Calculate ability modifier."""
        return (self.score - 10) // 2


class CharacterClass(BaseModel):
    """Character class information."""
    name: str
    level: int = Field(ge=1, le=20)
    hit_dice: Annotated[str, "The type of hit dice for this character. E.g.. '1d8'"] = "1d4" # e.g., "1d8"
    subclass: Annotated[str | None, "The character's subclass."] = None


class Race(BaseModel):
    """Character race information."""
    name: str
    subrace: str | None = None
    traits: list[str] = Field(default_factory=list)


class Item(BaseModel):
    """Generic item model."""
    id: str = Field(default_factory=lambda: random(length=8))
    name: str
    description: str | None = None
    quantity: int = 1
    weight: float | None = None
    value: str | None = None  # e.g., "50 gp"
    item_type: str = "misc"  # weapon, armor, consumable, misc, etc.
    properties: dict[str, Any] = Field(default_factory=dict)


class Spell(BaseModel):
    """Spell information."""
    id: str = Field(default_factory=lambda: random(length=8))
    name: str
    level: int = Field(ge=0, le=9)
    school: str
    casting_time: str
    range: int = Field(default=5, description="The range of the spell, in feet")
    duration: str
    components: list[str]  # V, S, M
    description: str
    material_components: str | None = None
    prepared: bool = False


class Feature(BaseModel):
    """Structured class/race/background feature."""
    name: str
    source: str  # e.g., "Ranger 1", "Wood Elf", "Outlander"
    description: str = ""
    level_gained: int = 1


class Modifier(BaseModel):
    """A single stat modifier within an active effect.

    Represents a change to a character's stat, such as +2 to attack rolls
    or setting speed to 0.

    Attributes:
        stat: The stat being modified (e.g., "attack_roll", "armor_class", "strength", "speed").
        operation: How the modifier applies: "add" (additive), "set" (override), "dice" (extra dice roll).
        value: The modifier value. For "add"/"set", an integer. For "dice", a dice notation string like "1d4".
    """
    stat: str = Field(description="The stat being modified (e.g., 'attack_roll', 'armor_class', 'strength')")
    operation: str = Field(
        default="add",
        description="How the modifier applies: 'add' (additive), 'set' (override), 'dice' (extra dice)"
    )
    value: int | str = Field(
        description="Modifier value: int for add/set, dice notation string (e.g., '1d4') for dice operation"
    )


class ActiveEffect(BaseModel):
    """An active effect on a character (buff, debuff, condition, spell effect).

    Represents a temporary or permanent modification to a character's stats,
    capabilities, or state. Effects can grant advantage/disadvantage, modify
    stats, grant immunities, and have tracked durations.

    Attributes:
        id: Unique identifier for this effect instance.
        name: Display name of the effect (e.g., "Bless", "Blinded").
        source: What caused this effect (e.g., "Bless spell", "Poison trap").
        modifiers: List of stat modifiers this effect applies.
        duration_type: How duration is tracked: "rounds", "minutes", "concentration", "permanent".
        duration_remaining: Remaining duration in rounds/minutes. None for concentration/permanent.
        grants_advantage: List of check types this effect grants advantage on.
        grants_disadvantage: List of check types this effect grants disadvantage on.
        immunities: List of damage types or conditions this effect grants immunity to.
        stackable: Whether multiple instances of the same-named effect can coexist.
    """
    id: str = Field(default_factory=lambda: random(length=8))
    name: str
    source: str = ""
    modifiers: list[Modifier] = Field(default_factory=list)
    duration_type: str = Field(
        default="permanent",
        description="Duration tracking: 'rounds', 'minutes', 'concentration', 'permanent'"
    )
    duration_remaining: int | None = Field(
        default=None,
        description="Remaining duration in rounds/minutes. None for concentration/permanent."
    )
    grants_advantage: list[str] = Field(
        default_factory=list,
        description="Check types this effect grants advantage on (e.g., ['attack_roll', 'dexterity_save'])"
    )
    grants_disadvantage: list[str] = Field(
        default_factory=list,
        description="Check types this effect grants disadvantage on (e.g., ['attack_roll', 'ability_check'])"
    )
    immunities: list[str] = Field(
        default_factory=list,
        description="Damage types or conditions this grants immunity to"
    )
    stackable: bool = Field(
        default=False,
        description="Whether multiple instances of same-named effect can coexist"
    )


class ConcentrationState(BaseModel):
    """Tracks a character's active concentration on a spell.

    When a character is concentrating on a spell, this model records the spell
    name, the IDs of any ActiveEffects that should be removed when concentration
    breaks, and the round when concentration started.

    Attributes:
        spell_name: The name of the spell being concentrated on.
        effect_ids: IDs of ActiveEffects to remove when concentration breaks.
        started_round: The combat round when concentration began (0 if out of combat).
    """
    spell_name: str
    effect_ids: list[str] = Field(default_factory=list)
    started_round: int = 0


class Character(BaseModel):
    """Complete character sheet."""
    # Basic Info
    id: str = Field(default_factory=lambda: random(length=8))
    name: str
    player_name: str | None = None
    classes: list[CharacterClass] = Field(min_length=1)
    race: Race
    background: str | None = None
    alignment: str | None = None
    description: str | None = None  # A brief description of the character's appearance and demeanor.
    bio: str | None = None  # The character's backstory, personality, and motivations.

    # Core Stats
    abilities: dict[str, AbilityScore] = Field(
        default_factory=lambda: {
            "strength": AbilityScore(score=10),
            "dexterity": AbilityScore(score=10),
            "constitution": AbilityScore(score=10),
            "intelligence": AbilityScore(score=10),
            "wisdom": AbilityScore(score=10),
            "charisma": AbilityScore(score=10),
        }
    )

    # Progression
    experience_points: int = 0
    speed: int = 30

    # Combat Stats
    armor_class: int = 10
    hit_points_max: int = 1
    hit_points_current: int = 1
    temporary_hit_points: int = 0
    hit_dice_type: str = "d8"  # e.g., "d10" for Fighter, "d6" for Wizard
    hit_dice_remaining: str = "1d8"
    death_saves_success: int = Field(ge=0, le=3, default=0)
    death_saves_failure: int = Field(ge=0, le=3, default=0)
    conditions: list[str] = Field(default_factory=list)
    active_effects: list[ActiveEffect] = Field(
        default_factory=list,
        description="Active effects (buffs, debuffs, conditions) currently on this character"
    )
    concentration: "ConcentrationState | None" = Field(
        default=None,
        description="Current concentration state. None if not concentrating."
    )
    position: "Position | None" = Field(
        default=None,
        description="Grid position in combat (5ft per square). None when not in grid combat."
    )

    # Skills & Proficiencies
    proficiency_bonus: int = 2
    skill_proficiencies: list[str] = Field(default_factory=list)
    saving_throw_proficiencies: list[str] = Field(default_factory=list)
    tool_proficiencies: list[str] = Field(default_factory=list)

    # Equipment
    inventory: list[Item] = Field(default_factory=list)
    equipment: dict[str, Item | None] = Field(
        default_factory=lambda: {
            "weapon_main": None,
            "weapon_off": None,
            "armor": None,
            "shield": None,
        }
    )

    # Spellcasting
    spellcasting_ability: str | None = None
    spell_slots: dict[int, int] = Field(default_factory=dict)  # level: max_slots
    spell_slots_used: dict[int, int] = Field(default_factory=dict)  # level: used_slots
    spells_known: list[Spell] = Field(default_factory=list)

    # Character Features
    features_and_traits: list[str] = Field(default_factory=list)  # Legacy: plain text list
    features: list[Feature] = Field(default_factory=list)  # Structured features with source/level
    languages: list[str] = Field(default_factory=list)

    # Misc
    inspiration: bool = False
    notes: str = ""
    creation_rolls: dict[str, Any] | None = Field(
        default=None,
        description="Dice rolls from character creation (ability scores, HP, gold, etc.)"
    )
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @model_validator(mode="before")
    @classmethod
    def _migrate_character_class(cls, data: Any) -> Any:
        """Migrate old character_class field to classes list.

        Supports both old format (character_class: CharacterClass)
        and new format (classes: [CharacterClass, ...]).
        """
        if isinstance(data, dict):
            if "character_class" in data and "classes" not in data:
                cc = data.pop("character_class")
                data["classes"] = [cc]
        return data

    @property
    def character_class(self) -> CharacterClass:
        """Backward-compatible access to primary class.

        Returns a reference to classes[0], so mutations like
        char.character_class.level = X work in-place.
        """
        return self.classes[0]

    @property
    def total_level(self) -> int:
        """Total character level across all classes."""
        return sum(c.level for c in self.classes)

    @property
    def is_multiclass(self) -> bool:
        """Whether the character has more than one class."""
        return len(self.classes) > 1

    def class_string(self) -> str:
        """Human-readable class string, e.g. 'Fighter 5 / Wizard 3'."""
        return " / ".join(f"{c.name} {c.level}" for c in self.classes)

    @model_validator(mode="after")
    def _compute_proficiency_bonus(self) -> "Character":
        """Auto-calculate proficiency bonus from total level."""
        self.proficiency_bonus = 2 + (self.total_level - 1) // 4
        return self

    @model_validator(mode="after")
    def _heal_spell_slots(self) -> "Character":
        """Self-heal casters that were persisted without spell slots."""
        self.heal_missing_spell_slots()
        return self

    def heal_missing_spell_slots(self) -> bool:
        """Populate empty spell_slots from the SRD progression for the primary class.

        A character who knows leveled spells should have spell slots; older
        creation/import paths could leave them empty. Returns True if slots
        were populated, False if no repair was needed or possible. Existing
        slot data (e.g. rulebook-derived) is never overridden.
        """
        if self.spell_slots:
            return False
        if not any(spell.level > 0 for spell in self.spells_known):
            return False
        slots = slots_for_class(self.classes[0].name, self.classes[0].level)
        if not slots:
            return False
        self.spell_slots = slots
        logger.info(
            f"Repaired missing spell slots for {self.name} "
            f"({self.classes[0].name} {self.classes[0].level}): {slots}"
        )
        return True


class NPC(BaseModel):
    """Non-player character."""
    id: str = Field(default_factory=lambda: random(length=8))
    name: str
    description: str | None = None
    bio: str | None = None  # The NPC's backstory, motivations, and secrets.
    race: str | None = None
    occupation: str | None = None
    location: str | None = None
    attitude: str | None = None  # friendly, neutral, hostile, etc.
    notes: str = ""
    stats: dict[str, Any] | None = None  # Combat stats if needed
    relationships: dict[str, str] = Field(default_factory=dict)  # character_name: relationship


class Location(BaseModel):
    """Geographic location or settlement."""
    id: str = Field(default_factory=lambda: random(length=8))
    name: str
    location_type: str  # city, town, village, dungeon, forest, etc.
    description: str
    population: int | None = None
    government: str | None = None
    notable_features: list[str] = Field(default_factory=list)
    npcs: list[str] = Field(default_factory=list)  # NPC names
    connections: list[str] = Field(default_factory=list)  # Connected locations
    notes: str = ""


class Quest(BaseModel):
    """Quest or mission."""
    id: str = Field(default_factory=lambda: random(length=8))
    title: str
    description: str
    giver: str | None = None  # NPC who gave the quest
    status: str = "active"  # active, completed, failed, on_hold
    objectives: list[str] = Field(default_factory=list)
    completed_objectives: list[str] = Field(default_factory=list)
    reward: str | None = None
    notes: str = ""
    created_at: datetime = Field(default_factory=datetime.now)


class CombatEncounter(BaseModel):
    """Combat encounter details."""
    id: str = Field(default_factory=lambda: random(length=8))
    name: str
    description: str
    enemies: list[str] = Field(default_factory=list)
    difficulty: str | None = None  # easy, medium, hard, deadly
    experience_value: int | None = None
    location: str | None = None
    status: str = "planned"  # planned, active, completed
    notes: str = ""


class SessionNote(BaseModel):
    """Session notes and summary."""
    id: str = Field(default_factory=lambda: random(length=8))
    session_number: int
    date: datetime = Field(default_factory=datetime.now)
    title: str | None = None
    summary: str
    events: list[str] = Field(default_factory=list)
    characters_present: list[str] = Field(default_factory=list)
    npcs_encountered: list[str] = Field(default_factory=list)
    quest_updates: dict[str, str] = Field(default_factory=dict)
    combat_encounters: list[str] = Field(default_factory=list)
    experience_gained: int | None = None
    treasure_found: list[str] = Field(default_factory=list, alias="loot_found")
    notes: str = ""


class GameState(BaseModel):
    """Current state of the game."""
    campaign_name: str
    current_session: int = 1
    current_date_in_game: str | None = None
    current_location: str | None = None
    active_quests: list[str] = Field(default_factory=list)
    party_level: int = 1
    party_funds: str = "0 gp"
    initiative_order: list[dict[str, Any]] = Field(default_factory=list)
    in_combat: bool = False
    current_turn: str | None = None
    notes: str = ""
    updated_at: datetime = Field(default_factory=datetime.now)


class Campaign(BaseModel):
    """Main campaign container."""
    id: str = Field(default_factory=lambda: random(length=8))
    name: str
    description: str
    dm_name: str | None = None
    setting: str | Path | None = None
    characters: dict[str, Character] = Field(default_factory=dict)
    npcs: dict[str, NPC] = Field(default_factory=dict)
    locations: dict[str, Location] = Field(default_factory=dict)
    quests: dict[str, Quest] = Field(default_factory=dict)
    encounters: dict[str, CombatEncounter] = Field(default_factory=dict)
    sessions: list[SessionNote] = Field(default_factory=list)
    game_state: GameState
    world_notes: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime | None = Field(default_factory=datetime.now)

    def get_setting(self) -> str:
        """Return the setting details for the active campaign."""
        if isinstance(self.setting, str):
            return self.setting
        elif isinstance(self.setting, Path):
            setting_txt = self.setting.read_text()
            setting_txt += f"\n\n(From file: {str(self.setting)})"
            return setting_txt
        elif not self.setting:
            return "(⚠️ No setting details have been set for this campaign!)"
        else:
            e = TypeError(f"❌ Unknown setting type: {type(self.setting)}")
            raise e



# Event types for the adventure log
class EventType(str, Enum):
    COMBAT = "combat"
    ROLEPLAY = "roleplay"
    EXPLORATION = "exploration"
    QUEST = "quest"
    CHARACTER = "character"
    WORLD = "world"
    SESSION = "session"
    SOCIAL = "social"


class AdventureEvent(BaseModel):
    """Individual event in the adventure log."""
    id: str = Field(default_factory=lambda: random(length=8))
    event_type: EventType
    title: str
    description: str
    timestamp: datetime = Field(default_factory=datetime.now)
    session_number: int | None = None
    characters_involved: list[str] = Field(default_factory=list)
    location: str | None = None
    tags: list[str] = Field(default_factory=list)
    importance: int = Field(ge=1, le=5, default=3)  # 1=minor, 5=major
    campaign: str | None = None  # owning campaign; None = unattributed (pre-scoping)

# Deferred import for Position to avoid circular dependency.
# Position is defined in combat.positioning but referenced as a forward-ref
# string annotation ("Position | None") on Character.position.
from dm20_protocol.combat.positioning import Position  # noqa: E402

Character.model_rebuild()

__all__ = [
    "AbilityScore",
    "CharacterClass",
    "Race",
    "Item",
    "Spell",
    "Feature",
    "Modifier",
    "ActiveEffect",
    "ConcentrationState",
    "Character",
    "NPC",
    "Location",
    "Quest",
    "CombatEncounter",
    "SessionNote",
    "GameState",
    "Campaign",
    "EventType",
    "AdventureEvent",
    "GameStats",
    "Position",
]