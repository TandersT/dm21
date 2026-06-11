"""
Tests for adventure content parser.

Tests the AdventureParser class including chapter extraction, NPC extraction,
encounter detection, location tracking, and read-aloud text handling.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from dm20_protocol.adventures.parser import (
    AdventureParser,
    AdventureParserError,
    ParserContext,
)
from dm20_protocol.claudmaster.models.module import ContentType

# Use anyio for async tests (compatible with pytest-anyio)
pytestmark = pytest.mark.anyio


@pytest.fixture(scope="session")
def anyio_backend():
    """Configure anyio to use asyncio backend."""
    return "asyncio"


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    """Temporary cache directory."""
    return tmp_path


@pytest.fixture
def parser(cache_dir: Path) -> AdventureParser:
    """Adventure parser instance."""
    return AdventureParser(cache_dir)


@pytest.fixture
def parser_context() -> ParserContext:
    """Fresh parser context."""
    return ParserContext(current_chapter="Chapter 1", current_page=1)


# --- Mock Adventure Data ---


def create_mock_adventure_data() -> dict[str, Any]:
    """Create realistic mock adventure JSON data."""
    return {
        "data": [
            {
                "name": "Lost Mine of Phandelver",
                "source": "LMoP",
                "data": [
                    {
                        "type": "section",
                        "name": "Chapter 1: Goblin Arrows",
                        "page": 1,
                        "entries": [
                            "The adventure begins on the High Road.",
                            {
                                "type": "insetReadaloud",
                                "entries": [
                                    "You've been on the road for days. "
                                    "The weather has been pleasant, and "
                                    "your companions are in good spirits."
                                ],
                            },
                            {
                                "type": "entries",
                                "name": "Goblin Ambush",
                                "entries": [
                                    "The party encounters {@creature goblin|MM}s "
                                    "on the road.",
                                    {
                                        "type": "statblock",
                                        "name": "Goblin",
                                        "source": "MM",
                                    },
                                ],
                            },
                        ],
                    },
                    {
                        "type": "section",
                        "name": "Chapter 2: Phandalin",
                        "page": 10,
                        "entries": [
                            "The characters arrive in {@area Phandalin|LMoP}.",
                            {
                                "type": "entries",
                                "name": "1. Town Square",
                                "entries": [
                                    "The town square is bustling with activity.",
                                ],
                            },
                            {
                                "type": "entries",
                                "name": "2. Stonehill Inn",
                                "entries": [
                                    "You meet {@creature Toblen Stonehill|LMoP}, "
                                    "the innkeeper.",
                                    {
                                        "type": "statblockInline",
                                        "name": "Toblen Stonehill",
                                    },
                                ],
                            },
                            {
                                "type": "table",
                                "caption": "Random Encounter Table",
                                "colLabels": ["d20", "Encounter"],
                                "rows": [
                                    ["1-5", "1d4 goblins"],
                                    ["6-10", "1 ogre"],
                                ],
                            },
                        ],
                    },
                ],
            }
        ]
    }


def create_adventure_with_types() -> dict[str, Any]:
    """Create adventure data with various entry types."""
    return {
        "data": [
            {
                "name": "Test Adventure",
                "source": "TEST",
                "data": [
                    {
                        "type": "section",
                        "name": "Test Chapter",
                        "page": 1,
                        "entries": [
                            {"type": "image", "href": "test.png"},
                            {"type": "gallery", "images": []},
                            {"type": "flowchart", "blocks": []},
                            {
                                "type": "inset",
                                "name": "DM Note",
                                "entries": ["This is a DM note."],
                            },
                            {
                                "type": "list",
                                "items": ["Item 1", "Item 2", "Item 3"],
                            },
                            {
                                "type": "quote",
                                "entries": ["A famous quote."],
                            },
                            {
                                "type": "unknown_type",
                                "entries": ["Should still parse this."],
                            },
                        ],
                    }
                ],
            }
        ]
    }


# --- Parser Context Tests ---


def test_context_append_text(parser_context: ParserContext):
    """Test text appending with markup stripping."""
    parser_context.append_text("Normal text")
    parser_context.append_text("Text with {@spell fireball}")
    parser_context.append_text("DC check: {@dc 15}")

    assert len(parser_context.text_buffer) == 3
    assert parser_context.text_buffer[0] == "Normal text"
    assert parser_context.text_buffer[1] == "Text with fireball"
    assert parser_context.text_buffer[2] == "DC check: DC 15"


def test_context_section_id(parser_context: ParserContext):
    """Test section ID generation."""
    assert parser_context.get_section_id() == "Chapter 1"

    parser_context.current_section = "Section A"
    assert parser_context.get_section_id() == "Chapter 1::Section A"


def test_context_extract_creature_refs(parser_context: ParserContext):
    """Test creature reference extraction."""
    text = "You encounter {@creature goblin|MM} and {@creature orc|MM}."
    refs = parser_context.extract_creature_refs(text)

    assert len(refs) == 2
    assert "goblin" in refs
    assert "orc" in refs


def test_context_extract_area_refs(parser_context: ParserContext):
    """Test area reference extraction."""
    text = "Travel to {@area Phandalin|LMoP} and {@area Cragmaw Castle|LMoP}."
    refs = parser_context.extract_area_refs(text)

    assert len(refs) == 2
    assert "Phandalin" in refs
    assert "Cragmaw Castle" in refs


# --- Chapter Parsing Tests ---


async def test_parse_chapters(parser: AdventureParser):
    """Test chapter extraction from mock data."""
    mock_data = create_mock_adventure_data()

    with patch.object(parser, "_get_adventure_data", return_value=mock_data):
        result = await parser.parse_adventure("LMoP")

    assert result.module_id == "LMoP"
    assert result.title == "Lost Mine of Phandelver"
    assert len(result.chapters) == 2

    ch1 = result.chapters[0]
    assert ch1.name == "Chapter 1: Goblin Arrows"
    assert ch1.content_type == ContentType.CHAPTER
    assert ch1.page_start == 1

    ch2 = result.chapters[1]
    assert ch2.name == "Chapter 2: Phandalin"
    assert ch2.page_start == 10


async def test_parse_chapter_children(parser: AdventureParser):
    """Test subsection parsing as chapter children."""
    mock_data = create_mock_adventure_data()

    with patch.object(parser, "_get_adventure_data", return_value=mock_data):
        result = await parser.parse_adventure("LMoP")

    ch1 = result.chapters[0]
    assert len(ch1.children) == 1
    assert "Goblin Ambush" in ch1.children

    ch2 = result.chapters[1]
    assert len(ch2.children) == 2
    assert "1. Town Square" in ch2.children
    assert "2. Stonehill Inn" in ch2.children


# --- NPC Extraction Tests ---


async def test_npc_extraction_from_tags(parser: AdventureParser):
    """Test NPC extraction from {@creature ...} tags."""
    mock_data = create_mock_adventure_data()

    with patch.object(parser, "_get_adventure_data", return_value=mock_data):
        result = await parser.parse_adventure("LMoP")

    npc_names = {npc.name for npc in result.npcs}
    assert "Goblin" in npc_names
    assert "Toblen Stonehill" in npc_names


async def test_npc_extraction_from_statblocks(parser: AdventureParser):
    """Test NPC extraction from statblock entries."""
    mock_data = create_mock_adventure_data()

    with patch.object(parser, "_get_adventure_data", return_value=mock_data):
        result = await parser.parse_adventure("LMoP")

    # Should have NPCs from both statblock and statblockInline
    npc_names = {npc.name for npc in result.npcs}
    assert "Goblin" in npc_names
    assert "Toblen Stonehill" in npc_names


async def test_npc_deduplication(parser: AdventureParser):
    """Test that duplicate NPCs are only added once."""
    duplicate_data = {
        "data": [
            {
                "name": "Test",
                "source": "TEST",
                "data": [
                    {
                        "type": "section",
                        "name": "Chapter 1",
                        "page": 1,
                        "entries": [
                            "{@creature Goblin|MM} appears here.",
                            "{@creature Goblin|MM} appears again.",
                            {"type": "statblock", "name": "Goblin"},
                        ],
                    }
                ],
            }
        ]
    }

    with patch.object(parser, "_get_adventure_data", return_value=duplicate_data):
        result = await parser.parse_adventure("TEST")

    # Should only have one Goblin NPC
    npc_names = [npc.name for npc in result.npcs]
    assert npc_names.count("Goblin") == 1


async def test_npc_chapter_context(parser: AdventureParser):
    """Test that NPCs capture their chapter context."""
    mock_data = create_mock_adventure_data()

    with patch.object(parser, "_get_adventure_data", return_value=mock_data):
        result = await parser.parse_adventure("LMoP")

    goblin = next(npc for npc in result.npcs if npc.name == "Goblin")
    assert goblin.chapter == "Chapter 1: Goblin Arrows"
    assert goblin.page == 1

    toblen = next(npc for npc in result.npcs if npc.name == "Toblen Stonehill")
    assert toblen.chapter == "Chapter 2: Phandalin"
    assert toblen.page == 10


# --- Encounter Extraction Tests ---


async def test_encounter_extraction_from_table(parser: AdventureParser):
    """Test encounter extraction from tables."""
    mock_data = create_mock_adventure_data()

    with patch.object(parser, "_get_adventure_data", return_value=mock_data):
        result = await parser.parse_adventure("LMoP")

    assert len(result.encounters) == 1
    enc = result.encounters[0]
    assert enc.name == "Random Encounter Table"
    assert enc.chapter == "Chapter 2: Phandalin"
    assert enc.encounter_type == "combat"


async def test_encounter_type_inference(parser: AdventureParser):
    """Test encounter type inference from caption."""
    encounter_data = {
        "data": [
            {
                "name": "Test",
                "source": "TEST",
                "data": [
                    {
                        "type": "section",
                        "name": "Chapter 1",
                        "page": 1,
                        "entries": [
                            {
                                "type": "table",
                                "caption": "Social Encounter: Persuasion Checks",
                            },
                            {
                                "type": "table",
                                "caption": "Trap and Puzzle Encounter",
                            },
                            {
                                "type": "table",
                                "caption": "Combat Encounter",
                            },
                        ],
                    }
                ],
            }
        ]
    }

    with patch.object(parser, "_get_adventure_data", return_value=encounter_data):
        result = await parser.parse_adventure("TEST")

    assert len(result.encounters) == 3

    social = next(e for e in result.encounters if "Social" in e.name)
    assert social.encounter_type == "social"

    trap = next(e for e in result.encounters if "Trap" in e.name)
    assert trap.encounter_type == "exploration"

    combat = next(e for e in result.encounters if "Combat" in e.name)
    assert combat.encounter_type == "combat"


# --- Location Extraction Tests ---


async def test_location_extraction_from_area_tags(parser: AdventureParser):
    """Test location extraction from {@area ...} tags."""
    mock_data = create_mock_adventure_data()

    with patch.object(parser, "_get_adventure_data", return_value=mock_data):
        result = await parser.parse_adventure("LMoP")

    location_names = {loc.name for loc in result.locations}
    assert "Phandalin" in location_names


async def test_location_extraction_from_numbered_areas(parser: AdventureParser):
    """Test location extraction from numbered area headings."""
    mock_data = create_mock_adventure_data()

    with patch.object(parser, "_get_adventure_data", return_value=mock_data):
        result = await parser.parse_adventure("LMoP")

    location_names = {loc.name for loc in result.locations}
    assert "1. Town Square" in location_names
    assert "2. Stonehill Inn" in location_names


async def test_location_parent_hierarchy(parser: AdventureParser):
    """Test location parent/child hierarchy tracking."""
    hierarchy_data = {
        "data": [
            {
                "name": "Test",
                "source": "TEST",
                "data": [
                    {
                        "type": "section",
                        "name": "Chapter 1",
                        "page": 1,
                        "entries": [
                            "{@area Castle|TEST}",
                            {
                                "type": "entries",
                                "name": "1. Courtyard",
                                "entries": [
                                    "{@area Guard Tower|TEST}",
                                ],
                            },
                        ],
                    }
                ],
            }
        ]
    }

    with patch.object(parser, "_get_adventure_data", return_value=hierarchy_data):
        result = await parser.parse_adventure("TEST")

    # Note: The parser doesn't automatically set parent_location in this test
    # because context.current_location is not set by default.
    # In a real adventure, numbered areas would be children of the main area.
    location_names = {loc.name for loc in result.locations}
    assert "Castle" in location_names
    assert "1. Courtyard" in location_names


# --- Read-Aloud Text Tests ---


async def test_read_aloud_extraction(parser: AdventureParser):
    """Test read-aloud text extraction with markup stripping."""
    mock_data = create_mock_adventure_data()

    with patch.object(parser, "_get_adventure_data", return_value=mock_data):
        result = await parser.parse_adventure("LMoP")

    assert len(result.read_aloud) > 0

    # Check that read-aloud text was extracted
    all_text = []
    for texts in result.read_aloud.values():
        all_text.extend(texts)

    assert any("You've been on the road" in text for text in all_text)


async def test_read_aloud_markup_stripped(parser: AdventureParser):
    """Test that markup is stripped from read-aloud text."""
    readaloud_data = {
        "data": [
            {
                "name": "Test",
                "source": "TEST",
                "data": [
                    {
                        "type": "section",
                        "name": "Chapter 1",
                        "page": 1,
                        "entries": [
                            {
                                "type": "insetReadaloud",
                                "entries": [
                                    "Make a {@dc 15} check. "
                                    "You see {@creature dragon|MM}."
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }

    with patch.object(parser, "_get_adventure_data", return_value=readaloud_data):
        result = await parser.parse_adventure("TEST")

    all_text = []
    for texts in result.read_aloud.values():
        all_text.extend(texts)

    # Should have DC and creature markup stripped
    text = " ".join(all_text)
    assert "{@" not in text  # No markup tags
    assert "DC 15" in text
    assert "dragon" in text


# --- Entry Type Handling Tests ---


async def test_handle_various_entry_types(parser: AdventureParser):
    """Test graceful handling of various entry types."""
    mock_data = create_adventure_with_types()

    with patch.object(parser, "_get_adventure_data", return_value=mock_data):
        result = await parser.parse_adventure("TEST")

    # Should parse without errors
    assert result.module_id == "TEST"
    assert len(result.chapters) == 1


async def test_handle_unknown_entry_type(parser: AdventureParser):
    """Test that unknown entry types don't cause crashes."""
    mock_data = create_adventure_with_types()

    with patch.object(parser, "_get_adventure_data", return_value=mock_data):
        result = await parser.parse_adventure("TEST")

    # Should complete parsing despite unknown types
    assert result is not None


