"""
Adventure content parser for 5etools JSON entries.

Downloads individual adventure JSON files from 5etools GitHub mirror
and converts the recursive entry format into dm20-protocol's ModuleStructure.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from dm20_protocol.claudmaster.models.module import (
    ContentType,
    EncounterReference,
    LocationReference,
    ModuleElement,
    ModuleStructure,
    NPCReference,
)
from dm20_protocol.rulebooks.sources.fivetools_utils import (
    convert_5etools_markup,
    render_entries,
)

logger = logging.getLogger("dm20-protocol")

# Base URL for adventure JSON files
ADVENTURE_BASE_URL = (
    "https://raw.githubusercontent.com/5etools-mirror-3/"
    "5etools-src/main/data/adventure/adventure-{id}.json"
)

DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0

# Regex patterns for extraction
_CREATURE_TAG_RE = re.compile(r"\{@creature\s+([^}|]+?)(?:\|[^}]*)?\}")
_AREA_TAG_RE = re.compile(r"\{@area\s+([^}|]+?)(?:\|[^}]*)?\}")
_NUMBERED_AREA_RE = re.compile(r"^(\d+[A-Za-z]?)\.\s+(.+)")


class AdventureParserError(Exception):
    """Error parsing adventure content."""

    pass


@dataclass
class ParserContext:
    """Context accumulated during recursive entry parsing.

    Tracks current position in hierarchy and extracted references.
    """

    current_chapter: str = ""
    current_section: str = ""
    current_page: int = 0
    current_location: str = ""
    text_buffer: list[str] = field(default_factory=list)
    npcs: dict[str, NPCReference] = field(default_factory=dict)
    encounters: list[EncounterReference] = field(default_factory=list)
    locations: dict[str, LocationReference] = field(default_factory=dict)
    read_aloud: dict[str, list[str]] = field(default_factory=dict)

    def append_text(self, text: str) -> None:
        """Append text to buffer, stripping markup."""
        if text:
            self.text_buffer.append(convert_5etools_markup(text))

    def get_section_id(self) -> str:
        """Generate unique section ID from current context."""
        if self.current_section:
            return f"{self.current_chapter}::{self.current_section}"
        return self.current_chapter

    def extract_creature_refs(self, text: str) -> list[str]:
        """Extract creature names from {@creature ...} tags."""
        return _CREATURE_TAG_RE.findall(text)

    def extract_area_refs(self, text: str) -> list[str]:
        """Extract area names from {@area ...} tags."""
        return _AREA_TAG_RE.findall(text)


class AdventureParser:
    """Parser for 5etools adventure JSON files.

    Downloads adventure content on demand, caches locally, and converts
    the recursive entry structure into dm20-protocol's ModuleStructure.

    Args:
        cache_dir: Directory for cached adventure files.
    """

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir / "adventures" / "cache" / "content"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    async def parse_adventure(self, adventure_id: str) -> ModuleStructure:
        """Parse an adventure by ID.

        Downloads the adventure JSON if not cached, then parses all
        data sections into a ModuleStructure.

        Args:
            adventure_id: Short adventure ID (e.g. 'CoS', 'LMoP').

        Returns:
            Parsed module structure with chapters, NPCs, encounters, locations.

        Raises:
            AdventureParserError: If download or parsing fails.
        """
        adventure_data = await self._get_adventure_data(adventure_id)
        return self._parse_adventure_data(adventure_id, adventure_data)

    async def _get_adventure_data(self, adventure_id: str) -> dict[str, Any]:
        """Get adventure data from cache or by downloading.

        Args:
            adventure_id: Short adventure ID.

        Returns:
            Parsed JSON data.

        Raises:
            AdventureParserError: If download or parsing fails.
        """
        # Normalize to lowercase for URL/cache consistency
        normalized_id = adventure_id.lower()
        cache_file = self.cache_dir / f"{normalized_id}.json"

        # Return cached if available
        if cache_file.exists():
            try:
                return json.loads(cache_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                logger.warning(
                    f"Corrupt cache for {adventure_id}, re-downloading: {e}"
                )

        # Download fresh copy
        return await self._download_adventure(adventure_id)

    async def _download_adventure(self, adventure_id: str) -> dict[str, Any]:
        """Download adventure JSON with retry logic.

        Args:
            adventure_id: Short adventure ID.

        Returns:
            Parsed JSON data.

        Raises:
            AdventureParserError: If download fails after retries.
        """
        # Normalize to lowercase for URL consistency (5etools uses lowercase IDs)
        normalized_id = adventure_id.lower()
        url = ADVENTURE_BASE_URL.format(id=normalized_id)
        last_error: Exception | None = None

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            for attempt in range(MAX_RETRIES):
                try:
                    response = await client.get(url)

                    if response.status_code == 429:
                        wait = RETRY_BACKOFF**attempt
                        logger.warning(f"Rate limited, waiting {wait}s")
                        await asyncio.sleep(wait)
                        continue

                    if response.status_code == 404:
                        raise AdventureParserError(
                            f"Adventure {adventure_id} not found"
                        )

                    response.raise_for_status()
                    data = response.json()

                    # Write to cache
                    cache_file = self.cache_dir / f"{normalized_id}.json"
                    cache_file.write_text(
                        json.dumps(data, indent=2), encoding="utf-8"
                    )

                    logger.info(f"Downloaded adventure {adventure_id}")
                    return data

                except httpx.TimeoutException as e:
                    logger.warning(
                        f"Timeout downloading {adventure_id}, "
                        f"attempt {attempt + 1}/{MAX_RETRIES}"
                    )
                    last_error = e
                    await asyncio.sleep(RETRY_BACKOFF**attempt)

                except httpx.HTTPStatusError as e:
                    if e.response.status_code >= 500:
                        logger.warning(
                            f"Server error {e.response.status_code}, "
                            f"attempt {attempt + 1}/{MAX_RETRIES}"
                        )
                        last_error = e
                        await asyncio.sleep(RETRY_BACKOFF**attempt)
                    else:
                        raise AdventureParserError(
                            f"HTTP error downloading {adventure_id}: {e}"
                        ) from e

        raise AdventureParserError(
            f"Failed to download {adventure_id} after {MAX_RETRIES} "
            f"retries: {last_error}"
        )

    def _parse_adventure_data(
        self, adventure_id: str, data: dict[str, Any] | list[dict[str, Any]]
    ) -> ModuleStructure:
        """Parse raw adventure JSON into ModuleStructure.

        Handles two 5etools JSON formats:
        - Nested (wrapper): {"data": [{"name": ..., "data": [...sections...]}]}
        - Flat (sections directly): [{"type": "section", ...}, ...]

        Args:
            adventure_id: Short adventure ID.
            data: Raw JSON data from 5etools (dict or list).

        Returns:
            Parsed module structure.
        """
        # Detect format: list = flat, dict = nested wrapper
        if isinstance(data, list):
            # Flat format: list of sections directly
            title = adventure_id
            source = adventure_id

            # Check if first entry has adventure metadata
            if data and isinstance(data[0], dict):
                if data[0].get("type") == "section" and data[0].get("name"):
                    title = data[0].get("name", adventure_id)
                elif "adventure" in data[0]:
                    # Some flat formats have metadata in first entry
                    meta = data[0]["adventure"]
                    title = meta.get("name", adventure_id)
                    source = meta.get("source", adventure_id)

            data_entries = data
        else:
            inner = data.get("data", [])
            first = inner[0] if inner and isinstance(inner[0], dict) else {}
            if first.get("type") == "section":
                # Real 5etools content format: sections live directly under
                # "data". The content file carries no adventure title — that
                # lives in the separate adventures.json index.
                title = self._lookup_index_title(adventure_id) or adventure_id
                source = adventure_id
                data_entries = inner
            else:
                # Wrapper format: {"data": [{"name": ..., "data": [...]}]}
                title = first.get("name", adventure_id)
                source = first.get("source", adventure_id)
                data_entries = first.get("data", [])

        # Initialize parser context
        context = ParserContext()

        # Parse all data entries (chapters/sections)
        chapters: list[ModuleElement] = []

        for idx, entry in enumerate(data_entries):
            if isinstance(entry, dict) and entry.get("type") == "section":
                chapter = self._parse_chapter(entry, idx + 1, context)
                if chapter:
                    chapters.append(chapter)

        # Build module structure
        normalized_id = adventure_id.lower()
        return ModuleStructure(
            module_id=adventure_id,
            title=title,
            source_file=f"adventure-{normalized_id}.json",
            chapters=chapters,
            npcs=list(context.npcs.values()),
            encounters=context.encounters,
            locations=list(context.locations.values()),
            metadata={"source": source},
            read_aloud=context.read_aloud,
        )

    def _lookup_index_title(self, adventure_id: str) -> str | None:
        """Resolve an adventure's display title from the cached index.

        The 5etools content file (adventure-<id>.json) carries only section
        data, not the adventure's human-readable name. That name lives in the
        separate adventures.json index, which AdventureIndex caches alongside
        the content cache. Returns None if the index is unavailable so callers
        can fall back to the adventure ID.
        """
        index_file = self.cache_dir.parent / "adventures.json"
        if not index_file.exists():
            return None
        try:
            raw = json.loads(index_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

        adventure_id_lower = adventure_id.lower()
        for item in raw.get("adventure", []):
            if (
                isinstance(item, dict)
                and item.get("id", "").lower() == adventure_id_lower
            ):
                return item.get("name")
        return None

    def _parse_chapter(
        self, entry: dict[str, Any], chapter_num: int, context: ParserContext
    ) -> ModuleElement | None:
        """Parse a top-level section as a chapter.

        Args:
            entry: Section entry dict.
            chapter_num: Sequential chapter number.
            context: Parser context.

        Returns:
            ModuleElement for the chapter, or None if invalid.
        """
        name = entry.get("name", f"Chapter {chapter_num}")
        page = entry.get("page", chapter_num)

        # Update context
        context.current_chapter = name
        context.current_page = page

        # Parse subsections
        sub_entries = entry.get("entries", [])
        children: list[ModuleElement] = []

        for sub_entry in sub_entries:
            self._parse_entry(sub_entry, context)

            # If it's a subsection, create child element
            if isinstance(sub_entry, dict):
                if sub_entry.get("type") == "entries":
                    sub_name = sub_entry.get("name", "")
                    if sub_name:
                        child = ModuleElement(
                            name=sub_name,
                            content_type=ContentType.SECTION,
                            page_start=context.current_page,
                            parent=name,
                        )
                        children.append(child)

        chapter = ModuleElement(
            name=name,
            content_type=ContentType.CHAPTER,
            page_start=page,
            children=[ch.name for ch in children],
        )

        return chapter

    def _parse_entry(self, entry: Any, context: ParserContext) -> None:
        """Recursively parse an entry and its children.

        Dispatches to type-specific handlers based on entry.type.

        Args:
            entry: Entry to parse (string or dict).
            context: Parser context.
        """
        if isinstance(entry, str):
            context.append_text(entry)
            self._extract_inline_refs(entry, context)

        elif isinstance(entry, dict):
            entry_type = entry.get("type", "")
            handler = self._get_handler(entry_type)
            handler(entry, context)

        elif isinstance(entry, list):
            for item in entry:
                self._parse_entry(item, context)

    def _get_handler(self, entry_type: str):
        """Get handler function for entry type."""
        return getattr(
            self, f"_handle_{entry_type}", self._handle_unknown
        )

    def _handle_entries(self, entry: dict[str, Any], context: ParserContext) -> None:
        """Handle nested entries (subsections)."""
        old_section = context.current_section
        section_name = entry.get("name", "")

        if section_name:
            context.current_section = section_name

            # Check if this section name is a numbered area (e.g., "1. Town Square")
            match = _NUMBERED_AREA_RE.match(section_name.strip())
            if match:
                self._add_location(section_name, context)

        # Recurse into nested entries
        for sub_entry in entry.get("entries", []):
            self._parse_entry(sub_entry, context)

        # Restore previous section
        context.current_section = old_section

    def _handle_section(self, entry: dict[str, Any], context: ParserContext) -> None:
        """Handle section entry (same as entries)."""
        self._handle_entries(entry, context)

    def _handle_inset(self, entry: dict[str, Any], context: ParserContext) -> None:
        """Handle inset boxes (DM notes)."""
        name = entry.get("name", "")
        if name:
            context.append_text(f"[{name}]")

        for sub_entry in entry.get("entries", []):
            self._parse_entry(sub_entry, context)

    def _handle_insetReadaloud(
        self, entry: dict[str, Any], context: ParserContext
    ) -> None:
        """Handle read-aloud text boxes."""
        section_id = context.get_section_id()
        read_aloud_texts = context.read_aloud.setdefault(section_id, [])

        # Extract all text from entries
        for sub_entry in entry.get("entries", []):
            if isinstance(sub_entry, str):
                clean_text = convert_5etools_markup(sub_entry)
                read_aloud_texts.append(clean_text)
            elif isinstance(sub_entry, dict):
                # Recursively render nested entries
                rendered = render_entries([sub_entry])
                read_aloud_texts.extend(rendered)

    def _handle_list(self, entry: dict[str, Any], context: ParserContext) -> None:
        """Handle list entries."""
        for item in entry.get("items", []):
            if isinstance(item, str):
                context.append_text(f"- {item}")
            elif isinstance(item, dict):
                self._parse_entry(item, context)

    def _handle_table(self, entry: dict[str, Any], context: ParserContext) -> None:
        """Handle table entries."""
        caption = entry.get("caption", "")
        if caption:
            context.append_text(f"[Table: {caption}]")

            # Check for encounter tables
            if "encounter" in caption.lower():
                self._extract_encounter_from_table(entry, context)

    def _handle_quote(self, entry: dict[str, Any], context: ParserContext) -> None:
        """Handle quote entries."""
        for sub_entry in entry.get("entries", []):
            self._parse_entry(sub_entry, context)

    def _handle_statblock(
        self, entry: dict[str, Any], context: ParserContext
    ) -> None:
        """Handle statblock references."""
        creature_name = entry.get("name", "")
        if creature_name:
            self._add_npc(creature_name, context)

    def _handle_statblockInline(
        self, entry: dict[str, Any], context: ParserContext
    ) -> None:
        """Handle inline statblocks."""
        creature_name = entry.get("name", "")
        if creature_name:
            self._add_npc(creature_name, context)

    def _handle_image(self, entry: dict[str, Any], context: ParserContext) -> None:
        """Handle image entries (skip)."""
        pass

    def _handle_gallery(self, entry: dict[str, Any], context: ParserContext) -> None:
        """Handle gallery entries (skip)."""
        pass

    def _handle_flowchart(
        self, entry: dict[str, Any], context: ParserContext
    ) -> None:
        """Handle flowchart entries (skip)."""
        pass

    def _handle_flowBlock(
        self, entry: dict[str, Any], context: ParserContext
    ) -> None:
        """Handle flowBlock entries (skip)."""
        pass

    def _handle_unknown(self, entry: dict[str, Any], context: ParserContext) -> None:
        """Handle unknown entry types gracefully."""
        entry_type = entry.get("type", "unknown")
        logger.debug(f"Unknown entry type: {entry_type}")

        # Try to extract nested entries anyway
        if "entries" in entry:
            for sub_entry in entry["entries"]:
                self._parse_entry(sub_entry, context)

    def _extract_inline_refs(self, text: str, context: ParserContext) -> None:
        """Extract creature and area references from inline tags.

        Args:
            text: Text containing {@creature ...} or {@area ...} tags.
            context: Parser context.
        """
        # Extract creatures
        for creature_name in context.extract_creature_refs(text):
            self._add_npc(creature_name, context)

        # Extract areas
        for area_name in context.extract_area_refs(text):
            self._add_location(area_name, context)

        # Check for numbered areas
        match = _NUMBERED_AREA_RE.match(text.strip())
        if match:
            area_num = match.group(1)
            area_name = match.group(2)
            full_name = f"{area_num}. {area_name}"
            self._add_location(full_name, context)

    def _add_npc(self, name: str, context: ParserContext) -> None:
        """Add NPC reference with deduplication.

        Args:
            name: NPC name.
            context: Parser context.
        """
        # Deduplicate: only add if not seen before
        if name not in context.npcs:
            npc = NPCReference(
                name=name,
                location=context.current_location or None,
                chapter=context.current_chapter,
                page=context.current_page,
            )
            context.npcs[name] = npc

    def _add_location(self, name: str, context: ParserContext) -> None:
        """Add location reference with parent tracking.

        Args:
            name: Location name.
            context: Parser context.
        """
        if name not in context.locations:
            location = LocationReference(
                name=name,
                chapter=context.current_chapter,
                page=context.current_page,
                parent_location=context.current_location or None,
            )
            context.locations[name] = location

            # Update parent's sub_locations if we have a parent
            if context.current_location:
                parent = context.locations.get(context.current_location)
                if parent and name not in parent.sub_locations:
                    parent.sub_locations.append(name)

    def _extract_encounter_from_table(
        self, table_entry: dict[str, Any], context: ParserContext
    ) -> None:
        """Extract encounter from table entry.

        Args:
            table_entry: Table entry dict.
            context: Parser context.
        """
        caption = table_entry.get("caption", "")
        encounter_type = "combat"

        # Simple type inference from caption
        caption_lower = caption.lower()
        if any(word in caption_lower for word in ["social", "persuasion", "dialogue"]):
            encounter_type = "social"
        elif any(word in caption_lower for word in ["trap", "puzzle", "exploration"]):
            encounter_type = "exploration"

        encounter = EncounterReference(
            name=caption,
            location=context.current_location or context.current_section,
            chapter=context.current_chapter,
            page=context.current_page,
            encounter_type=encounter_type,
        )
        context.encounters.append(encounter)


__all__ = [
    "AdventureParser",
    "AdventureParserError",
]
