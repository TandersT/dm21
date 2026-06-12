---
description: Begin or resume a D&D game session. Load a campaign, set the scene, and start playing.
argument-hint: [campaign_name]
allowed-tools: Task, AskUserQuestion, Skill, mcp__dm20-protocol__check_for_updates, mcp__dm20-protocol__get_campaign_info, mcp__dm20-protocol__list_campaigns, mcp__dm20-protocol__load_campaign, mcp__dm20-protocol__create_campaign, mcp__dm20-protocol__list_characters, mcp__dm20-protocol__get_character, mcp__dm20-protocol__create_character, mcp__dm20-protocol__update_character, mcp__dm20-protocol__import_from_dndbeyond, mcp__dm20-protocol__get_game_state, mcp__dm20-protocol__get_claudmaster_session_state, mcp__dm20-protocol__start_claudmaster_session, mcp__dm20-protocol__get_sessions, mcp__dm20-protocol__get_location, mcp__dm20-protocol__list_quests, mcp__dm20-protocol__configure_claudmaster, mcp__dm20-protocol__update_game_state, mcp__dm20-protocol__discover_adventures, mcp__dm20-protocol__load_adventure, mcp__dm20-protocol__load_rulebook, mcp__dm20-protocol__start_party_mode, mcp__dm20-protocol__get_class_info, mcp__dm20-protocol__get_race_info, mcp__dm20-protocol__roll_dice, mcp__dm20-protocol__search_rules, mcp__dm20-protocol__add_spell, mcp__dm20-protocol__get_spell_info, mcp__dm20-protocol__get_session_recap, mcp__dm20-protocol__party_knowledge, mcp__dm20-protocol__sync_facts, mcp__dm20-protocol__get_events
---

# DM Start

Begin or resume a D&D game session.

## Usage
```
/dm:start [campaign_name]
```

## DM Persona

!`cat .claude/dm-persona.md`

**From this point forward, you ARE the Dungeon Master.** All output follows the persona's formatting and authority rules above.

## Instructions

### 0. Update Check

Call `check_for_updates` silently. **If an update is available**, display a brief notification before proceeding:

```
╔══════════════════════════════════════════════════════╗
║  ⚡ dm20-protocol update available: v{current} → v{latest}  ║
║                                                      ║
║  Run: bash <(curl -fsSL https://raw.githubusercontent║
║  .com/Polloinfilzato/dm20-protocol/main/install.sh)  ║
║  --upgrade                                           ║
║                                                      ║
║  See what's new: /dm:release-notes                   ║
╚══════════════════════════════════════════════════════╝
```

**If up to date or if the check fails:** Say nothing. Do not show any version info — just proceed silently to Step 1.

### 1. Campaign Selection

**If `$ARGUMENTS` is provided:**
```
load_campaign(name="$ARGUMENTS")
```
Skip to Step 2 (Gather World State).

**If no arguments:**

1. Call `list_campaigns()`
2. Use `AskUserQuestion` to let the player choose:

```
Question: "Which campaign shall we embark upon?"
Header: "Campaign"
Options:
  - [Each existing campaign name as an option]
  - "Create a new campaign"
  - "Adventure module — start an official published adventure"
```

- **If "Create a new campaign":**
  1. Ask for name and description conversationally.
  2. Ask for the rules edition using `AskUserQuestion`:
     ```
     Question: "Which D&D rules edition?"
     Header: "Rules"
     Options:
       - "2024 — D&D 2024 revised rules (Recommended)"
       - "2014 — D&D 5e 2014 classic rules"
     ```
  3. Call `create_campaign()` with the chosen `rules_version` ("2024" or "2014").
  4. Load rule sources for the chosen edition:
     ```
     load_rulebook(source="srd", version=chosen_version)
     load_rulebook(source="5etools")
     ```
     5etools provides richer data (full spell descriptions, equipment stats, monster details) that supplements the SRD.
  5. **Auto-launch profile configuration on first campaign:**
     - Call `configure_claudmaster()` with no args to read current config.
     - If this is a fresh/default config (no previous customization), invoke `/dm:profile` via the Skill tool (`skill: "dm:profile"`) to let the player set model quality, narrative style, dialogue style, and interaction mode.
     - After profile setup, save `profile_configured:true` to game_state notes via `update_game_state(notes=...)`.
     - On subsequent loads, skip profile setup if `profile_configured:true` exists in game_state notes.
- **If "Adventure module":** Call `discover_adventures()` to show options, let the player pick, then call `load_adventure()`. After loading, also call `load_rulebook(source="5etools")` to supplement rule data.
  - **IMPORTANT:** When presenting the adventure list, always inform the player about the source: *"These adventure modules are indexed from the 5etools open database, which catalogs official D&D 5th Edition adventures published by Wizards of the Coast."*
