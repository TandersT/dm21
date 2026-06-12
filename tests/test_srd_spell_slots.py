"""Tests for the built-in SRD spell slot progression tables."""

from dm20_protocol.srd_spell_slots import slots_for_caster_type, slots_for_class


class TestSlotsForClass:
    """Class-name keyed lookups against PHB checkpoints."""

    def test_wizard_level_1(self):
        assert slots_for_class("Wizard", 1) == {1: 2}

    def test_wizard_level_5(self):
        assert slots_for_class("Wizard", 5) == {1: 4, 2: 3, 3: 2}

    def test_sorcerer_level_1(self):
        # Broden Arolio's shape: level 1 sorcerer with leveled spells known.
        assert slots_for_class("Sorcerer", 1) == {1: 2}

    def test_cleric_level_20_has_ninth_level_slot(self):
        slots = slots_for_class("Cleric", 20)
        assert slots[9] == 1
        assert slots[1] == 4

    def test_class_name_case_insensitive(self):
        assert slots_for_class("wIzArD", 3) == {1: 4, 2: 2}

    def test_paladin_level_1_has_no_slots(self):
        assert slots_for_class("Paladin", 1) == {}

    def test_paladin_level_5(self):
        assert slots_for_class("Paladin", 5) == {1: 4, 2: 2}

    def test_ranger_level_2(self):
        assert slots_for_class("Ranger", 2) == {1: 2}

    def test_warlock_level_1(self):
        assert slots_for_class("Warlock", 1) == {1: 1}

    def test_warlock_level_5_pact_slots(self):
        assert slots_for_class("Warlock", 5) == {3: 2}

    def test_warlock_level_17(self):
        assert slots_for_class("Warlock", 17) == {5: 4}

    def test_non_caster_class(self):
        assert slots_for_class("Fighter", 10) == {}

    def test_unknown_class_name(self):
        assert slots_for_class("Homebrewmancer", 5) == {}


class TestSlotsForCasterType:
    """Caster-type keyed lookups (SpellcastingInfo.caster_type literals)."""

    def test_full_caster(self):
        assert slots_for_caster_type("full", 4) == {1: 4, 2: 3}

    def test_half_caster_rounds_down(self):
        # Half casters use the full table at ceil(level / 2); none at level 1.
        assert slots_for_caster_type("half", 1) == {}
        assert slots_for_caster_type("half", 6) == {1: 4, 2: 2}

    def test_third_caster_starts_at_3(self):
        assert slots_for_caster_type("third", 2) == {}
        assert slots_for_caster_type("third", 3) == {1: 2}
        assert slots_for_caster_type("third", 7) == {1: 4, 2: 2}

    def test_pact_magic(self):
        assert slots_for_caster_type("pact", 11) == {5: 3}

    def test_unknown_type(self):
        assert slots_for_caster_type("psionic", 5) == {}

    def test_level_out_of_range(self):
        assert slots_for_caster_type("full", 0) == {}
        assert slots_for_caster_type("full", 21) == {}
