"""Built-in SRD spell slot progressions (PHB ch. 10).

Fallback source for spell slot maximums when no rulebook slot table is
available. Rulebook `SpellcastingInfo.spell_slots` data wins when present.
"""

from math import ceil

# Full-caster slots by character level: [1st, 2nd, ..., 9th].
_FULL_CASTER_SLOTS: dict[int, list[int]] = {
    1: [2],
    2: [3],
    3: [4, 2],
    4: [4, 3],
    5: [4, 3, 2],
    6: [4, 3, 3],
    7: [4, 3, 3, 1],
    8: [4, 3, 3, 2],
    9: [4, 3, 3, 3, 1],
    10: [4, 3, 3, 3, 2],
    11: [4, 3, 3, 3, 2, 1],
    12: [4, 3, 3, 3, 2, 1],
    13: [4, 3, 3, 3, 2, 1, 1],
    14: [4, 3, 3, 3, 2, 1, 1],
    15: [4, 3, 3, 3, 2, 1, 1, 1],
    16: [4, 3, 3, 3, 2, 1, 1, 1],
    17: [4, 3, 3, 3, 2, 1, 1, 1, 1],
    18: [4, 3, 3, 3, 3, 1, 1, 1, 1],
    19: [4, 3, 3, 3, 3, 2, 1, 1, 1],
    20: [4, 3, 3, 3, 3, 2, 2, 1, 1],
}

# Class name -> caster type (matches SpellcastingInfo.caster_type literals).
_CASTER_TYPE_BY_CLASS: dict[str, str] = {
    "bard": "full",
    "cleric": "full",
    "druid": "full",
    "sorcerer": "full",
    "wizard": "full",
    "paladin": "half",
    "ranger": "half",
    "artificer": "half",
    "warlock": "pact",
}


def slots_for_caster_type(caster_type: str, level: int) -> dict[int, int]:
    """Spell slot maximums {spell_level: count} for a caster type and level.

    Returns {} when the type is unknown or grants no slots at that level.
    """
    if level < 1 or level > 20:
        return {}

    if caster_type == "pact":
        pact_level = min(ceil(level / 2), 5)
        count = 1 if level == 1 else 2 if level < 11 else 3 if level < 17 else 4
        return {pact_level: count}

    if caster_type == "full":
        effective_level = level
    elif caster_type == "half":
        effective_level = ceil(level / 2) if level >= 2 else 0
    elif caster_type == "third":
        effective_level = ceil(level / 3) if level >= 3 else 0
    else:
        return {}

    slots = _FULL_CASTER_SLOTS.get(effective_level, [])
    return {idx + 1: count for idx, count in enumerate(slots) if count > 0}


def caster_type_for_class(class_name: str) -> str | None:
    """Caster type for a class name (case-insensitive), or None if unknown."""
    return _CASTER_TYPE_BY_CLASS.get(class_name.strip().lower())


def slots_for_class(class_name: str, level: int) -> dict[int, int]:
    """Spell slot maximums for a class name (case-insensitive) and level.

    Returns {} for non-casters and unknown class names.
    """
    caster_type = caster_type_for_class(class_name)
    if caster_type is None:
        return {}
    return slots_for_caster_type(caster_type, level)
