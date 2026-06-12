# DM20 Protocol - Data Storage Guide

This guide details the file and folder structure used by the DM20 Protocol MCP server to persist all data. Storage is managed primarily by the `DnDStorage` class in `src/dm20_protocol/storage.py`, with additional managers for the library and rulebook systems.

## Root Directory

The root data directory is controlled by the `DM20_STORAGE_DIR` environment variable. If not set, it defaults to the current working directory.

```
$DM20_STORAGE_DIR/
├── campaigns/
├── events/
├── library/
└── rulebook_cache/
```

## Campaigns Directory

- **Path**: `campaigns/`
- **Purpose**: Stores all individual campaign data.

DM20 supports two storage formats, auto-detected on load:

### Split Format (default for new campaigns)

Each campaign is a directory with separate JSON files for each data type. This format uses atomic writes (temp file + rename) and SHA-256 dirty tracking to avoid redundant saves.

```
campaigns/
└── {campaign-name}/
    ├── campaign.json               # Campaign metadata (id, name, description, dm_name, setting, rules_version, interaction_mode)
    ├── characters.json             # All player characters (dict of name -> Character)
    ├── npcs.json                   # All NPCs (dict of name -> NPC)
    ├── locations.json              # All locations (dict of name -> Location)
    ├── quests.json                 # All quests (dict of title -> Quest)
    ├── encounters.json             # Combat encounters (dict of name -> CombatEncounter)
    ├── game_state.json             # Current game state (location, combat status, party level, funds)
    ├── claudmaster-config.json     # Claudmaster AI DM configuration for this campaign
    ├── adventure_log.json          # Campaign-scoped adventure log (AdventureEvent array)
    │
    ├── sessions/                   # Session notes
    │   └── session-{NNN}.json      # One file per session (NNN = zero-padded number)
    │
    ├── rulebooks/                  # Campaign-specific rulebook management
    │   ├── manifest.json           # Loaded sources and versions
    │   ├── library-bindings.json   # Which library sources are enabled for this campaign
    │   └── custom/                 # Custom rulebook definitions
    │       └── {source-id}.json    # CustomSource JSON files
    │
    └── claudmaster_sessions/       # Claudmaster session persistence (pause/resume)
        └── {session-id}/
            ├── session_meta.json   # Metadata (id, campaign, status, duration, action count)
            ├── state_snapshot.json # Full session state at save time
            └── action_history.json # Conversation and action history
```

### Monolithic Format (legacy)

Older campaigns may use a single JSON file containing the entire `Campaign` Pydantic model. This format is auto-detected and supported for backward compatibility.

```
campaigns/
└── {campaign-name}.json            # Single file with all campaign data
```

**File naming**: Campaign names are sanitized for the filesystem (alphanumeric, spaces, hyphens, underscores only).

## Events Directory (legacy)

- **Path**: `events/`
- **Purpose**: Legacy fallback adventure log. Split-format campaigns store
  their log inside the campaign directory (`campaigns/{name}/adventure_log.json`);
  this global file is only used by monolithic campaigns and when no campaign
  is loaded.

```
events/
├── adventure_log.json              # Legacy/fallback JSON array of AdventureEvent objects
└── adventure_log.json.migrated     # Backup left behind after one-shot migration
```

When a split campaign is loaded and it is the only campaign in the data
directory, any legacy global log is migrated into the campaign's own log
(events stamped with the campaign name) and the global file is renamed to
`adventure_log.json.migrated`. With multiple campaigns the legacy events
cannot be attributed automatically and the file is left in place, excluded
from campaign views.

## Library Directory

- **Path**: `library/`
- **Purpose**: Manages the PDF/Markdown rulebook library for content indexing, search, and extraction.

Created lazily on first access by `LibraryManager.ensure_directories()`.

```
library/
├── pdfs/                           # User-provided source files
│   ├── {filename}.pdf              # PDF rulebooks
│   └── {filename}.md               # Markdown rulebooks
│
├── index/                          # Auto-generated table of contents indexes
│   └── {source-id}.index.json      # TOC for each source (filename, file_hash, page count, entries)
│
└── extracted/                      # Extracted content in CustomSource JSON format
    └── {source-id}/                # One directory per source
        └── {content-name}.json     # Extracted classes, races, spells, monsters, feats, items
```

## Rulebook Cache Directory

- **Path**: `rulebook_cache/`
- **Purpose**: Caches API responses from the D&D 5e SRD API to avoid repeated network calls.

Created lazily on first SRD load.

```
rulebook_cache/
├── srd_2014/                       # Official D&D 5e SRD (2014) cache
│   ├── classes/{endpoint}.json
│   ├── races/{endpoint}.json
│   ├── spells/{endpoint}.json
│   ├── monsters/{endpoint}.json
│   ├── equipment/{endpoint}.json
│   ├── feats/{endpoint}.json
│   └── backgrounds/{endpoint}.json
│
└── srd_2024/                       # D&D 5e SRD (2024) cache (same structure)
    └── ...
```

## Summary

| Path | Content | Created By |
|------|---------|------------|
| `campaigns/` | Campaign data (split or monolithic) | `DnDStorage.__init__` |
| `campaigns/{name}/` | Split-format campaign directory | `SplitStorageBackend._ensure_campaign_structure` |
| `campaigns/{name}/rulebooks/` | Campaign rulebook config and custom sources | `DnDStorage.create_campaign` |
| `campaigns/{name}/sessions/` | Session notes (one file per session) | `SplitStorageBackend._ensure_campaign_structure` |
| `campaigns/{name}/claudmaster_sessions/` | Paused/ended Claudmaster session state | `SessionSerializer.save_session` |
| `campaigns/{name}/adventure_log.json` | Campaign-scoped adventure log | `DnDStorage._save_events` |
| `events/` | Legacy/fallback adventure log | `DnDStorage.__init__` |
| `library/pdfs/` | User-provided PDF/Markdown source files | `LibraryManager.ensure_directories` |
| `library/index/` | Auto-generated TOC indexes | `LibraryManager.ensure_directories` |
| `library/extracted/` | Extracted CustomSource JSON files | `LibraryManager.ensure_directories` |
| `rulebook_cache/srd_{version}/` | Cached SRD API responses | `SRDSource.load` |

## Configuration

| Variable | Purpose | Default |
|----------|---------|---------|
| `DM20_STORAGE_DIR` | Root data directory path | Current working directory |

Set this in a `.env` file or as an environment variable before starting the server.