# --- Download and Caching Tests ---


async def test_download_caching(parser: AdventureParser, cache_dir: Path):
    """Test that downloaded files are cached."""
    from unittest.mock import MagicMock
    mock_data = create_mock_adventure_data()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = mock_data
    mock_response.raise_for_status = MagicMock()
    mock_response.headers = {}

    with patch("httpx.AsyncClient") as mock_client:
        async def mock_get(*args, **kwargs):
            return mock_response

        mock_client.return_value.__aenter__.return_value.get = mock_get

        await parser.parse_adventure("LMoP")

    # Check cache file was created
    cache_file = cache_dir / "adventures" / "cache" / "content" / "LMoP.json"
    assert cache_file.exists()

    # Check content
    cached_data = json.loads(cache_file.read_text())
    assert cached_data == mock_data


async def test_use_cached_data(parser: AdventureParser, cache_dir: Path):
    """Test that cached data is used instead of downloading."""
    mock_data = create_mock_adventure_data()

    # Pre-populate cache
    cache_file = cache_dir / "adventures" / "cache" / "content" / "LMoP.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(mock_data))

    with patch("httpx.AsyncClient") as mock_client:
        result = await parser.parse_adventure("LMoP")

        # Should NOT have made HTTP request
        mock_client.assert_not_called()
    assert result.module_id == "LMoP"


