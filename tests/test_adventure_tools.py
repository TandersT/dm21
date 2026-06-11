"""
Tests for adventure loading tools and MCP integration.
"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from dm20_protocol.adventures.tools import load_adventure_flow
from dm20_protocol.adventures.parser import AdventureParserError
from dm20_protocol.claudmaster.models.module import (
    ContentType,
    LocationReference,
    ModuleElement,
    ModuleStructure,
    NPCReference,
)
from dm20_protocol.claudmaster.module_binding import BindingResult
from dm20_protocol.models import Campaign, GameState, Location, NPC, Quest

# Configure anyio to use only asyncio backend
pytestmark = pytest.mark.anyio


@pytest.fixture(scope="session")
def anyio_backend():
    """Configure anyio to use asyncio backend."""
    return "asyncio"


@pytest.fixture
def mock_module_structure():
    """Create a mock ModuleStructure for testing."""
    return ModuleStructure(
        module_id="TestAdv",
        title="Test Adventure",
        source_file="adventure-TestAdv.json",
        chapters=[
            ModuleElement(
                name="Chapter 1: The Beginning",
                content_type=ContentType.CHAPTER,
                page_start=1,
            ),
            ModuleElement(
                name="Chapter 2: The Middle", content_type=ContentType.CHAPTER, page_start=10
            ),
        ],
        npcs=[
            NPCReference(
                name="Bob the Wizard",
                chapter="Chapter 1: The Beginning",
                page=3,
                description_preview="A wise wizard",
            ),
            NPCReference(
                name="Alice the Rogue",
                chapter="Chapter 1: The Beginning",
                page=5,
                description_preview="A cunning thief",
            ),
            NPCReference(
                name="Charlie the Barbarian",
                chapter="Chapter 2: The Middle",
                page=12,
                description_preview="A mighty warrior",
            ),
        ],
        locations=[
            LocationReference(
                name="Starting Town", chapter="Chapter 1: The Beginning", page=2
            ),
            LocationReference(
                name="Dark Forest", chapter="Chapter 1: The Beginning", page=4
            ),
            LocationReference(
                name="Mountain Peak", chapter="Chapter 2: The Middle", page=11
            ),
        ],
        metadata={"source": "TestAdv"},
    )


@pytest.fixture
def mock_storage():
    """Create a mock DnDStorage instance."""
    storage = MagicMock()
    storage._current_campaign = None
    storage.list_campaigns.return_value = []
    storage._split_backend = MagicMock()
    storage._split_backend._get_campaign_dir.return_value = Path("/fake/campaign/dir")
    return storage


@pytest.fixture
def mock_storage_with_campaign(mock_storage):
    """Create a mock DnDStorage with an existing campaign."""
    game_state = GameState(campaign_name="Test Campaign")
    campaign = Campaign(
        name="Test Campaign", description="Test", game_state=game_state
    )
    mock_storage._current_campaign = campaign
    return mock_storage


async def test_load_adventure_flow_new_campaign(mock_storage, mock_module_structure):
    """Test loading an adventure with a new campaign name."""
    # Mock the parser
    with patch(
        "dm20_protocol.adventures.tools.AdventureParser"
    ) as mock_parser_class:
        mock_parser = AsyncMock()
        mock_parser.parse_adventure.return_value = mock_module_structure
        mock_parser_class.return_value = mock_parser

        # Mock the module manager
        with patch(
            "dm20_protocol.adventures.tools.CampaignModuleManager"
        ) as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager.bind_module.return_value = BindingResult(
                success=True, module_id="TestAdv", message="Success"
            )
            mock_manager_class.return_value = mock_manager

            # Run the flow
            result = await load_adventure_flow(
                storage=mock_storage,
                data_path=Path("/fake/data"),
                adventure_id="TestAdv",
                campaign_name="New Campaign",
                populate_chapter_1=True,
            )

            # Verify parser was called
            mock_parser.parse_adventure.assert_called_once_with("TestAdv")

            # Verify campaign was created
            mock_storage.create_campaign.assert_called_once_with(
                name="New Campaign", description="Adventure: Test Adventure"
            )

            # Verify module was bound
            mock_manager.bind_module.assert_called_once_with(
                module_id="TestAdv", source_id="5etools", set_active=True
            )

            # Verify Chapter 1 entities were created
            assert mock_storage.add_location.call_count == 2  # Max 3, we have 2 in Ch1
            assert mock_storage.add_npc.call_count == 2  # Max 5, we have 2 in Ch1
            assert mock_storage.add_quest.call_count == 1

            # Verify result structure
            assert result["adventure_name"] == "Test Adventure"
            assert result["campaign_name"] == "New Campaign"
            assert result["module_bound"] is True
            assert result["chapter_1_populated"] is True
            assert result["entities_created"]["npcs"] == 2
            assert result["entities_created"]["locations"] == 2
            assert result["entities_created"]["quests"] == 1


async def test_load_adventure_flow_existing_campaign(
    mock_storage_with_campaign, mock_module_structure
):
    """Test loading an adventure into an existing campaign."""
    with patch(
        "dm20_protocol.adventures.tools.AdventureParser"
    ) as mock_parser_class:
        mock_parser = AsyncMock()
        mock_parser.parse_adventure.return_value = mock_module_structure
        mock_parser_class.return_value = mock_parser

        with patch(
            "dm20_protocol.adventures.tools.CampaignModuleManager"
        ) as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager.bind_module.return_value = BindingResult(
                success=True, module_id="TestAdv", message="Success"
            )
            mock_manager_class.return_value = mock_manager

            # Run without campaign_name (use current)
            result = await load_adventure_flow(
                storage=mock_storage_with_campaign,
                data_path=Path("/fake/data"),
                adventure_id="TestAdv",
                campaign_name=None,
                populate_chapter_1=True,
            )

            # Verify no new campaign was created
            mock_storage_with_campaign.create_campaign.assert_not_called()

            # Verify result uses existing campaign
            assert result["campaign_name"] == "Test Campaign"


async def test_load_adventure_flow_no_campaign_error(mock_storage, mock_module_structure):
    """Test error when no campaign_name provided and no current campaign."""
    with patch(
        "dm20_protocol.adventures.tools.AdventureParser"
    ) as mock_parser_class:
        mock_parser = AsyncMock()
        mock_parser.parse_adventure.return_value = mock_module_structure
        mock_parser_class.return_value = mock_parser

        # Should raise ValueError
        with pytest.raises(ValueError, match="No campaign_name provided"):
            await load_adventure_flow(
                storage=mock_storage,
                data_path=Path("/fake/data"),
                adventure_id="TestAdv",
                campaign_name=None,
                populate_chapter_1=True,
            )


async def test_load_adventure_flow_parser_error(mock_storage):
    """Test handling of parser errors."""
    with patch(
        "dm20_protocol.adventures.tools.AdventureParser"
    ) as mock_parser_class:
        mock_parser = AsyncMock()
        mock_parser.parse_adventure.side_effect = AdventureParserError(
            "Adventure not found"
        )
        mock_parser_class.return_value = mock_parser

        # Should propagate the parser error
        with pytest.raises(AdventureParserError, match="Adventure not found"):
            await load_adventure_flow(
                storage=mock_storage,
                data_path=Path("/fake/data"),
                adventure_id="BadAdv",
                campaign_name="Test Campaign",
                populate_chapter_1=True,
            )


async def test_load_adventure_flow_no_populate(
    mock_storage, mock_module_structure
):
    """Test loading without Chapter 1 population."""
    with patch(
        "dm20_protocol.adventures.tools.AdventureParser"
    ) as mock_parser_class:
        mock_parser = AsyncMock()
        mock_parser.parse_adventure.return_value = mock_module_structure
        mock_parser_class.return_value = mock_parser

        with patch(
            "dm20_protocol.adventures.tools.CampaignModuleManager"
        ) as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager.bind_module.return_value = BindingResult(
                success=True, module_id="TestAdv", message="Success"
            )
            mock_manager_class.return_value = mock_manager

            result = await load_adventure_flow(
                storage=mock_storage,
                data_path=Path("/fake/data"),
                adventure_id="TestAdv",
                campaign_name="Test Campaign",
                populate_chapter_1=False,
            )

            # Verify no entities were created
            mock_storage.add_location.assert_not_called()
            mock_storage.add_npc.assert_not_called()
            mock_storage.add_quest.assert_not_called()

            assert result["chapter_1_populated"] is False
            assert result["entities_created"]["npcs"] == 0
            assert result["entities_created"]["locations"] == 0
            assert result["entities_created"]["quests"] == 0


async def test_load_adventure_flow_no_chapters(mock_storage):
    """Test handling when module has no chapters."""
    # Create module without chapters
    module_no_chapters = ModuleStructure(
        module_id="NoChapters",
        title="No Chapters Adventure",
        source_file="adventure-NoChapters.json",
        chapters=[],
        npcs=[],
        locations=[],
        metadata={},
    )

    with patch(
        "dm20_protocol.adventures.tools.AdventureParser"
    ) as mock_parser_class:
        mock_parser = AsyncMock()
        mock_parser.parse_adventure.return_value = module_no_chapters
        mock_parser_class.return_value = mock_parser

        with patch(
            "dm20_protocol.adventures.tools.CampaignModuleManager"
        ) as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager.bind_module.return_value = BindingResult(
                success=True, module_id="NoChapters", message="Success"
            )
            mock_manager_class.return_value = mock_manager

            result = await load_adventure_flow(
                storage=mock_storage,
                data_path=Path("/fake/data"),
                adventure_id="NoChapters",
                campaign_name="Test Campaign",
                populate_chapter_1=True,
            )

            # Should have warning about no chapters
            assert any("No chapters found" in w for w in result["warnings"])
            assert result["chapter_1_populated"] is False


async def test_load_adventure_flow_only_chapter_1_entities(
    mock_storage, mock_module_structure
):
    """Test that only Chapter 1 entities are created (spoiler boundary)."""
    with patch(
        "dm20_protocol.adventures.tools.AdventureParser"
    ) as mock_parser_class:
        mock_parser = AsyncMock()
        mock_parser.parse_adventure.return_value = mock_module_structure
        mock_parser_class.return_value = mock_parser

        with patch(
            "dm20_protocol.adventures.tools.CampaignModuleManager"
        ) as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager.bind_module.return_value = BindingResult(
                success=True, module_id="TestAdv", message="Success"
            )
            mock_manager_class.return_value = mock_manager

            result = await load_adventure_flow(
                storage=mock_storage,
                data_path=Path("/fake/data"),
                adventure_id="TestAdv",
                campaign_name="Test Campaign",
                populate_chapter_1=True,
            )

            # Verify only Chapter 1 NPCs were created
            add_npc_calls = mock_storage.add_npc.call_args_list
            created_npc_names = [call[0][0].name for call in add_npc_calls]

            # Should have Bob and Alice (Ch1), NOT Charlie (Ch2)
            assert "Bob the Wizard" in created_npc_names
            assert "Alice the Rogue" in created_npc_names
            assert "Charlie the Barbarian" not in created_npc_names

            # Verify only Chapter 1 locations were created
            add_location_calls = mock_storage.add_location.call_args_list
            created_location_names = [call[0][0].name for call in add_location_calls]

            # Should have Starting Town and Dark Forest (Ch1), NOT Mountain Peak (Ch2)
            assert "Starting Town" in created_location_names
            assert "Dark Forest" in created_location_names
            assert "Mountain Peak" not in created_location_names


async def test_load_adventure_flow_existing_campaign_loaded(mock_storage, mock_module_structure):
    """Test loading into an existing campaign that's already in the list."""
    # Mark campaign as already existing
    mock_storage.list_campaigns.return_value = ["Existing Campaign"]

    with patch(
        "dm20_protocol.adventures.tools.AdventureParser"
    ) as mock_parser_class:
        mock_parser = AsyncMock()
        mock_parser.parse_adventure.return_value = mock_module_structure
        mock_parser_class.return_value = mock_parser

        with patch(
            "dm20_protocol.adventures.tools.CampaignModuleManager"
        ) as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager.bind_module.return_value = BindingResult(
                success=True, module_id="TestAdv", message="Success"
            )
            mock_manager_class.return_value = mock_manager

            result = await load_adventure_flow(
                storage=mock_storage,
                data_path=Path("/fake/data"),
                adventure_id="TestAdv",
                campaign_name="Existing Campaign",
                populate_chapter_1=True,
            )

            # Should load existing campaign instead of creating
            mock_storage.load_campaign.assert_called_once_with("Existing Campaign")
            mock_storage.create_campaign.assert_not_called()

            # Should have warning about existing campaign
            assert any("already exists" in w for w in result["warnings"])