- **If an existing campaign:** Call `load_campaign(name=chosen)`.

**After loading an adventure module:** Always provide a clear, human-readable summary to the player. Do NOT just show raw tool output. Summarize:
  - The adventure name and a brief (1-2 sentence) spoiler-free teaser
  - Whether the module was loaded successfully
  - Whether Chapter 1 was populated (locations, NPCs, starting quest)
  - If there were any warnings, explain them in plain language
  - Example: *"Baldur's Gate: Descent Into Avernus is ready! Chapter 1 has been populated with starting locations and NPCs. You're all set to begin your descent into the Nine Hells."*

**If load fails:** "No campaign named '$ARGUMENTS' found. Available campaigns:" then list them.

### 2. Gather World State

**If the campaign was just created (fresh from Step 1):** Only call `get_campaign_info` and `get_game_state`. Do NOT call `list_characters` or `list_quests` — there are none yet, and showing "No characters" is confusing noise.

**If loading an existing campaign:** Call these in parallel to build your context:
- `get_campaign_info` — campaign name, description, entity counts
- `list_characters` — all PCs in the campaign
- `get_game_state` — current location, session number, combat status, in-game date
- `list_quests(status="active")` — active quest hooks

### 3. Game Mode Check

Check if the campaign already has a game mode stored in game_state notes (look for `game_mode:solo` or `game_mode:human_party`).

**If no game mode is set (first time):**

Use `AskUserQuestion`:
```
Question: "How would you like to play?"
Header: "Game Mode"
Options:
  - "SOLO — Just me + AI companions + AI DM"
  - "HUMAN PARTY — Multiple human players + AI DM via Party Mode"
```

- **SOLO selected:** Save `game_mode:solo` to game_state notes via `update_game_state(notes=...)`. Proceed to Step 3a.
- **HUMAN PARTY selected:** Save `game_mode:human_party` to game_state notes via `update_game_state(notes=...)`. Proceed to Step 3b for character setup. Party Mode server will be started AFTER characters are created.

**If game mode already set:** Proceed to Step 3a (if solo) or Step 3b (if human_party). If human_party and characters already exist, invoke `/dm:party-mode` directly.

### 3a. SOLO Party Setup

**If the campaign has no player characters:**
Guide the player through character creation using Step 3c (Character Creation Flow).

After the player's character is created, proceed to AI companion setup below.

### 3b. HUMAN PARTY Setup

**If the campaign has no player characters:**

1. Ask how many human players will participate.
2. For EACH player, use Step 3c (Character Creation Flow) to create or import their character.
3. **After ALL characters are created:** Invoke `/dm:party-mode` using the Skill tool (`skill: "dm:party-mode"`) to start the server and generate tokens/QR codes.

**If characters already exist:** Invoke `/dm:party-mode` directly.

### 3c. Character Creation Flow (shared by SOLO and HUMAN PARTY)

For EACH player character that needs to be created:

**Step P0 — Player Name:**
First, ask for the **player's** real name (the human playing this character). In HUMAN PARTY mode this is essential since multiple humans play. Store as `player_name` for `create_character()`.

Then present this choice:

Use `AskUserQuestion`:
```
Question: "How would you like to create {player_name}'s character?"
Header: "Character"
Options:
  - "Import from D&D Beyond — paste a DDB URL or character ID"
  - "Create from scratch — choose name, race, and class"
```

**If "Import from D&D Beyond":**
1. Ask the player for their D&D Beyond character URL (e.g., `https://www.dndbeyond.com/characters/123456789`)
2. Call `import_from_dndbeyond(url=...)` with the provided URL
3. Provide a clear summary of the imported character (name, race, class, level, HP)
4. If the import fails, explain the error and offer to try again or create from scratch

**If "Create from scratch":**

1. Ask for the character's **name**.

2. Present a choice for build depth:

Use `AskUserQuestion`:
```
Question: "How would you like to build {name}?"
Header: "Build Mode"
Options:
  - "Quick build — I'll handle race, class, stats, and everything for you"
  - "Guided wizard — step-by-step choices where you decide everything"
```

**If "Quick build":**
- Ask only for a brief concept (e.g., "stealthy archer", "holy warrior", "chaotic wizard")
- Ask "What level should {name} be?" — if other PCs exist, suggest matching their level. Default to 1 if no party context.
- Auto-select race, class, subclass, ability scores, and equipment to match the concept
- For spellcasting classes, auto-select thematic cantrips and spells matching the concept and level
- Call `create_character()` with reasonable defaults (include `player_name` from Step P0)
- Proceed to **Character Completeness Check** below