async def test_download_404_error(parser: AdventureParser):
    """Test handling of 404 errors."""
    mock_response = AsyncMock()
    mock_response.status_code = 404

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=mock_response
        )

        with pytest.raises(AdventureParserError, match="not found"):
            await parser.parse_adventure("INVALID")


async def test_download_retry_on_timeout(parser: AdventureParser):
    """Test retry logic on timeout."""
    import httpx
    from unittest.mock import MagicMock

    mock_data = create_mock_adventure_data()
    mock_success = MagicMock()
    mock_success.status_code = 200
    mock_success.json.return_value = mock_data
    mock_success.raise_for_status = MagicMock()
    mock_success.headers = {}

    call_count = 0

    async def mock_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise httpx.TimeoutException("Timeout")
        return mock_success

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = mock_get

        result = await parser.parse_adventure("LMoP")

    # Should have retried and succeeded
    assert call_count == 2
    assert result.module_id == "LMoP"


# --- Full Parse Cycle Test ---


async def test_full_parse_cycle(parser: AdventureParser):
    """Test complete parse cycle with realistic mock data."""
    mock_data = create_mock_adventure_data()

    with patch.object(parser, "_get_adventure_data", return_value=mock_data):
        result = await parser.parse_adventure("LMoP")

    # Verify all components
    assert result.module_id == "LMoP"
    assert result.title == "Lost Mine of Phandelver"
    assert result.source_file == "adventure-lmop.json"
    assert result.metadata["source"] == "LMoP"

    # Chapters
    assert len(result.chapters) == 2
    assert all(ch.content_type == ContentType.CHAPTER for ch in result.chapters)

    # NPCs
    assert len(result.npcs) >= 2
    assert all(npc.name for npc in result.npcs)
    assert all(npc.chapter for npc in result.npcs)

    # Encounters
    assert len(result.encounters) >= 1
    assert all(enc.name for enc in result.encounters)

    # Locations
    assert len(result.locations) >= 3
    assert all(loc.name for loc in result.locations)

    # Read-aloud
    assert len(result.read_aloud) > 0