async def test_load_adventure_flow_binding_failure(mock_storage, mock_module_structure):
    """Test handling when module binding fails."""
    with patch(
        "dm20_protocol.adventures.tools.AdventureParser"
    ) as mock_parser_class:
        mock_parser = AsyncMock()
        mock_parser.parse_adventure.return_value = mock_module_structure
        mock_parser_class.return_value = mock_parser

        with patch(
            "dm20_protocol.adventures.tools.CampaignModuleManager"
        ) as mock_manager_class:
            mock_manager = MagicMock()
            # Simulate binding failure
            mock_manager.bind_module.return_value = BindingResult(
                success=False, module_id="TestAdv", message="Already bound"
            )
            mock_manager_class.return_value = mock_manager

            result = await load_adventure_flow(
                storage=mock_storage,
                data_path=Path("/fake/data"),
                adventure_id="TestAdv",
                campaign_name="Test Campaign",
                populate_chapter_1=True,
            )

            # Should have warning about binding failure
            assert result["module_bound"] is False
            assert any("binding" in w.lower() for w in result["warnings"])


async def test_load_adventure_flow_skips_front_matter(mock_storage):
    """Population selects the first playable chapter, skipping front-matter.

    Many real modules (e.g. Curse of Strahd) open with a Foreword and an
    Introduction before the first playable chapter. Chapter 1 population must
    seed from the playable chapter, not the front-matter.
    """
    module_with_front_matter = ModuleStructure(
        module_id="FrontMatter",
        title="Front Matter Adventure",
        source_file="adventure-FrontMatter.json",
        chapters=[
            ModuleElement(
                name="Foreword: A Note",
                content_type=ContentType.CHAPTER,
                page_start=1,
            ),
            ModuleElement(
                name="Introduction",
                content_type=ContentType.CHAPTER,
                page_start=2,
            ),
            ModuleElement(
                name="Chapter 1: Into the Mists",
                content_type=ContentType.CHAPTER,
                page_start=5,
            ),
        ],
        npcs=[
            NPCReference(name="Foreword Ghost", chapter="Foreword: A Note", page=1),
            NPCReference(
                name="Strahd", chapter="Chapter 1: Into the Mists", page=5
            ),
        ],
        locations=[
            LocationReference(name="Front Cover", chapter="Foreword: A Note", page=1),
            LocationReference(
                name="Village of Barovia",
                chapter="Chapter 1: Into the Mists",
                page=5,
            ),
        ],
        metadata={},
    )

    with patch(
        "dm20_protocol.adventures.tools.AdventureParser"
    ) as mock_parser_class:
        mock_parser = AsyncMock()
        mock_parser.parse_adventure.return_value = module_with_front_matter
        mock_parser_class.return_value = mock_parser

        with patch(
            "dm20_protocol.adventures.tools.CampaignModuleManager"
        ) as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager.bind_module.return_value = BindingResult(
                success=True, module_id="FrontMatter", message="Success"
            )
            mock_manager_class.return_value = mock_manager

            result = await load_adventure_flow(
                storage=mock_storage,
                data_path=Path("/fake/data"),
                adventure_id="FrontMatter",
                campaign_name="Test Campaign",
                populate_chapter_1=True,
            )

    # Entities seeded from the playable chapter, not the front-matter
    created_npc_names = [c[0][0].name for c in mock_storage.add_npc.call_args_list]
    assert "Strahd" in created_npc_names
    assert "Foreword Ghost" not in created_npc_names

    created_loc_names = [
        c[0][0].name for c in mock_storage.add_location.call_args_list
    ]
    assert "Village of Barovia" in created_loc_names
    assert "Front Cover" not in created_loc_names

    # Module progress advanced to the playable chapter
    mock_manager.update_progress.assert_called_once_with(
        module_id="FrontMatter", current_chapter="Chapter 1: Into the Mists"
    )
    assert result["chapter_1_populated"] is True