**If "Guided wizard":**
Guide the player through EACH decision interactively, using `AskUserQuestion` for each step. **CRITICAL: In guided mode, NEVER auto-assign anything. Every single choice must be presented to the player.**

**Step W1 — Level:**
Ask what level the character should be. If other PCs in the party are above level 1, suggest matching their level but always let the player decide.

**Step W2 — Race:**
Present race options in tiers, using `AskUserQuestion`:
```
Question: "Choose a race for {name}:"
Header: "Race"
Options:
  - "Adventure races — races featured in the loaded adventure module" (ONLY if an adventure is loaded AND it provides specific races)
  - "Classic races — Human, Elf, Dwarf, Halfling, Gnome, Half-Elf, Half-Orc, Tiefling, Dragonborn"
  - "Exotic/uncommon races — browse an extended list with racial traits"
```

- **Adventure races:** If an adventure is loaded (e.g., Dragonlance provides Kender), use `search_rules` or adventure data to list the adventure-specific races with a brief description of each. Let the player pick.
- **Classic races:** List the 9 standard PHB races. Let the player pick.
- **Exotic/uncommon races:** Use `search_rules` to find all available races from loaded rulebooks. Present a numbered table showing: name, ability bonuses, speed, key traits. Let the player pick by number or name.

After race selection, if the race has subraces (e.g., High Elf, Wood Elf), present them as choices.

**IMPORTANT — Racial ability bonuses:** If the race grants ability score bonuses that the player can distribute (e.g., "+2 to one ability, +1 to another" or "Customizing Your Origin" rules), you MUST ask the player where to assign them. NEVER auto-assign racial bonuses in guided mode.

**Step W3 — Class:**
Present class options similarly:
```
Question: "Choose a class for {name}:"
Header: "Class"
Options:
  - "Adventure classes — classes featured in the loaded adventure" (ONLY if applicable)
  - "Standard classes — the 12 core D&D classes"
  - "Show me all available classes from loaded rulebooks"
```

For standard classes, list all 12 with a one-line description. Let the player pick.

**Step W4 — Subclass:**
If the character's level qualifies for a subclass, use `get_class_info` to retrieve available subclasses. Present each with a brief description and let the player choose.

**Step W5 — Ability Scores:**
```
Question: "How do you want to determine ability scores?"
Header: "Abilities"
Options:
  - "Standard Array (15, 14, 13, 12, 10, 8)"
  - "Roll 4d6 drop lowest — I'll roll, you assign"
  - "Manual — tell me exactly what scores you want"
```

- **Standard Array:** Present the scores and ask the player to assign each to STR/DEX/CON/INT/WIS/CHA. Suggest an optimal distribution for their class but let them override.
- **Roll 4d6 drop lowest:** Call `roll_dice("4d6kh3")` 6 times. For EACH roll, display the individual dice results (e.g., "Rolled: 4, 3, 6, 5 → dropped 3 → **15**"). Record the roll data in a `creation_rolls` dict:
  ```
  creation_rolls = {
      "strength": {"rolls": [4, 3, 6, 5], "dropped": 3, "total": 15},
      "dexterity": {"rolls": [2, 6, 6, 4], "dropped": 2, "total": 16},
      ...
  }
  ```
  After all 6 rolls, present the totals and ask the player to assign each to an ability score. Save `creation_rolls` to pass to `create_character()` or set via `update_character()` after creation.
- **Manual:** Let the player specify each score.

After base scores, apply racial bonuses (asking the player if distributable — see Step W2 note).

**Step W6 — Skills:**
Use `get_class_info` to show the class's skill proficiency list and how many they can pick. Present as a numbered list and let the player choose.

**Step W7 — Equipment:**
```
Question: "How do you want to handle equipment?"
Header: "Equipment"
Options:
  - "Default starting equipment for my class"
  - "Let me choose from the starting equipment options"
```

**Step W8 — Spells (spellcasting classes only):**
If the chosen class has spellcasting ability:
1. Use `search_rules()` and `get_spell_info()` to get the available cantrips and spells for this class at the chosen level.
2. Determine how many cantrips and spells the character knows/prepares at their level (per PHB rules for their class).
3. Present the available **cantrips** with name, school, and brief description. Let the player pick the correct number.
4. Present the available **level 1+ spells** (up to the highest spell slot level available). Let the player pick their known/prepared spells.
5. For **Quick Build**, auto-select thematic spells instead of asking.

Call `create_character()` with all the player's choices (include `player_name` from Step P0). Summarize the final character sheet.

### Character Completeness Check