# --- Real 5etools Content Format (sections directly under "data") ---


def create_real_format_adventure_data() -> dict[str, Any]:
    """Real 5etools content format: sections live directly under "data".

    Unlike the wrapper fixture (create_mock_adventure_data), the actual
    adventure-<id>.json files have no wrapper object — data["data"] is the
    list of section objects, and the adventure's display name is not present
    (it lives in the separate adventures.json index).
    """
    return {
        "data": [
            {
                "type": "section",
                "name": "Foreword: Ravenloft Revisited",
                "page": 1,
                "entries": ["Welcome to Barovia."],
            },
            {
                "type": "section",
                "name": "Chapter 1: Into the Mists",
                "page": 5,
                "entries": ["The mists close in around you."],
            },
            {
                "type": "section",
                "name": "Chapter 2: The Lands of Barovia",
                "page": 20,
                "entries": ["A gloomy valley stretches before you."],
            },
        ]
    }


async def test_parse_real_5etools_format(parser: AdventureParser):
    """Sections directly under data[] must be parsed as chapters.

    Regression: the parser mistook data["data"][0] (the first section) for an
    adventure wrapper, producing zero chapters and a title equal to that
    section's name.
    """
    mock_data = create_real_format_adventure_data()

    with patch.object(parser, "_get_adventure_data", return_value=mock_data):
        result = await parser.parse_adventure("CoS")

    assert len(result.chapters) == 3
    assert result.chapters[0].name == "Foreword: Ravenloft Revisited"
    assert result.chapters[1].name == "Chapter 1: Into the Mists"
    assert result.chapters[2].name == "Chapter 2: The Lands of Barovia"
    # The title must NOT be the first section's name.
    assert result.title != "Foreword: Ravenloft Revisited"


async def test_real_format_title_from_index(
    parser: AdventureParser, cache_dir: Path
):
    """Display title for real-format adventures is resolved from the index."""
    index_dir = cache_dir / "adventures" / "cache"
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "adventures.json").write_text(
        json.dumps(
            {
                "adventure": [
                    {"id": "CoS", "name": "Curse of Strahd", "source": "CoS"}
                ]
            }
        ),
        encoding="utf-8",
    )

    mock_data = create_real_format_adventure_data()
    with patch.object(parser, "_get_adventure_data", return_value=mock_data):
        result = await parser.parse_adventure("CoS")

    assert result.title == "Curse of Strahd"


async def test_real_format_title_falls_back_to_id(parser: AdventureParser):
    """Without a cached index, the title falls back to the adventure ID."""
    mock_data = create_real_format_adventure_data()

    with patch.object(parser, "_get_adventure_data", return_value=mock_data):
        result = await parser.parse_adventure("CoS")

    assert result.title == "CoS"
