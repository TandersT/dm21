"""
Adventure loading tools for MCP integration.

Orchestrates the adventure parsing, campaign creation, module binding,
and optional Chapter 1 entity population workflow.
"""

from __future__ import annotations

import logging
from pathlib import Path

from dm20_protocol.adventures.parser import AdventureParser, AdventureParserError
from dm20_protocol.claudmaster.module_binding import CampaignModuleManager
from dm20_protocol.models import Location, NPC, Quest

logger = logging.getLogger("dm20-protocol")

# Leading section names that are front-matter, not playable chapters. Matched
# as case-insensitive substrings against a chapter name.
_FRONT_MATTER_MARKERS = (
    "foreword",
    "introduction",
    "preface",
    "credits",
    "acknowledg",
    "how to use",
    "using this",
    "table of contents",
    "welcome to",
)


def _first_playable_chapter(chapters):
    """Return the first chapter that is actual play content.

    Real modules often open with a Foreword and an Introduction before the
    first playable chapter. This skips leading front-matter and returns the
    first content chapter, falling back to the first chapter if every entry
    looks like front-matter.

    Args:
        chapters: Non-empty list of ModuleElement chapters.

    Returns:
        The first playable ModuleElement.
    """
    for chapter in chapters:
        name_lower = chapter.name.lower()
        if not any(marker in name_lower for marker in _FRONT_MATTER_MARKERS):
            return chapter
    return chapters[0]


