"""
Storage layer for the D&D MCP Server.
Handles persistence of campaign data to JSON files.
"""

import asyncio
import logging
import shutil
import shortuuid
import json
from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from .models import (
    Campaign, Character, NPC, Location, Quest, CombatEncounter,
    SessionNote, GameState, AdventureEvent
)
from .rulebooks.manager import RulebookManager
from .library.manager import LibraryManager
from .library.bindings import LibraryBindings
from .consistency.discovery import DiscoveryTracker

logger = logging.getLogger("dm20-protocol")

logging.basicConfig(
    level=logging.DEBUG,
)


# UUID Helper function
def new_uuid() -> str:
    """Generate a new random 8-character UUID."""
    return shortuuid.random(length=8)


# Storage Format Enums
class StorageFormat:
    """Storage format constants for campaign data."""
    MONOLITHIC = "monolithic"  # Single JSON file per campaign
    SPLIT = "split"           # Directory with separate JSON files
    NOT_FOUND = "not_found"   # Campaign doesn't exist yet

class DnDStorage:
    """Handles storage and retrieval of D&D campaign data."""

    def __init__(self, data_dir: str | Path = "dnd_data"):
        self.data_dir = Path(data_dir)
        logger.debug(f"📂 Initializing DnDStorage with data_dir: {self.data_dir.resolve()}")
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Create subdirectories if necessary
        (self.data_dir / "campaigns").mkdir(exist_ok=True)
        (self.data_dir / "events").mkdir(exist_ok=True)
        logger.debug("📂 Storage subdirectories ensured.")

        self._current_campaign: Campaign | None = None
        self._events: list[AdventureEvent] = []

        # Performance optimization: indexes for O(1) character lookups
        self._character_id_index: dict[str, str] = {}  # id -> character_name
        self._player_name_index: dict[str, str] = {}  # player_name (lowercase) -> character_name

        # Batch mode flag to defer saves during bulk operations
        self._batch_mode: bool = False

        # Callback system for sheet sync and other listeners
        self._character_callbacks: list = []

        # Dirty tracking: hash of last saved campaign state
        self._campaign_hash: str = ""

        # Track storage format of current campaign
        self._current_format: str = StorageFormat.NOT_FOUND

        # Initialize split storage backend (without auto-loading campaigns)
        self._split_backend = SplitStorageBackend(data_dir=data_dir, auto_load=False)

        # Rulebook manager for the current campaign
        self._rulebook_manager: RulebookManager | None = None

        # Rules version for the current campaign ('2014' or '2024')
        self._rules_version: str = "2024"

        # Interaction mode for the current campaign
        self._interaction_mode: str = "classic"

        # Library manager (lazy initialization)
        self._library_manager: LibraryManager | None = None

        # Library bindings for the current campaign
        self._library_bindings: LibraryBindings | None = None

        # Discovery tracker for location/feature discovery state
        self._discovery_tracker: DiscoveryTracker | None = None

        # Fact graph for the current campaign (derived index over the journal;
        # claudmaster types, loaded lazily in _load_fact_graph)
        self._fact_db = None
        self._npc_knowledge_tracker = None
        self._party_knowledge = None
        self._timeline_tracker = None

        # Load existing data
        logger.debug("📂 Loading initial data...")
        self._load_current_campaign()
        self._load_events()
        logger.debug("✅ Initial data loaded.")

    @property
    def rulebook_manager(self) -> RulebookManager | None:
        """Get the rulebook manager for the current campaign."""
        return self._rulebook_manager

    @property
    def rules_version(self) -> str:
        """Get the rules version for the current campaign.

        Returns:
            Rules version string ('2014' or '2024'). Defaults to '2024'.
        """
        return self._rules_version

    @property
    def interaction_mode(self) -> str:
        """Get the interaction mode for the current campaign.

        Returns:
            Interaction mode string ('classic', 'narrated', or 'immersive').
            Defaults to 'classic'.
        """
        return self._interaction_mode

    @property
    def rulebooks_dir(self) -> Path | None:
        """Get the rulebooks directory for the current campaign.

        Returns:
            Path to rulebooks directory for split storage campaigns, None otherwise
        """
        if self._current_format == StorageFormat.SPLIT and self._current_campaign:
            campaign_dir = self._split_backend._get_campaign_dir(self._current_campaign.name)
            return campaign_dir / "rulebooks"
        return None

    @property
    def rulebook_cache_dir(self) -> Path:
        """Get the global rulebook cache directory.

        Returns:
            Path to the global cache directory (shared across campaigns)
        """
        cache_dir = self.data_dir / "rulebook_cache"
        cache_dir.mkdir(exist_ok=True)
        return cache_dir

    @property
    def library_dir(self) -> Path:
        """Get the global library directory.

        Returns:
            Path to the library directory (dnd_data/library/)
        """
        return self.data_dir / "library"

    @property
    def packs_dir(self) -> Path:
        """Get the directory for compendium pack files.

        Creates the directory if it does not exist.

        Returns:
            Path to the packs directory (dnd_data/packs/)
        """
        packs = self.data_dir / "packs"
        packs.mkdir(exist_ok=True)
        return packs

    @property
    def library_manager(self) -> LibraryManager:
        """Get the library manager, creating it if necessary.

        The library manager is lazily initialized on first access.
        It ensures the library directory structure exists.

        Returns:
            LibraryManager instance for the global library
        """
        if self._library_manager is None:
            self._library_manager = LibraryManager(self.library_dir)
            self._library_manager.ensure_directories()
            logger.debug(f"📚 Initialized LibraryManager at {self.library_dir}")
        return self._library_manager

    @property
    def library_bindings(self) -> LibraryBindings | None:
        """Get the library bindings for the current campaign.

        Returns:
            LibraryBindings instance if a campaign is loaded, None otherwise
        """
        return self._library_bindings

    def _get_campaign_file(self, campaign_name: str | None = None) -> Path:
        """Get the file path for a campaign."""
        if campaign_name is None and self._current_campaign:
            campaign_name = self._current_campaign.name
        if campaign_name is None:
            raise ValueError("No campaign name provided and no current campaign")

        safe_name = "".join(c for c in campaign_name if c.isalnum() or c in (' ', '-', '_', "'")).rstrip()
        return self.data_dir / "campaigns" / f"{safe_name}.json"

    def _get_events_file(self) -> Path:
        """Get the file path for adventure events."""
        return self.data_dir / "events" / "adventure_log.json"

    def _detect_campaign_format(self, campaign_name: str) -> str:
        """Detect the storage format of a campaign.

        Args:
            campaign_name: The name of the campaign to check

        Returns:
            One of StorageFormat constants: MONOLITHIC, SPLIT, or NOT_FOUND
        """
        safe_name = "".join(c for c in campaign_name if c.isalnum() or c in (' ', '-', '_', "'")).rstrip()

        dir_path = self.data_dir / "campaigns" / safe_name
        file_path = self.data_dir / "campaigns" / f"{safe_name}.json"

        # Check for split format (directory-based)
        if dir_path.is_dir():
            # Verify it has the campaign.json file to confirm it's a valid split campaign
            campaign_file = dir_path / "campaign.json"
            if campaign_file.exists():
                logger.debug(f"📂 Campaign '{campaign_name}' detected as SPLIT format")
                return StorageFormat.SPLIT
            else:
                logger.warning(f"⚠️ Directory exists for '{campaign_name}' but missing campaign.json")

        # Check for monolithic format (single file)
        if file_path.is_file():
            logger.debug(f"📂 Campaign '{campaign_name}' detected as MONOLITHIC format")
            return StorageFormat.MONOLITHIC

        # Campaign doesn't exist
        logger.debug(f"📂 Campaign '{campaign_name}' NOT FOUND")
        return StorageFormat.NOT_FOUND

    def _rebuild_character_index(self) -> None:
        """Rebuild character indexes for O(1) lookups by ID or player name."""
        self._character_id_index.clear()
        self._player_name_index.clear()
        if self._current_campaign:
            for name, char in self._current_campaign.characters.items():
                self._character_id_index[char.id] = name
                if char.player_name:
                    # Index by lowercase player name for case-insensitive matching
                    self._player_name_index[char.player_name.lower()] = name
            logger.debug(f"🔄 Character index rebuilt with {len(self._character_id_index)} ID entries, {len(self._player_name_index)} player entries")

    def _compute_campaign_hash(self) -> str:
        """Compute hash of campaign data for dirty tracking."""
        if not self._current_campaign:
            return ""
        campaign_data = self._current_campaign.model_dump(mode='json')
        return sha256(json.dumps(campaign_data, sort_keys=True).encode()).hexdigest()

    @contextmanager
    def batch_update(self):
        """Context manager for batch operations - defers saves until exit."""
        self._batch_mode = True
        try:
            yield
            # Sync with split backend if using split format
            if self._current_format == StorageFormat.SPLIT and self._current_campaign:
                self._split_backend._current_campaign = self._current_campaign
            self._save_campaign(force=True)  # Single save at the end
        finally:
            self._batch_mode = False

    def _save_campaign(self, force: bool = False) -> None:
        """Save the current campaign to disk using the appropriate format.

        Args:
            force: If True, bypass batch mode and dirty checking.
        """
        if not self._current_campaign:
            logger.debug("❌ No current campaign to save.")
            return

        # Skip save if in batch mode (unless forced)
        if self._batch_mode and not force:
            logger.debug("⏳ Batch mode active, deferring save...")
            return

        # Dirty tracking: skip save if unchanged (unless forced)
        if not force:
            current_hash = self._compute_campaign_hash()
            if current_hash == self._campaign_hash:
                logger.debug("✅ Campaign unchanged, skipping save.")
                return

        # Route to appropriate saver based on current format
        if self._current_format == StorageFormat.MONOLITHIC:
            self._save_monolithic_campaign()
        elif self._current_format == StorageFormat.SPLIT:
            self._save_split_campaign()
        else:
            # Default to monolithic for backward compatibility
            logger.warning(f"⚠️ Unknown storage format '{self._current_format}', defaulting to monolithic")
            self._save_monolithic_campaign()

        # Update hash after successful save
        self._campaign_hash = self._compute_campaign_hash()
        logger.debug(f"✅ Campaign '{self._current_campaign.name}' saved successfully.")

        # Notify listeners
        self._notify_character_callbacks("saved")

    def save(self) -> None:
        """Save the current campaign to disk.

        Public wrapper for _save_campaign, used by MCP tools that modify
        character objects directly (equip, spell slots, rests, etc.).
        """
        self._save_campaign()

    # --- Character Callback System ---

    def register_character_callback(self, callback) -> None:
        """Register a callback for character events.

        The callback is called as callback(action, *args) where action is
        one of: "saved", "deleted", "renamed".
        """
        self._character_callbacks.append(callback)

    def _notify_character_callbacks(self, action: str, *args) -> None:
        """Fire all registered character callbacks, catching exceptions."""
        for cb in self._character_callbacks:
            try:
                cb(action, *args)
            except Exception:
                logger.exception("Error in character callback (action=%s)", action)

    def find_character(self, name_or_id: str) -> Character | None:
        """Public wrapper for _find_character — find by name, ID, or player name."""
        return self._find_character(name_or_id)

    def _save_monolithic_campaign(self) -> None:
        """Save campaign as a single JSON file (legacy format)."""
        campaign_file = self._get_campaign_file()
        logger.debug(f"💾 Saving campaign '{self._current_campaign.name}' to {campaign_file} (monolithic)")
        logger.info(f"💾 Autosaving '{self._current_campaign.name}'")
        campaign_data = self._current_campaign.model_dump(mode='json')

        with open(campaign_file, 'w', encoding='utf-8') as f:
            json.dump(campaign_data, f, default=str)

    def _save_split_campaign(self) -> None:
        """Save campaign using split directory structure (new format)."""
        if not self._current_campaign:
            return

        logger.debug(f"💾 Saving campaign '{self._current_campaign.name}' using split format")

        # Sync current campaign to split backend
        self._split_backend._current_campaign = self._current_campaign

        # Use split backend to save all files
        self._split_backend.save_all(force=False)

        logger.debug(f"✅ Campaign '{self._current_campaign.name}' saved successfully (split format).")

    def _load_current_campaign(self):
        """Load the most recently used campaign."""
        logger.debug("📂 Attempting to load the most recent campaign...")
        campaigns_dir = self.data_dir / "campaigns"
        if not campaigns_dir.exists():
            logger.debug("❌ Campaigns directory does not exist. No campaign loaded.")
            return

        # Find the most recent campaign (file or directory)
        campaign_files = list(campaigns_dir.glob("*.json"))
        campaign_dirs = [d for d in campaigns_dir.iterdir() if d.is_dir()]

        if not campaign_files and not campaign_dirs:
            logger.debug("❌ No campaigns found.")
            return

        # Get most recent from both files and directories
        all_campaigns = []
        if campaign_files:
            all_campaigns.extend(campaign_files)
        if campaign_dirs:
            # For directories, check campaign.json modification time
            for d in campaign_dirs:
                campaign_file = d / "campaign.json"
                if campaign_file.exists():
                    all_campaigns.append(campaign_file)

        if not all_campaigns:
            logger.debug("❌ No valid campaigns found.")
            return

        # Sort by modification time and load the most recent
        latest_file = max(all_campaigns, key=lambda f: f.stat().st_mtime)
        logger.debug(f"📂 Most recent campaign file is '{latest_file.name}'.")

        # Determine campaign name from the file/directory
        if latest_file.name == "campaign.json":
            # Split format: campaign name is the parent directory
            campaign_name = latest_file.parent.name
        else:
            # Monolithic format: campaign name is the file stem
            campaign_name = latest_file.stem

        # Load campaign using the appropriate method
        try:
            self.load_campaign(campaign_name)
        except Exception as e:
            logger.error(f"❌ Error loading campaign '{campaign_name}': {e}")

    def _save_events(self):
        """Save adventure events to disk."""
        events_file = self._get_events_file()
        logger.debug(f"💾 Saving {len(self._events)} events to {events_file}...")
        events_data = [event.model_dump(mode='json') for event in self._events]

        with open(events_file, 'w', encoding='utf-8') as f:
            json.dump(events_data, f, default=str)
        logger.debug("✅ Events saved successfully.")

    def _load_events(self):
        """Load adventure events from disk."""
        logger.debug("📂 Attempting to load adventure events...")
        events_file = self._get_events_file()
        if not events_file.exists():
            logger.debug("❌ Adventure log file does not exist. No events loaded.")
            return

        try:
            with open(events_file, 'r', encoding='utf-8') as f:
                events_data = json.load(f)
            self._events = [AdventureEvent.model_validate(event) for event in events_data]
            logger.info(f"✅ Successfully loaded {len(self._events)} events.")
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"❌ Error loading events: {e}")

    # Campaign Management
    def create_campaign(self, name: str, description: str, dm_name: str | None = None, setting: str | Path | None = None, rules_version: str = "2024", interaction_mode: str = "classic") -> Campaign:
        """Create a new campaign using split storage format.

        Args:
            name: Campaign name
            description: Campaign description
            dm_name: Dungeon Master name
            setting: Campaign setting
            rules_version: D&D rules version ('2014' or '2024', default '2024')
            interaction_mode: Interaction mode ('classic', 'narrated', or 'immersive', default 'classic')
        """
        logger.info(f"✨ Creating new campaign: '{name}' (rules: {rules_version}, mode: {interaction_mode})")

        # Use split backend to create the campaign
        campaign = self._split_backend.create_campaign(
            name=name,
            description=description,
            dm_name=dm_name,
            setting=setting
        )

        # Sync to main storage
        self._current_campaign = campaign
        self._current_format = StorageFormat.SPLIT

        # Store rules_version and interaction_mode in campaign metadata
        self._rules_version = rules_version
        self._interaction_mode = interaction_mode

        # Create rulebooks directory structure
        campaign_dir = self._split_backend._get_campaign_dir(name)
        rulebooks_dir = campaign_dir / "rulebooks"
        rulebooks_dir.mkdir(exist_ok=True)
        (rulebooks_dir / "custom").mkdir(exist_ok=True)
        logger.debug(f"📂 Created rulebooks directory structure at {rulebooks_dir}")

        # Save rules_version and interaction_mode to campaign.json metadata
        self._save_rules_version(campaign_dir, rules_version)
        self._save_interaction_mode(campaign_dir, interaction_mode)

        # Rebuild indexes for new campaign
        self._rebuild_character_index()

        # Update campaign hash
        self._campaign_hash = self._compute_campaign_hash()

        # Initialize library bindings for the new campaign
        self._library_bindings = LibraryBindings(campaign_id=campaign.id)
        logger.debug(f"📚 Created empty library bindings for campaign '{name}'")

        # Initialize discovery tracker for the new campaign
        self._discovery_tracker = DiscoveryTracker(campaign_dir)
        logger.debug(f"Initialized empty DiscoveryTracker for campaign '{name}'")

        # Initialize fact graph for the new campaign
        self._load_fact_graph()

        # Initialize timeline for the new campaign — born anchored at the
        # epoch: a fresh campaign's Day 1 is a genuine anchor (DM2-6 spike)
        self._load_timeline_tracker()
        if self._timeline_tracker is not None:
            self._timeline_tracker.anchored = True
            self._timeline_tracker.save()

        logger.info(f"✅ Campaign '{name}' created and set as active using {self._current_format} format (rules: {rules_version}, mode: {interaction_mode}).")
        return campaign

    def get_current_campaign(self) -> Campaign | None:
        """Get the current campaign."""
        return self._current_campaign

    def list_campaigns(self) -> list[str]:
        """List all available campaigns (both monolithic and split formats)."""
        campaigns_dir = self.data_dir / "campaigns"
        if not campaigns_dir.exists():
            return []

        campaigns = []

        # Find monolithic campaigns (JSON files)
        for f in campaigns_dir.glob("*.json"):
            campaigns.append(f.stem)

        # Find split campaigns (directories with campaign.json)
        for d in campaigns_dir.iterdir():
            if d.is_dir():
                campaign_file = d / "campaign.json"
                if campaign_file.exists():
                    campaigns.append(d.name)

        return sorted(campaigns)

    def load_campaign(self, name: str) -> Campaign:
        """Load a specific campaign, automatically detecting format."""
        logger.info(f"📂 Attempting to load campaign: '{name}'")

        # Detect storage format
        storage_format = self._detect_campaign_format(name)

        if storage_format == StorageFormat.NOT_FOUND:
            logger.error(f"❌ Campaign '{name}' not found")
            raise FileNotFoundError(f"Campaign '{name}' not found")

        # Route to appropriate loader based on format
        if storage_format == StorageFormat.MONOLITHIC:
            campaign = self._load_monolithic_campaign(name)
        elif storage_format == StorageFormat.SPLIT:
            campaign = self._load_split_campaign(name)
            # Sync split backend with loaded campaign
            self._split_backend._current_campaign = campaign
        else:
            raise ValueError(f"Unknown storage format: {storage_format}")

        self._current_campaign = campaign
        self._current_format = storage_format
        self._rebuild_character_index()
        self._campaign_hash = self._compute_campaign_hash()

        # Load rules version and interaction mode from campaign metadata
        self._load_rules_version()
        self._load_interaction_mode()

        # Load RulebookManager if manifest exists (split campaigns only)
        self._load_rulebook_manager()

        # Load library bindings if they exist (split campaigns only)
        self._load_library_bindings()

        # Load enabled library content into RulebookManager
        self._load_library_content()

        # Load discovery tracker (split campaigns only)
        self._load_discovery_tracker()

        # Load fact graph (split campaigns only)
        self._load_fact_graph()

        # Load timeline tracker (split campaigns only)
        self._load_timeline_tracker()

        logger.info(f"✅ Successfully loaded campaign '{name}' using {storage_format} format (rules: {self._rules_version}).")
        return self._current_campaign

    def delete_campaign(self, name: str) -> str:
        """Delete a campaign from storage.

        Supports both monolithic (single JSON file) and split (directory) formats.
        If the deleted campaign is the currently active one, all state is cleared.

        Args:
            name: The name of the campaign to delete

        Returns:
            The name of the deleted campaign

        Raises:
            FileNotFoundError: If the campaign does not exist
        """
        storage_format = self._detect_campaign_format(name)

        if storage_format == StorageFormat.NOT_FOUND:
            raise FileNotFoundError(f"Campaign '{name}' not found")

        safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_', "'")).rstrip()

        if storage_format == StorageFormat.MONOLITHIC:
            file_path = self._get_campaign_file(name)
            file_path.unlink()
            logger.info(f"🗑️ Deleted monolithic campaign file: {file_path}")
        elif storage_format == StorageFormat.SPLIT:
            dir_path = self.data_dir / "campaigns" / safe_name
            # Safety check: path must be under campaigns directory
            campaigns_dir = (self.data_dir / "campaigns").resolve()
            if not dir_path.resolve().is_relative_to(campaigns_dir):
                raise ValueError(f"Unsafe path detected: {dir_path}")
            try:
                shutil.rmtree(dir_path)
            except OSError:
                # Retry: macOS race condition with file watchers / .DS_Store
                import time
                time.sleep(0.3)
                shutil.rmtree(dir_path)
            logger.info(f"🗑️ Deleted split campaign directory: {dir_path}")

        # If deleting the active campaign, clear all state
        if self._current_campaign and self._current_campaign.name == name:
            self._current_campaign = None
            self._current_format = StorageFormat.NOT_FOUND
            self._character_id_index.clear()
            self._player_name_index.clear()
            self._campaign_hash = ""
            self._rulebook_manager = None
            self._rules_version = "2024"
            self._interaction_mode = "classic"
            self._library_bindings = None
            self._discovery_tracker = None
            self._fact_db = None
            self._npc_knowledge_tracker = None
            self._party_knowledge = None
            self._timeline_tracker = None
            if hasattr(self, '_split_backend'):
                self._split_backend._current_campaign = None
            logger.info(f"🧹 Cleared active campaign state (was: '{name}')")

        return name

    def _save_rules_version(self, campaign_dir: Path, rules_version: str) -> None:
        """Save rules_version to the campaign.json metadata file.

        Updates the existing campaign.json with the rules_version field.

        Args:
            campaign_dir: Path to campaign directory
            rules_version: Rules version string ('2014' or '2024')
        """
        file_path = campaign_dir / "campaign.json"
        if not file_path.exists():
            logger.warning(f"Cannot save rules_version: {file_path} not found")
            return

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)

            metadata["rules_version"] = rules_version

            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2)

            logger.debug(f"💾 Saved rules_version '{rules_version}' to {file_path}")
        except Exception as e:
            logger.error(f"❌ Failed to save rules_version: {e}")

    def _load_rules_version(self) -> None:
        """Load rules_version from the current campaign's metadata.

        Reads the rules_version field from campaign.json.
        Defaults to '2024' if not present (backward compatible).
        """
        self._rules_version = "2024"  # Default

        if not self._current_campaign:
            return

        if self._current_format == StorageFormat.SPLIT:
            campaign_dir = self._split_backend._get_campaign_dir(self._current_campaign.name)
        else:
            return  # Monolithic campaigns don't support rules_version

        file_path = campaign_dir / "campaign.json"
        if not file_path.exists():
            return

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)

            self._rules_version = metadata.get("rules_version", "2024")
            logger.debug(f"📖 Loaded rules_version: {self._rules_version}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to load rules_version: {e}")

    VALID_INTERACTION_MODES = ("classic", "narrated", "immersive")

    def _save_interaction_mode(self, campaign_dir: Path, interaction_mode: str) -> None:
        """Save interaction_mode to the campaign.json metadata file.

        Updates the existing campaign.json with the interaction_mode field.

        Args:
            campaign_dir: Path to campaign directory
            interaction_mode: Mode string ('classic', 'narrated', or 'immersive')
        """
        file_path = campaign_dir / "campaign.json"
        if not file_path.exists():
            logger.warning(f"Cannot save interaction_mode: {file_path} not found")
            return

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)

            metadata["interaction_mode"] = interaction_mode

            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2)

            logger.debug(f"💾 Saved interaction_mode '{interaction_mode}' to {file_path}")
        except Exception as e:
            logger.error(f"❌ Failed to save interaction_mode: {e}")

    def _load_interaction_mode(self) -> None:
        """Load interaction_mode from the current campaign's metadata.

        Reads the interaction_mode field from campaign.json.
        Defaults to 'classic' if not present (backward compatible).
        """
        self._interaction_mode = "classic"  # Default

        if not self._current_campaign:
            return

        if self._current_format == StorageFormat.SPLIT:
            campaign_dir = self._split_backend._get_campaign_dir(self._current_campaign.name)
        else:
            return  # Monolithic campaigns don't support interaction_mode

        file_path = campaign_dir / "campaign.json"
        if not file_path.exists():
            return

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)

            mode = metadata.get("interaction_mode", "classic")
            if mode in self.VALID_INTERACTION_MODES:
                self._interaction_mode = mode
            else:
                logger.warning(f"⚠️ Invalid interaction_mode '{mode}', defaulting to 'classic'")
                self._interaction_mode = "classic"
            logger.debug(f"📖 Loaded interaction_mode: {self._interaction_mode}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to load interaction_mode: {e}")

    def set_interaction_mode(self, mode: str) -> None:
        """Change the interaction mode for the current campaign.

        Validates the mode, updates the in-memory state, and persists to disk.

        Args:
            mode: New interaction mode ('classic', 'narrated', or 'immersive')

        Raises:
            ValueError: If mode is invalid or no campaign is loaded.
        """
        if mode not in self.VALID_INTERACTION_MODES:
            raise ValueError(f"Invalid interaction_mode '{mode}'. Must be one of: {', '.join(self.VALID_INTERACTION_MODES)}")

        if not self._current_campaign:
            raise ValueError("No active campaign")

        self._interaction_mode = mode

        if self._current_format == StorageFormat.SPLIT:
            campaign_dir = self._split_backend._get_campaign_dir(self._current_campaign.name)
            self._save_interaction_mode(campaign_dir, mode)

        logger.info(f"🔄 Interaction mode changed to '{mode}'")

    def _load_rulebook_manager(self) -> None:
        """Load RulebookManager for the current campaign if manifest exists.

        Only applicable to split storage campaigns. If manifest doesn't exist,
        sets _rulebook_manager to None (backward compatible).
        """
        # Clear any existing manager
        self._rulebook_manager = None

        # Only load for split campaigns
        if self._current_format != StorageFormat.SPLIT or not self._current_campaign:
            return

        # Get campaign directory
        campaign_dir = self._split_backend._get_campaign_dir(self._current_campaign.name)
        manifest_path = campaign_dir / "rulebooks" / "manifest.json"

        # Check if manifest exists
        if not manifest_path.exists():
            logger.debug(f"No rulebook manifest found at {manifest_path}, skipping RulebookManager load")
            return

        # Load manager from manifest (async operation)
        try:
            # Execute the async factory method
            # Use get_event_loop() instead of asyncio.run() to avoid closing the loop
            # which would break subsequent async operations in the same process
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No event loop running - get or create one without closing it
                loop = asyncio.get_event_loop()

            self._rulebook_manager = loop.run_until_complete(
                RulebookManager.from_manifest(campaign_dir)
            )
            logger.info(f"✅ Loaded RulebookManager for campaign '{self._current_campaign.name}'")
        except Exception as e:
            logger.warning(f"⚠️ Failed to load RulebookManager: {e}")
            self._rulebook_manager = None

    def _load_library_bindings(self) -> None:
        """Load library bindings for the current campaign if they exist.

        Only applicable to split storage campaigns. If bindings file doesn't exist,
        creates an empty LibraryBindings object for the campaign.
        """
        # Clear any existing bindings
        self._library_bindings = None

        # Only load for split campaigns
        if self._current_format != StorageFormat.SPLIT or not self._current_campaign:
            return

        # Get bindings file path
        campaign_dir = self._split_backend._get_campaign_dir(self._current_campaign.name)
        bindings_path = campaign_dir / "rulebooks" / "library-bindings.json"

        # Check if bindings file exists
        if not bindings_path.exists():
            # Create empty bindings for the campaign
            self._library_bindings = LibraryBindings(campaign_id=self._current_campaign.id)
            logger.debug(f"No library bindings found, created empty bindings for campaign '{self._current_campaign.name}'")
            return

        # Load bindings from file
        try:
            with open(bindings_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._library_bindings = LibraryBindings.from_dict(data)
            logger.info(f"✅ Loaded library bindings for campaign '{self._current_campaign.name}' ({len(self._library_bindings.sources)} sources)")
        except Exception as e:
            logger.warning(f"⚠️ Failed to load library bindings: {e}")
            # Create empty bindings as fallback
            self._library_bindings = LibraryBindings(campaign_id=self._current_campaign.id)

    def _save_library_bindings(self) -> None:
        """Save library bindings for the current campaign.

        Only applicable to split storage campaigns.
        """
        if not self._library_bindings:
            logger.debug("No library bindings to save")
            return

        # Only save for split campaigns
        if self._current_format != StorageFormat.SPLIT or not self._current_campaign:
            logger.debug("Skipping library bindings save (not a split campaign)")
            return

        # Get bindings file path
        campaign_dir = self._split_backend._get_campaign_dir(self._current_campaign.name)
        rulebooks_dir = campaign_dir / "rulebooks"
        rulebooks_dir.mkdir(exist_ok=True)
        bindings_path = rulebooks_dir / "library-bindings.json"

        # Save bindings to file
        try:
            with open(bindings_path, 'w', encoding='utf-8') as f:
                json.dump(self._library_bindings.to_dict(), f, indent=2)
            logger.debug(f"💾 Saved library bindings to {bindings_path}")
        except Exception as e:
            logger.error(f"❌ Failed to save library bindings: {e}")

    def _load_library_content(self) -> None:
        """Load enabled library content into RulebookManager.

        Scans the library's extracted directory for content files belonging
        to enabled sources and loads them as CustomSources into the
        RulebookManager. This makes library content searchable and queryable
        through search_rules, get_class_info, etc.

        Library content is loaded with lower priority than campaign custom
        sources but higher priority than the SRD base content.

        Only applicable to split storage campaigns with both a RulebookManager
        and library bindings configured.
        """
        # Check prerequisites
        if not self._library_bindings:
            logger.debug("No library bindings, skipping library content load")
            return

        if not self._rulebook_manager:
            logger.debug("No RulebookManager, skipping library content load")
            return

        # Get library manager (creates if needed)
        library_manager = self.library_manager

        # Get custom sources for enabled content
        sources = library_manager.get_custom_sources_for_campaign(self._library_bindings)
        if not sources:
            logger.debug("No extracted library content found for enabled sources")
            return

        # Import CustomSource here to avoid circular imports
        from .rulebooks.sources.custom import CustomSource

        # Track loaded sources for logging
        loaded_count = 0
        failed_count = 0

        # Load each extracted content file
        for source_id, json_path in sources:
            try:
                # Create CustomSource with a unique ID that identifies it as library content
                custom_source_id = f"library:{source_id}:{json_path.stem}"
                custom_source = CustomSource(
                    path=json_path,
                    source_id=custom_source_id,
                )

                # Load the source (async operation)
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = asyncio.get_event_loop()

                loop.run_until_complete(self._rulebook_manager.load_source(custom_source))
                loaded_count += 1
                logger.debug(f"Loaded library source: {custom_source_id}")

            except Exception as e:
                failed_count += 1
                logger.warning(f"Failed to load library source {source_id}/{json_path.name}: {e}")

        if loaded_count > 0:
            logger.info(f"📚 Loaded {loaded_count} library content files into RulebookManager")
        if failed_count > 0:
            logger.warning(f"⚠️ Failed to load {failed_count} library content files")

    def _load_monolithic_campaign(self, name: str) -> Campaign:
        """Load a campaign from a single JSON file (legacy format)."""
        campaign_file = self._get_campaign_file(name)
        logger.debug(f"📂 Loading monolithic campaign from: {campaign_file}")

        with open(campaign_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        return Campaign.model_validate(data)

    def _load_split_campaign(self, name: str) -> Campaign:
        """Load a campaign from split directory structure (new format)."""
        logger.debug(f"📂 Loading split campaign: '{name}'")

        # Use split backend to load campaign
        campaign = self._split_backend.load_campaign(name)

        logger.debug(f"✅ Successfully loaded split campaign: '{name}'")
        return campaign

    def update_campaign(self, **kwargs):
        """Update campaign metadata."""
        if not self._current_campaign:
            raise ValueError("No current campaign")

        logger.info(f"📝 Updating campaign '{self._current_campaign.name}' with data: {kwargs}")
        for key, value in kwargs.items():
            if hasattr(self._current_campaign, key):
                logger.debug(f"📝 Updating {key} to {value}")
                setattr(self._current_campaign, key, value)

        self._current_campaign.updated_at = datetime.now()
        self._save_campaign()
        logger.info(f"✅ Campaign '{self._current_campaign.name}' updated.")

    # Character Management
    def add_character(self, character: Character) -> None:
        """Add a character to the current campaign."""
        if not self._current_campaign:
            raise ValueError("No current campaign")

        logger.info(f"➕ Adding character '{character.name}' to campaign '{self._current_campaign.name}'.")
        self._current_campaign.characters[character.name] = character
        # Update indexes for O(1) lookup by ID and player name
        self._character_id_index[character.id] = character.name
        if character.player_name:
            self._player_name_index[character.player_name.lower()] = character.name
        self._current_campaign.updated_at = datetime.now()
        self._save_campaign()
        logger.debug(f"✅ Character '{character.name}' added to campaign: '{self._current_campaign.name}'.")

    def _find_character(self, name_or_id_or_player: str) -> Character | None:
        """Find a character by name, ID, or player name using O(1) index lookup.

        Lookup priority:
        1. Character name (exact match)
        2. Character ID (8-char UUID)
        3. Player name (case-insensitive)
        """
        if not self._current_campaign:
            e = ValueError("❌ No active campaign! Wtf???")
            logger.error(e)
            raise e

        # Direct character name lookup - O(1)
        if name_or_id_or_player in self._current_campaign.characters:
            return self._current_campaign.characters[name_or_id_or_player]

        # ID lookup via index - O(1)
        if name_or_id_or_player in self._character_id_index:
            char_name = self._character_id_index[name_or_id_or_player]
            return self._current_campaign.characters.get(char_name)

        # Player name lookup (case-insensitive) - O(1)
        player_key = name_or_id_or_player.lower()
        if player_key in self._player_name_index:
            char_name = self._player_name_index[player_key]
            return self._current_campaign.characters.get(char_name)

        return None

    def get_character(self, name_or_id: str) -> Character | None:
        """Get a character by name or ID."""
        char = self._find_character(name_or_id)
        if not char:
            logger.error(f"❌ Character '{name_or_id}' not found!")
            return None
        logger.debug(f"✅ Found character '{char.name}'")
        return char

    def update_character(self, name_or_id: str, **kwargs) -> None:
        """Update a character's data."""
        if not self._current_campaign:
            raise ValueError("No current campaign")

        logger.info(f"📝 Attempting to update character '{name_or_id}' with data: {kwargs}")
        character = self._find_character(name_or_id)
        if not character:
            e = ValueError(f"❌ Character '{name_or_id}' not found!")
            logger.error(e)
            raise e

        original_name = character.name
        original_player_name = character.player_name
        new_name = kwargs.get("name")
        new_player_name = kwargs.get("player_name")

        for key, value in kwargs.items():
            if hasattr(character, key):
                logger.debug(f"📝 Updating character '{original_name}': {key} -> {value}")
                setattr(character, key, value)

        character.updated_at = datetime.now()

        # Update character name in dict and indexes if changed
        if new_name and new_name != original_name:
            logger.debug(f"🏷️ Character name changed from '{original_name}' to '{new_name}'. Updating dictionary key.")
            self._current_campaign.characters[new_name] = self._current_campaign.characters.pop(original_name)
            self._character_id_index[character.id] = new_name
            # Update player name index to point to new character name
            if character.player_name:
                self._player_name_index[character.player_name.lower()] = new_name

        # Update player name index if player_name changed
        if new_player_name != original_player_name:
            # Remove old player name from index
            if original_player_name:
                self._player_name_index.pop(original_player_name.lower(), None)
            # Add new player name to index
            if new_player_name:
                char_name = new_name or original_name
                self._player_name_index[new_player_name.lower()] = char_name

        self._current_campaign.updated_at = datetime.now()
        self._save_campaign()

        # Notify rename if name changed
        if new_name and new_name != original_name:
            self._notify_character_callbacks("renamed", original_name, new_name, character)

        logger.info(f"✅ Character '{new_name or original_name}' updated successfully.")

    def remove_character(self, name_or_id: str) -> None:
        """Remove a character from the campaign."""
        if not self._current_campaign:
            raise ValueError("No current campaign")

        logger.debug(f"🗑️ Attempting to remove character '{name_or_id}'.")
        character_to_remove = self._find_character(name_or_id)
        if character_to_remove:
            char_name = character_to_remove.name
            char_id = character_to_remove.id
            player_name = character_to_remove.player_name
            logger.debug(f"🗑️ Found character '{char_name}' to remove.")
            # Remove from dict and all indexes
            del self._current_campaign.characters[char_name]
            self._character_id_index.pop(char_id, None)
            if player_name:
                self._player_name_index.pop(player_name.lower(), None)
            self._current_campaign.updated_at = datetime.now()
            self._save_campaign()
            self._notify_character_callbacks("deleted", char_name)
            logger.info(f"✅ Character '{char_name}' removed successfully.")
        else:
            logger.warning(f"⚠️ Character '{name_or_id}' not found for removal.")

    def list_characters(self) -> list[str]:
        """List all character names in the current campaign."""
        if not self._current_campaign:
            return []
        return list(self._current_campaign.characters.keys())

    def list_characters_detailed(self) -> list[Character]:
        """Return all characters without redundant lookups - O(n) instead of O(2n)."""
        if not self._current_campaign:
            return []
        return list(self._current_campaign.characters.values())

    # NPC Management
    def add_npc(self, npc: NPC) -> None:
        """Add an NPC to the current campaign."""
        if not self._current_campaign:
            raise ValueError("No current campaign")

        self._current_campaign.npcs[npc.name] = npc
        self._current_campaign.updated_at = datetime.now()
        self._save_campaign()

    def get_npc(self, name: str) -> NPC | None:
        """Get an NPC by name."""
        if not self._current_campaign:
            return None
        return self._current_campaign.npcs.get(name)

    def list_npcs(self) -> list[str]:
        """List all NPC names."""
        if not self._current_campaign:
            return []
        return list(self._current_campaign.npcs.keys())

    def list_npcs_detailed(self) -> list[NPC]:
        """Return all NPCs without redundant lookups."""
        if not self._current_campaign:
            return []
        return list(self._current_campaign.npcs.values())

    # Location Management
    def add_location(self, location: Location) -> None:
        """Add a location to the current campaign."""
        if not self._current_campaign:
            raise ValueError("No current campaign")

        self._current_campaign.locations[location.name] = location
        self._current_campaign.updated_at = datetime.now()
        self._save_campaign()

    def get_location(self, name: str) -> Location | None:
        """Get a location by name."""
        if not self._current_campaign:
            return None
        return self._current_campaign.locations.get(name)

    def list_locations(self) -> list[str]:
        """List all location names."""
        if not self._current_campaign:
            return []
        return list(self._current_campaign.locations.keys())

    def list_locations_detailed(self) -> list[Location]:
        """Return all locations without redundant lookups."""
        if not self._current_campaign:
            return []
        return list(self._current_campaign.locations.values())

    # Quest Management
    def add_quest(self, quest: Quest) -> None:
        """Add a quest to the current campaign."""
        if not self._current_campaign:
            raise ValueError("No current campaign")

        self._current_campaign.quests[quest.title] = quest
        self._current_campaign.updated_at = datetime.now()
        self._save_campaign()

    def get_quest(self, title: str) -> Quest | None:
        """Get a quest by title."""
        if not self._current_campaign:
            return None
        return self._current_campaign.quests.get(title)

    def update_quest_status(self, title: str, status: str) -> None:
        """Update a quest's status."""
        quest = self.get_quest(title)
        if quest:
            quest.status = status
            self._current_campaign.updated_at = datetime.now()  # type: ignore
            self._save_campaign()

    def list_quests(self, status: str | None = None) -> list[str]:
        """List quest titles, optionally filtered by status."""
        if not self._current_campaign:
            return []

        quests = self._current_campaign.quests
        if status:
            return [title for title, quest in quests.items() if quest.status == status]
        return list(quests.keys())

    # Game State Management
    def update_game_state(self, **kwargs) -> None:
        """Update the game state."""
        if not self._current_campaign:
            raise ValueError("No current campaign")

        game_state = self._current_campaign.game_state
        for key, value in kwargs.items():
            if hasattr(game_state, key):
                setattr(game_state, key, value)

        game_state.updated_at = datetime.now()
        self._current_campaign.updated_at = datetime.now()
        self._save_campaign()

    def get_game_state(self) -> GameState | None:
        """Get the current game state."""
        if not self._current_campaign:
            return None
        return self._current_campaign.game_state

    # Session Management
    def add_session_note(self, session_note: SessionNote) -> None:
        """Add a session note."""
        if not self._current_campaign:
            raise ValueError("No current campaign")

        self._current_campaign.sessions.append(session_note)
        self._current_campaign.updated_at = datetime.now()
        self._save_campaign()

    def get_sessions(self) -> list[SessionNote]:
        """Get all session notes."""
        if not self._current_campaign:
            return []
        return self._current_campaign.sessions

    # Adventure Log / Events
    def add_event(self, event: AdventureEvent) -> None:
        """Add an event to the adventure log."""
        logger.info(f"➕ Adding event: '{event.title}' ({event.event_type})")
        self._events.append(event)
        self._save_events()
        logger.debug("✅ Event added and log saved.")

    def get_events(
        self,
        limit: int | None = None,
        event_type: str | None = None,
        session_number: int | None = None,
    ) -> list[AdventureEvent]:
        """Get adventure events, optionally filtered."""
        events = self._events

        if event_type:
            events = [e for e in events if e.event_type == event_type]

        if session_number is not None:
            events = [e for e in events if e.session_number == session_number]

        # Sort by timestamp (newest first)
        events = sorted(events, key=lambda e: e.timestamp, reverse=True)

        if limit:
            events = events[:limit]

        return events

    def search_events(self, query: str) -> list[AdventureEvent]:
        """Search events by title or description."""
        query_lower = query.lower()
        return [
            event for event in self._events
            if query_lower in event.title.lower() or query_lower in event.description.lower()
        ]

    # Library Bindings Management
    def enable_library_source(
        self,
        source_id: str,
        content_type: str | None = None,
        content_names: list[str] | None = None,
    ) -> None:
        """Enable a library source for the current campaign.

        Args:
            source_id: The source identifier to enable
            content_type: Optional content type to filter (e.g., "class", "spell").
                If None, enables all content from the source.
            content_names: Optional list of specific content names to enable.
                Only used if content_type is specified.
        """
        if not self._current_campaign:
            raise ValueError("No current campaign")

        if not self._library_bindings:
            raise ValueError("Library bindings not initialized")

        # Convert string content_type to ContentType enum if provided
        from .library.models import ContentType
        content_type_enum = None
        if content_type and content_type != "all":
            try:
                content_type_enum = ContentType(content_type)
            except ValueError:
                raise ValueError(f"Invalid content type: {content_type}")

        self._library_bindings.enable_source(
            source_id=source_id,
            content_type=content_type_enum,
            content_names=content_names,
        )
        self._save_library_bindings()
        logger.info(f"✅ Enabled library source '{source_id}' for campaign '{self._current_campaign.name}'")

    def disable_library_source(self, source_id: str) -> None:
        """Disable a library source for the current campaign.

        Args:
            source_id: The source identifier to disable
        """
        if not self._current_campaign:
            raise ValueError("No current campaign")

        if not self._library_bindings:
            raise ValueError("Library bindings not initialized")

        self._library_bindings.disable_source(source_id)
        self._save_library_bindings()
        logger.info(f"🚫 Disabled library source '{source_id}' for campaign '{self._current_campaign.name}'")

    def get_enabled_library_sources(self) -> list[str]:
        """Get list of enabled library source IDs for the current campaign.

        Returns:
            List of source_id strings for all enabled sources.
        """
        if not self._library_bindings:
            return []
        return self._library_bindings.get_enabled_sources()

    # ------------------------------------------------------------------
    # Discovery Tracker Management
    # ------------------------------------------------------------------

    @property
    def discovery_tracker(self) -> DiscoveryTracker | None:
        """Get the discovery tracker for the current campaign.

        Returns:
            DiscoveryTracker instance if a split campaign is loaded, None otherwise.
        """
        return self._discovery_tracker

    def _load_discovery_tracker(self) -> None:
        """Load or initialize the DiscoveryTracker for the current campaign.

        Only applicable to split storage campaigns. Creates a DiscoveryTracker
        pointed at the campaign directory, which will load discovery_state.json
        if it exists.
        """
        self._discovery_tracker = None

        if self._current_format != StorageFormat.SPLIT or not self._current_campaign:
            return

        campaign_dir = self._split_backend._get_campaign_dir(self._current_campaign.name)
        try:
            self._discovery_tracker = DiscoveryTracker(campaign_dir)
            logger.info(
                f"Loaded DiscoveryTracker for campaign '{self._current_campaign.name}' "
                f"({self._discovery_tracker.location_count} locations)"
            )
        except Exception as e:
            logger.warning(f"Failed to load DiscoveryTracker: {e}")
            self._discovery_tracker = None

    # ------------------------------------------------------------------
    # Fact Graph Management
    # ------------------------------------------------------------------

    @property
    def fact_db(self):
        """Get the FactDatabase for the current campaign.

        Returns:
            FactDatabase instance if a split campaign is loaded and the graph
            loaded successfully, None otherwise.
        """
        return self._fact_db

    @property
    def npc_knowledge_tracker(self):
        """Get the NPCKnowledgeTracker for the current campaign (or None)."""
        return self._npc_knowledge_tracker

    @property
    def party_knowledge(self):
        """Get the PartyKnowledge tracker for the current campaign (or None)."""
        return self._party_knowledge

    def _load_fact_graph(self) -> None:
        """Load or initialize the fact graph for the current campaign.

        Only applicable to split storage campaigns. Builds the FactDatabase
        plus the NPCKnowledgeTracker and PartyKnowledge views over it, loading
        any persisted state from the campaign directory. On failure all three
        accessors degrade to None — fact graph problems must never break the
        primary journal/entity write path.
        """
        self._fact_db = None
        self._npc_knowledge_tracker = None
        self._party_knowledge = None

        if self._current_format != StorageFormat.SPLIT or not self._current_campaign:
            return

        campaign_dir = self._split_backend._get_campaign_dir(self._current_campaign.name)
        try:
            from .claudmaster.consistency.fact_database import FactDatabase
            from .claudmaster.consistency.npc_knowledge import NPCKnowledgeTracker
            from .consistency.party_knowledge import PartyKnowledge

            fact_db = FactDatabase(campaign_dir)
            self._npc_knowledge_tracker = NPCKnowledgeTracker(fact_db, campaign_dir)
            self._party_knowledge = PartyKnowledge(fact_db, campaign_dir)
            self._fact_db = fact_db
            logger.info(
                f"Loaded fact graph for campaign '{self._current_campaign.name}' "
                f"({len(fact_db.facts)} facts)"
            )
        except Exception as e:
            logger.warning(f"Failed to load fact graph: {e}")
            self._fact_db = None
            self._npc_knowledge_tracker = None
            self._party_knowledge = None

    # ------------------------------------------------------------------
    # Timeline Tracker Management
    # ------------------------------------------------------------------

    @property
    def timeline_tracker(self):
        """Get the TimelineTracker for the current campaign.

        Returns:
            TimelineTracker instance if a split campaign is loaded and the
            timeline loaded successfully, None otherwise.
        """
        return self._timeline_tracker

    def _load_timeline_tracker(self) -> None:
        """Load or initialize the timeline tracker for the current campaign.

        Only applicable to split storage campaigns. On failure the accessor
        degrades to None — timeline problems must never break the primary
        journal/entity write path.
        """
        self._timeline_tracker = None

        if self._current_format != StorageFormat.SPLIT or not self._current_campaign:
            return

        campaign_dir = self._split_backend._get_campaign_dir(self._current_campaign.name)
        try:
            from .claudmaster.consistency.timeline import TimelineTracker

            self._timeline_tracker = TimelineTracker(campaign_dir)
            logger.info(
                f"Loaded timeline for campaign '{self._current_campaign.name}' "
                f"({self._timeline_tracker.event_count} events, "
                f"{'anchored' if self._timeline_tracker.anchored else 'unanchored'})"
            )
        except Exception as e:
            logger.warning(f"Failed to load timeline tracker: {e}")
            self._timeline_tracker = None

    def _save_discovery_state(self) -> None:
        """Save the discovery state for the current campaign.

        Only applicable to split storage campaigns with an active tracker.
        """
        if not self._discovery_tracker:
            return

        if self._current_format != StorageFormat.SPLIT or not self._current_campaign:
            return

        try:
            self._discovery_tracker.save()
            logger.debug(f"Saved discovery state for campaign '{self._current_campaign.name}'")
        except Exception as e:
            logger.error(f"Failed to save discovery state: {e}")

    # ------------------------------------------------------------------
    # Claudmaster Configuration Management
    # ------------------------------------------------------------------

    def get_claudmaster_config(self):
        """Get Claudmaster configuration for the current campaign.

        Returns:
            ClaudmasterConfig instance for the campaign.

        Raises:
            ValueError: If no campaign is loaded.
        """
        from .claudmaster.config import ClaudmasterConfig

        if not self._current_campaign:
            raise ValueError("No current campaign")

        if self._current_format != StorageFormat.SPLIT:
            return ClaudmasterConfig()

        campaign_dir = self._split_backend._get_campaign_dir(self._current_campaign.name)
        config_path = campaign_dir / "claudmaster-config.json"

        if not config_path.exists():
            return ClaudmasterConfig()

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return ClaudmasterConfig.model_validate(data)
        except Exception as e:
            logger.warning(f"Failed to load Claudmaster config: {e}. Using default.")
            return ClaudmasterConfig()

    def save_claudmaster_config(self, config) -> None:
        """Save Claudmaster configuration for the current campaign.

        Args:
            config: ClaudmasterConfig instance to save.

        Raises:
            ValueError: If no campaign is loaded.
        """
        if not self._current_campaign:
            raise ValueError("No current campaign")

        if self._current_format != StorageFormat.SPLIT:
            logger.warning("Cannot save Claudmaster config for monolithic campaigns")
            return

        campaign_dir = self._split_backend._get_campaign_dir(self._current_campaign.name)
        config_path = campaign_dir / "claudmaster-config.json"

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config.model_dump(mode="json"), f, indent=2)
        logger.debug(f"Saved Claudmaster config for campaign '{self._current_campaign.name}'")


class SplitStorageBackend:
    """Storage backend that splits campaign data into separate JSON files.

    This backend stores campaign data in a directory structure with separate files
    for each data category (characters, npcs, locations, quests, encounters, game_state).
    Sessions are stored in individual files in a subdirectory.

    Directory structure:
        data/campaigns/{campaign-name}/
        ├── campaign.json      # Metadata only
        ├── characters.json
        ├── npcs.json
        ├── locations.json
        ├── quests.json
        ├── encounters.json
        ├── game_state.json
        └── sessions/
            └── session-{NNN}.json

    Features:
    - Per-file dirty tracking using SHA-256 hashes
    - Only writes files that have been modified
    - Atomic writes (write to temp file, then rename)
    """

    def __init__(self, data_dir: str | Path = "dnd_data", auto_load: bool = True):
        """Initialize split storage backend.

        Args:
            data_dir: Base directory for all campaign data
            auto_load: If True, automatically load the most recent campaign
        """
        self.data_dir = Path(data_dir)
        logger.debug(f"📂 Initializing SplitStorageBackend with data_dir: {self.data_dir.resolve()}")
        self.data_dir.mkdir(exist_ok=True)

        # Create campaigns subdirectory
        (self.data_dir / "campaigns").mkdir(exist_ok=True)
        logger.debug("📂 Storage subdirectories ensured.")

        self._current_campaign: Campaign | None = None

        # Per-section hash tracking for dirty detection
        self._section_hashes: dict[str, str] = {
            "campaign": "",
            "characters": "",
            "npcs": "",
            "locations": "",
            "quests": "",
            "encounters": "",
            "game_state": "",
        }

        # Load existing data if auto_load is enabled
        if auto_load:
            logger.debug("📂 Loading initial data...")
            self._load_current_campaign()
            logger.debug("✅ Initial data loaded.")

    def _get_campaign_dir(self, campaign_name: str | None = None) -> Path:
        """Get the directory path for a campaign.

        Args:
            campaign_name: Name of the campaign. Uses current campaign if None.

        Returns:
            Path to campaign directory

        Raises:
            ValueError: If no campaign name provided and no current campaign
        """
        if campaign_name is None and self._current_campaign:
            campaign_name = self._current_campaign.name
        if campaign_name is None:
            raise ValueError("No campaign name provided and no current campaign")

        safe_name = "".join(c for c in campaign_name if c.isalnum() or c in (' ', '-', '_', "'")).rstrip()
        return self.data_dir / "campaigns" / safe_name

    def _ensure_campaign_structure(self, campaign_name: str) -> None:
        """Create the directory structure for a campaign.

        Args:
            campaign_name: Name of the campaign
        """
        campaign_dir = self._get_campaign_dir(campaign_name)
        campaign_dir.mkdir(parents=True, exist_ok=True)
        (campaign_dir / "sessions").mkdir(exist_ok=True)
        logger.debug(f"✅ Ensured directory structure for campaign '{campaign_name}'")

    def _compute_section_hash(self, data: dict | list) -> str:
        """Compute SHA-256 hash of a data section for dirty tracking.

        Args:
            data: Data to hash (dict or list)

        Returns:
            Hex string of SHA-256 hash
        """
        return sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def _atomic_write(self, file_path: Path, data: dict | list) -> None:
        """Write data to file atomically (write to temp, then rename).

        Args:
            file_path: Path to the file to write
            data: Data to write (will be JSON serialized)
        """
        temp_file = file_path.with_suffix('.tmp')
        try:
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)
            temp_file.replace(file_path)
            logger.debug(f"✅ Atomic write to {file_path.name} successful")
        except Exception as e:
            if temp_file.exists():
                temp_file.unlink()
            logger.error(f"❌ Error during atomic write to {file_path.name}: {e}")
            raise

    def _save_characters(self, force: bool = False) -> None:
        """Save characters to characters.json if modified.

        Args:
            force: If True, save even if unchanged
        """
        if not self._current_campaign:
            return

        characters_data = {
            name: char.model_dump(mode='json')
            for name, char in self._current_campaign.characters.items()
        }

        current_hash = self._compute_section_hash(characters_data)
        if not force and current_hash == self._section_hashes["characters"]:
            logger.debug("✅ Characters unchanged, skipping save.")
            return

        campaign_dir = self._get_campaign_dir()
        file_path = campaign_dir / "characters.json"
        self._atomic_write(file_path, characters_data)
        self._section_hashes["characters"] = current_hash
        logger.debug(f"💾 Saved characters to {file_path}")

    def _save_npcs(self, force: bool = False) -> None:
        """Save NPCs to npcs.json if modified.

        Args:
            force: If True, save even if unchanged
        """
        if not self._current_campaign:
            return

        npcs_data = {
            name: npc.model_dump(mode='json')
            for name, npc in self._current_campaign.npcs.items()
        }

        current_hash = self._compute_section_hash(npcs_data)
        if not force and current_hash == self._section_hashes["npcs"]:
            logger.debug("✅ NPCs unchanged, skipping save.")
            return

        campaign_dir = self._get_campaign_dir()
        file_path = campaign_dir / "npcs.json"
        self._atomic_write(file_path, npcs_data)
        self._section_hashes["npcs"] = current_hash
        logger.debug(f"💾 Saved NPCs to {file_path}")

    def _save_locations(self, force: bool = False) -> None:
        """Save locations to locations.json if modified.

        Args:
            force: If True, save even if unchanged
        """
        if not self._current_campaign:
            return

        locations_data = {
            name: loc.model_dump(mode='json')
            for name, loc in self._current_campaign.locations.items()
        }

        current_hash = self._compute_section_hash(locations_data)
        if not force and current_hash == self._section_hashes["locations"]:
            logger.debug("✅ Locations unchanged, skipping save.")
            return

        campaign_dir = self._get_campaign_dir()
        file_path = campaign_dir / "locations.json"
        self._atomic_write(file_path, locations_data)
        self._section_hashes["locations"] = current_hash
        logger.debug(f"💾 Saved locations to {file_path}")

    def _save_quests(self, force: bool = False) -> None:
        """Save quests to quests.json if modified.

        Args:
            force: If True, save even if unchanged
        """
        if not self._current_campaign:
            return

        quests_data = {
            title: quest.model_dump(mode='json')
            for title, quest in self._current_campaign.quests.items()
        }

        current_hash = self._compute_section_hash(quests_data)
        if not force and current_hash == self._section_hashes["quests"]:
            logger.debug("✅ Quests unchanged, skipping save.")
            return

        campaign_dir = self._get_campaign_dir()
        file_path = campaign_dir / "quests.json"
        self._atomic_write(file_path, quests_data)
        self._section_hashes["quests"] = current_hash
        logger.debug(f"💾 Saved quests to {file_path}")

    def _save_encounters(self, force: bool = False) -> None:
        """Save encounters to encounters.json if modified.

        Args:
            force: If True, save even if unchanged
        """
        if not self._current_campaign:
            return

        encounters_data = {
            name: enc.model_dump(mode='json')
            for name, enc in self._current_campaign.encounters.items()
        }

        current_hash = self._compute_section_hash(encounters_data)
        if not force and current_hash == self._section_hashes["encounters"]:
            logger.debug("✅ Encounters unchanged, skipping save.")
            return

        campaign_dir = self._get_campaign_dir()
        file_path = campaign_dir / "encounters.json"
        self._atomic_write(file_path, encounters_data)
        self._section_hashes["encounters"] = current_hash
        logger.debug(f"💾 Saved encounters to {file_path}")

    def _save_game_state(self, force: bool = False) -> None:
        """Save game state to game_state.json if modified.

        Args:
            force: If True, save even if unchanged
        """
        if not self._current_campaign:
            return

        game_state_data = self._current_campaign.game_state.model_dump(mode='json')

        current_hash = self._compute_section_hash(game_state_data)
        if not force and current_hash == self._section_hashes["game_state"]:
            logger.debug("✅ Game state unchanged, skipping save.")
            return

        campaign_dir = self._get_campaign_dir()
        file_path = campaign_dir / "game_state.json"
        self._atomic_write(file_path, game_state_data)
        self._section_hashes["game_state"] = current_hash
        logger.debug(f"💾 Saved game state to {file_path}")

    def _save_campaign_metadata(self, force: bool = False) -> None:
        """Save campaign metadata to campaign.json if modified.

        Only saves core metadata fields (id, name, description, dm_name, setting,
        world_notes, created_at, updated_at). Data fields are stored in separate files.

        Args:
            force: If True, save even if unchanged
        """
        if not self._current_campaign:
            return

        # Extract only metadata fields
        metadata = {
            "id": self._current_campaign.id,
            "name": self._current_campaign.name,
            "description": self._current_campaign.description,
            "dm_name": self._current_campaign.dm_name,
            "setting": str(self._current_campaign.setting) if self._current_campaign.setting else None,
            "world_notes": self._current_campaign.world_notes,
            "created_at": self._current_campaign.created_at.isoformat(),
            "updated_at": self._current_campaign.updated_at.isoformat() if self._current_campaign.updated_at else None,
        }

        current_hash = self._compute_section_hash(metadata)
        if not force and current_hash == self._section_hashes["campaign"]:
            logger.debug("✅ Campaign metadata unchanged, skipping save.")
            return

        campaign_dir = self._get_campaign_dir()
        file_path = campaign_dir / "campaign.json"
        self._atomic_write(file_path, metadata)
        self._section_hashes["campaign"] = current_hash
        logger.debug(f"💾 Saved campaign metadata to {file_path}")

    def _save_session(self, session: SessionNote, force: bool = False) -> None:
        """Save a session note to sessions/session-{NNN}.json.

        Args:
            session: Session note to save
            force: If True, save even if unchanged
        """
        if not self._current_campaign:
            return

        campaign_dir = self._get_campaign_dir()
        sessions_dir = campaign_dir / "sessions"
        sessions_dir.mkdir(exist_ok=True)

        file_path = sessions_dir / f"session-{session.session_number:03d}.json"
        session_data = session.model_dump(mode='json')

        # Check if file exists and compare hash
        if not force and file_path.exists():
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
                existing_hash = self._compute_section_hash(existing_data)
                current_hash = self._compute_section_hash(session_data)
                if existing_hash == current_hash:
                    logger.debug(f"✅ Session {session.session_number} unchanged, skipping save.")
                    return
            except Exception as e:
                logger.warning(f"⚠️ Error reading existing session file: {e}")

        self._atomic_write(file_path, session_data)
        logger.debug(f"💾 Saved session {session.session_number} to {file_path}")

    def save_all(self, force: bool = False) -> None:
        """Save all campaign data to their respective files.

        Args:
            force: If True, save all files regardless of dirty state
        """
        if not self._current_campaign:
            logger.debug("❌ No current campaign to save.")
            return

        logger.info(f"💾 Saving campaign '{self._current_campaign.name}'")

        # Save metadata first
        self._save_campaign_metadata(force=force)

        # Save all data sections
        self._save_characters(force=force)
        self._save_npcs(force=force)
        self._save_locations(force=force)
        self._save_quests(force=force)
        self._save_encounters(force=force)
        self._save_game_state(force=force)

        # Save all sessions
        for session in self._current_campaign.sessions:
            self._save_session(session, force=force)

        logger.info(f"✅ Campaign '{self._current_campaign.name}' saved successfully.")

    def _load_characters(self, campaign_dir: Path) -> dict[str, Character]:
        """Load characters from characters.json.

        Args:
            campaign_dir: Path to campaign directory

        Returns:
            Dictionary of character name to Character object
        """
        file_path = campaign_dir / "characters.json"
        if not file_path.exists():
            logger.debug("No characters.json found, returning empty dict.")
            return {}

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            characters = {
                name: Character.model_validate(char_data)
                for name, char_data in data.items()
            }
            self._section_hashes["characters"] = self._compute_section_hash(data)
            logger.debug(f"✅ Loaded {len(characters)} characters")
            return characters
        except Exception as e:
            logger.error(f"❌ Error loading characters: {e}")
            return {}

    def _load_npcs(self, campaign_dir: Path) -> dict[str, NPC]:
        """Load NPCs from npcs.json.

        Args:
            campaign_dir: Path to campaign directory

        Returns:
            Dictionary of NPC name to NPC object
        """
        file_path = campaign_dir / "npcs.json"
        if not file_path.exists():
            logger.debug("No npcs.json found, returning empty dict.")
            return {}

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            npcs = {
                name: NPC.model_validate(npc_data)
                for name, npc_data in data.items()
            }
            self._section_hashes["npcs"] = self._compute_section_hash(data)
            logger.debug(f"✅ Loaded {len(npcs)} NPCs")
            return npcs
        except Exception as e:
            logger.error(f"❌ Error loading NPCs: {e}")
            return {}

    def _load_locations(self, campaign_dir: Path) -> dict[str, Location]:
        """Load locations from locations.json.

        Args:
            campaign_dir: Path to campaign directory

        Returns:
            Dictionary of location name to Location object
        """
        file_path = campaign_dir / "locations.json"
        if not file_path.exists():
            logger.debug("No locations.json found, returning empty dict.")
            return {}

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            locations = {
                name: Location.model_validate(loc_data)
                for name, loc_data in data.items()
            }
            self._section_hashes["locations"] = self._compute_section_hash(data)
            logger.debug(f"✅ Loaded {len(locations)} locations")
            return locations
        except Exception as e:
            logger.error(f"❌ Error loading locations: {e}")
            return {}

    def _load_quests(self, campaign_dir: Path) -> dict[str, Quest]:
        """Load quests from quests.json.

        Args:
            campaign_dir: Path to campaign directory

        Returns:
            Dictionary of quest title to Quest object
        """
        file_path = campaign_dir / "quests.json"
        if not file_path.exists():
            logger.debug("No quests.json found, returning empty dict.")
            return {}

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            quests = {
                title: Quest.model_validate(quest_data)
                for title, quest_data in data.items()
            }
            self._section_hashes["quests"] = self._compute_section_hash(data)
            logger.debug(f"✅ Loaded {len(quests)} quests")
            return quests
        except Exception as e:
            logger.error(f"❌ Error loading quests: {e}")
            return {}

    def _load_encounters(self, campaign_dir: Path) -> dict[str, CombatEncounter]:
        """Load encounters from encounters.json.

        Args:
            campaign_dir: Path to campaign directory

        Returns:
            Dictionary of encounter name to CombatEncounter object
        """
        file_path = campaign_dir / "encounters.json"
        if not file_path.exists():
            logger.debug("No encounters.json found, returning empty dict.")
            return {}

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            encounters = {
                name: CombatEncounter.model_validate(enc_data)
                for name, enc_data in data.items()
            }
            self._section_hashes["encounters"] = self._compute_section_hash(data)
            logger.debug(f"✅ Loaded {len(encounters)} encounters")
            return encounters
        except Exception as e:
            logger.error(f"❌ Error loading encounters: {e}")
            return {}

    def _load_game_state(self, campaign_dir: Path, campaign_name: str) -> GameState:
        """Load game state from game_state.json.

        Args:
            campaign_dir: Path to campaign directory
            campaign_name: Name of the campaign (for default GameState)

        Returns:
            GameState object
        """
        file_path = campaign_dir / "game_state.json"
        if not file_path.exists():
            logger.debug("No game_state.json found, creating default.")
            return GameState(campaign_name=campaign_name)

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            game_state = GameState.model_validate(data)
            self._section_hashes["game_state"] = self._compute_section_hash(data)
            logger.debug("✅ Loaded game state")
            return game_state
        except Exception as e:
            logger.error(f"❌ Error loading game state: {e}")
            return GameState(campaign_name=campaign_name)

    def _load_sessions(self, campaign_dir: Path) -> list[SessionNote]:
        """Load session notes from sessions/ subdirectory.

        Args:
            campaign_dir: Path to campaign directory

        Returns:
            List of SessionNote objects, sorted by session number
        """
        sessions_dir = campaign_dir / "sessions"
        if not sessions_dir.exists():
            logger.debug("No sessions directory found, returning empty list.")
            return []

        sessions = []
        session_files = sorted(sessions_dir.glob("session-*.json"))

        for file_path in session_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                session = SessionNote.model_validate(data)
                sessions.append(session)
            except Exception as e:
                logger.error(f"❌ Error loading session from {file_path.name}: {e}")

        logger.debug(f"✅ Loaded {len(sessions)} sessions")
        return sorted(sessions, key=lambda s: s.session_number)

    def _load_campaign_metadata(self, campaign_dir: Path) -> dict:
        """Load campaign metadata from campaign.json.

        Args:
            campaign_dir: Path to campaign directory

        Returns:
            Dictionary with campaign metadata

        Raises:
            FileNotFoundError: If campaign.json does not exist
        """
        file_path = campaign_dir / "campaign.json"
        if not file_path.exists():
            raise FileNotFoundError(f"Campaign metadata file not found: {file_path}")

        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        self._section_hashes["campaign"] = self._compute_section_hash(data)
        logger.debug("✅ Loaded campaign metadata")
        return data

    def _load_current_campaign(self) -> None:
        """Load the most recently modified campaign."""
        logger.debug("📂 Attempting to load the most recent campaign...")
        campaigns_dir = self.data_dir / "campaigns"
        if not campaigns_dir.exists():
            logger.debug("❌ Campaigns directory does not exist. No campaign loaded.")
            return

        # Find the most recent campaign directory
        campaign_dirs = [d for d in campaigns_dir.iterdir() if d.is_dir()]
        if not campaign_dirs:
            logger.debug("❌ No campaign directories found.")
            return

        # Sort by modification time and load the most recent
        latest_dir = max(campaign_dirs, key=lambda d: d.stat().st_mtime)
        logger.debug(f"📂 Most recent campaign directory is '{latest_dir.name}'.")

        try:
            self._load_campaign_from_dir(latest_dir)
            logger.info(f"✅ Successfully loaded campaign: {self._current_campaign.name}")  # type: ignore
        except Exception as e:
            logger.error(f"❌ Error loading campaign from {latest_dir.name}: {e}")

    def _load_campaign_from_dir(self, campaign_dir: Path) -> Campaign:
        """Load a campaign from a directory.

        Args:
            campaign_dir: Path to campaign directory

        Returns:
            Campaign object

        Raises:
            FileNotFoundError: If campaign.json does not exist
        """
        metadata = self._load_campaign_metadata(campaign_dir)

        # Load all data sections
        characters = self._load_characters(campaign_dir)
        npcs = self._load_npcs(campaign_dir)
        locations = self._load_locations(campaign_dir)
        quests = self._load_quests(campaign_dir)
        encounters = self._load_encounters(campaign_dir)
        game_state = self._load_game_state(campaign_dir, metadata["name"])
        sessions = self._load_sessions(campaign_dir)

        # Construct Campaign object
        campaign = Campaign(
            id=metadata.get("id", new_uuid()),
            name=metadata["name"],
            description=metadata["description"],
            dm_name=metadata.get("dm_name"),
            setting=metadata.get("setting"),
            characters=characters,
            npcs=npcs,
            locations=locations,
            quests=quests,
            encounters=encounters,
            sessions=sessions,
            game_state=game_state,
            world_notes=metadata.get("world_notes", ""),
            created_at=datetime.fromisoformat(metadata["created_at"]),
            updated_at=datetime.fromisoformat(metadata["updated_at"]) if metadata.get("updated_at") else None,
        )

        self._current_campaign = campaign
        return campaign

    def create_campaign(self, name: str, description: str, dm_name: str | None = None, setting: str | Path | None = None) -> Campaign:
        """Create a new campaign.

        Args:
            name: Campaign name
            description: Campaign description
            dm_name: Dungeon Master name
            setting: Campaign setting (string or path to file)

        Returns:
            New Campaign object
        """
        logger.info(f"✨ Creating new campaign: '{name}'")

        # Ensure directory structure exists
        self._ensure_campaign_structure(name)

        # Create campaign object
        game_state = GameState(campaign_name=name)
        campaign = Campaign(
            name=name,
            description=description,
            dm_name=dm_name,
            setting=setting,
            game_state=game_state
        )

        self._current_campaign = campaign
        self.save_all(force=True)  # Force save for new campaign
        logger.info(f"✅ Campaign '{name}' created and set as active.")
        return campaign

    def get_current_campaign(self) -> Campaign | None:
        """Get the current campaign.

        Returns:
            Current Campaign object or None
        """
        return self._current_campaign

    def list_campaigns(self) -> list[str]:
        """List all available campaigns.

        Returns:
            List of campaign names
        """
        campaigns_dir = self.data_dir / "campaigns"
        if not campaigns_dir.exists():
            return []

        return [d.name for d in campaigns_dir.iterdir() if d.is_dir()]

    def load_campaign(self, name: str) -> Campaign:
        """Load a specific campaign.

        Args:
            name: Campaign name to load

        Returns:
            Loaded Campaign object

        Raises:
            FileNotFoundError: If campaign does not exist
        """
        logger.info(f"📂 Attempting to load campaign: '{name}'")
        campaign_dir = self._get_campaign_dir(name)
        logger.debug(f"📂 Campaign directory path: {campaign_dir}")

        if not campaign_dir.exists():
            logger.error(f"❌ Campaign directory not found for '{name}'")
            raise FileNotFoundError(f"Campaign '{name}' not found")

        campaign = self._load_campaign_from_dir(campaign_dir)
        logger.info(f"✅ Successfully loaded campaign '{name}'.")
        return campaign