After EVERY `create_character()` call (both Quick Build and Guided Wizard), perform this validation:

1. Call `get_character(name_or_id=<character_id>)` to inspect the created character.
2. **Spells check:** If the character's class has spellcasting but `spells_known` is empty, use `add_spell()` to add appropriate starting spells from the rulebook data. For Quick Build, auto-select thematic spells. For Guided Wizard, the player already chose in Step W8.
3. **Inventory check:** If `inventory` is empty, warn and add class default starting equipment.
4. **AC check:** Verify armor_class is correct (base 10 + DEX mod, or armor-based if wearing armor). Update via `update_character()` if needed.
5. **Creation rolls:** If ability scores were rolled (4d6 drop lowest), save `creation_rolls` to the character via `update_character(creation_rolls=...)`.
6. Present a **Character Review** summary showing: name, player, race, class, level, all ability scores (with roll details if rolled), HP, AC, speed, skills, equipment, spells (if any), and features. Ask for confirmation before proceeding.

**Important:** If other PCs in the party are above level 1, proactively suggest matching their level but always let the player decide.

After each character is created/imported and reviewed, confirm before proceeding to the next one.

---

**If characters exist but no AI companions are registered (SOLO mode only):**

Use `AskUserQuestion`:
```
Question: "How many AI companions would you like in your party?"
Header: "Party Size"
Options:
  - "0 — I'll go alone"
  - "1 companion"
  - "2 companions"
  - "3 companions"
```

**For each AI companion requested:**

Use `AskUserQuestion`:
```
Question: "What role should companion #N fill?"
Header: "Role"
Options:
  - "Tank — frontline warrior, heavy armor"
  - "Healer — divine caster, keeps the party alive"
  - "Caster — arcane spellcaster, versatile magic"
  - "Rogue — stealth, traps, precision damage"
```

Based on the role, auto-generate a PC with:
- **Tank:** Fighter, high STR/CON, heavy armor
- **Healer:** Cleric, high WIS/CON, healing spells
- **Caster:** Wizard, high INT/DEX, offensive/utility spells
- **Rogue:** Rogue, high DEX/CHA, stealth skills

Call `create_character()` for each with:
- A fitting name (fantasy-appropriate)
- `player_name` set to `"AI"`
- Appropriate `bio` describing personality (brave/cautious/scholarly/cunning)
- `description` for appearance

Save the AI companion character IDs in game_state notes: `ai_companions:[id1,id2,...]`

### 4. Check for Existing Session

Check if there's a paused session to resume:
- Look at `get_game_state` for session info
- Check `get_sessions` for the most recent session note

**If resuming (previous session exists):**
1. `get_session_recap()` — the full "previously on" picture: narrative recap, key events, active quests, unresolved threads, NPC reminders, plus the last session's journal events verbatim
2. `party_knowledge()` — everything the party has learned so far; these facts are established canon
3. **If the recap or party knowledge come back empty but session notes exist** (the campaign has history the fact graph hasn't ingested yet): call `sync_facts()` once, then retry `get_session_recap()`
4. **If the recap is still unavailable** (e.g., the fact graph can't load): reconstruct it from `get_sessions(detail="full")` + `get_events(session_number=<last session>)`
5. `get_location` for the current location details
6. `get_character(name_or_id=<character_name>)` for each PC — check HP, conditions, inventory
7. Deliver a "Previously..." recap woven into narrative (not a bullet list). Established details from the recap — names, places, exact wording — are canon; never contradict them.
8. Re-establish the scene where they left off

**If new session (no previous sessions):**
1. `start_claudmaster_session(campaign_name="...")` to initialize
2. `get_location` for the starting location
3. Set the opening scene with a vivid description
4. Introduce the world, the PC's situation, and the first hook

### 5. Present the Scene

Following the DM persona's output formatting:
- Use blockquote italics for read-aloud scene text
- Describe what the PC sees, hears, and can interact with
- If AI companions are present, briefly describe what they're doing (e.g., "Lyra checks her spell components while Tormund scans the treeline")
- End with an implicit or explicit prompt for the player's first action

### 6. Await Player Action

Do NOT take any further action. The scene is set — wait for the player to tell you what they do.

**Important for SOLO mode:** After the human player acts, AI companion turns will be handled automatically by the PlayerCharacterAgent system. Do NOT roleplay the AI companions manually — they have their own agent that decides their actions.

## Error Handling

- **No campaigns exist:** "No campaigns found. Let's create one!" → guide through creation
- **No characters in campaign:** "This campaign has no player characters. Let's create your hero!" → guide through character creation
- **Session start fails:** Report the error clearly and suggest checking campaign data.