async def load_adventure_flow(
    storage,  # DnDStorage instance
    data_path: Path,
    adventure_id: str,
    campaign_name: str | None = None,
    populate_chapter_1: bool = True,
) -> dict:
    """Load an adventure module and integrate it with the campaign.

    This function orchestrates the complete adventure loading workflow:
    1. Parse adventure from 5etools JSON (download if needed, use cache if available)
    2. Create new campaign or use existing one
    3. Bind module to campaign
    4. Auto-populate Chapter 1 entities (locations, NPCs, starting quest)

    Args:
        storage: DnDStorage instance with current campaign context.
        data_path: Base data path for cache directories.
        adventure_id: Adventure ID from 5etools (e.g., 'CoS', 'LMoP', 'SCC-CK').
        campaign_name: Name for new campaign. If None, uses current campaign.
        populate_chapter_1: Whether to auto-create Chapter 1 locations, NPCs, and quest.

    Returns:
        Summary dict with keys:
            - adventure_name: Title of the adventure
            - campaign_name: Name of the campaign
            - module_bound: Whether module binding succeeded
            - entities_created: Dict with counts {npcs, locations, quests}
            - chapter_1_populated: Whether Chapter 1 was populated
            - warnings: List of warning messages

    Raises:
        ValueError: If no campaign_name provided and no current campaign exists.
        AdventureParserError: If adventure parsing fails.
    """
    logger.info(f"Loading adventure '{adventure_id}'...")

    warnings: list[str] = []
    entities_created = {"npcs": 0, "locations": 0, "quests": 0}

    # Step 1: Parse adventure
    try:
        parser = AdventureParser(cache_dir=data_path)
        module = await parser.parse_adventure(adventure_id)
        logger.info(f"Parsed adventure: {module.title}")
    except AdventureParserError as e:
        logger.error(f"Failed to parse adventure '{adventure_id}': {e}")
        raise

    # Step 2: Create or use campaign
    if campaign_name:
        # Check if campaign already exists
        existing_campaigns = storage.list_campaigns()
        if campaign_name in existing_campaigns:
            logger.warning(f"Campaign '{campaign_name}' already exists, using existing")
            warnings.append(f"Campaign '{campaign_name}' already exists, using existing")
            storage.load_campaign(campaign_name)
        else:
            storage.create_campaign(
                name=campaign_name, description=f"Adventure: {module.title}"
            )
        final_campaign_name = campaign_name
    else:
        # Use current campaign
        if not storage._current_campaign:
            raise ValueError(
                "No campaign_name provided and no current campaign exists. "
                "Please provide a campaign_name or load a campaign first."
            )
        final_campaign_name = storage._current_campaign.name
        logger.info(f"Using current campaign: {final_campaign_name}")

    # Step 3: Bind module to campaign
    campaign_dir = storage._split_backend._get_campaign_dir(final_campaign_name)
    module_manager = CampaignModuleManager(campaign_path=campaign_dir)

    binding_result = module_manager.bind_module(
        module_id=adventure_id, source_id="5etools", set_active=True
    )

    if not binding_result.success:
        warnings.append(f"Module binding: {binding_result.message}")
        logger.warning(f"Module binding warning: {binding_result.message}")

    module_bound = binding_result.success

    # Step 4: VectorStore indexing (SKIP for now - TODO)
    # ChromaDB integration for RAG is out of scope for this task.
    # The ModuleIndexer.index_module() requires a PDF path which we don't have
    # for 5etools JSON-based adventures. A future enhancement could add
    # index_module_from_text() method that chunks the ModuleStructure directly.
    logger.debug("Skipping RAG indexing (ChromaDB integration not yet implemented)")

    # Step 5: Chapter 1 auto-population
    chapter_1_populated = False
    if populate_chapter_1 and module.chapters:
        ch1 = _first_playable_chapter(module.chapters)
        logger.info(f"Populating Chapter 1: {ch1.name}")

        # Create locations from Chapter 1 (max 3)
        ch1_locations = [loc for loc in module.locations if loc.chapter == ch1.name]
        for loc in ch1_locations[:3]:
            try:
                storage.add_location(
                    Location(
                        name=loc.name,
                        location_type="area",
                        description=f"Location from {module.title}, {ch1.name}",
                    )
                )
                entities_created["locations"] += 1
                logger.debug(f"Created location: {loc.name}")
            except Exception as e:
                logger.warning(f"Failed to create location {loc.name}: {e}")
                warnings.append(f"Failed to create location {loc.name}: {e}")

        # Create NPCs from Chapter 1 (max 5)
        ch1_npcs = [npc for npc in module.npcs if npc.chapter == ch1.name]
        for npc in ch1_npcs[:5]:
            try:
                storage.add_npc(
                    NPC(
                        name=npc.name,
                        description=npc.description_preview
                        or f"NPC from {module.title}, {ch1.name}",
                    )
                )
                entities_created["npcs"] += 1
                logger.debug(f"Created NPC: {npc.name}")
            except Exception as e:
                logger.warning(f"Failed to create NPC {npc.name}: {e}")
                warnings.append(f"Failed to create NPC {npc.name}: {e}")

        # Create initial quest
        try:
            quest_title = f"{module.title} - Chapter 1"
            storage.add_quest(
                Quest(
                    title=quest_title,
                    description=f"Begin the {module.title} adventure: {ch1.name}",
                    status="active",
                )
            )
            entities_created["quests"] += 1
            logger.debug(f"Created starting quest: {quest_title}")
        except Exception as e:
            logger.warning(f"Failed to create starting quest: {e}")
            warnings.append(f"Failed to create starting quest: {e}")

        # Set game state to first location
        if ch1_locations:
            try:
                storage.update_game_state(current_location=ch1_locations[0].name)
                logger.debug(f"Set current location to: {ch1_locations[0].name}")
            except Exception as e:
                logger.warning(f"Failed to update game state: {e}")
                warnings.append(f"Failed to update game state: {e}")

        # Update module progress to Chapter 1
        try:
            module_manager.update_progress(
                module_id=adventure_id, current_chapter=ch1.name
            )
            logger.debug(f"Set module progress to chapter: {ch1.name}")
        except Exception as e:
            logger.warning(f"Failed to update module progress: {e}")
            warnings.append(f"Failed to update module progress: {e}")

        chapter_1_populated = True
        logger.info(
            f"Chapter 1 populated: {entities_created['npcs']} NPCs, "
            f"{entities_created['locations']} locations, "
            f"{entities_created['quests']} quest"
        )
    elif populate_chapter_1 and not module.chapters:
        warnings.append("No chapters found in module, skipping Chapter 1 population")
        logger.warning("No chapters found in module, skipping Chapter 1 population")

    # Return summary
    result = {
        "adventure_name": module.title,
        "campaign_name": final_campaign_name,
        "module_bound": module_bound,
        "entities_created": entities_created,
        "chapter_1_populated": chapter_1_populated,
        "warnings": warnings,
    }

    logger.info(f"Adventure loading complete: {result}")
    return result


__all__ = ["load_adventure_flow"]