async def test_load_adventure_flow_max_limits(mock_storage):
    """Test that entity limits are respected (max 3 locations, max 5 NPCs)."""
    # Create module with many Chapter 1 entities
    module_many_entities = ModuleStructure(
        module_id="ManyEntities",
        title="Many Entities Adventure",
        source_file="adventure-ManyEntities.json",
        chapters=[
            ModuleElement(
                name="Chapter 1", content_type=ContentType.CHAPTER, page_start=1
            )
        ],
        npcs=[
            NPCReference(name=f"NPC{i}", chapter="Chapter 1", page=i)
            for i in range(10)
        ],
        locations=[
            LocationReference(name=f"Location{i}", chapter="Chapter 1", page=i)
            for i in range(10)
        ],
        metadata={},
    )

    with patch(
        "dm20_protocol.adventures.tools.AdventureParser"
    ) as mock_parser_class:
        mock_parser = AsyncMock()
        mock_parser.parse_adventure.return_value = module_many_entities
        mock_parser_class.return_value = mock_parser

        with patch(
            "dm20_protocol.adventures.tools.CampaignModuleManager"
        ) as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager.bind_module.return_value = BindingResult(
                success=True, module_id="ManyEntities", message="Success"
            )
            mock_manager_class.return_value = mock_manager

            result = await load_adventure_flow(
                storage=mock_storage,
                data_path=Path("/fake/data"),
                adventure_id="ManyEntities",
                campaign_name="Test Campaign",
                populate_chapter_1=True,
            )

            # Should respect limits
            assert result["entities_created"]["locations"] == 3  # Max 3
            assert result["entities_created"]["npcs"] == 5  # Max 5
            assert mock_storage.add_location.call_count == 3
            assert mock_storage.add_npc.call_count == 5
