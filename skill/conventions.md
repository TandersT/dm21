# Conventions, dm20 tool map, and formats

Read this before running `/play`, `/recap`, or starting a new campaign. It holds
the details kept out of SKILL.md: how the multi-hero overlay maps onto dm20's
real tools, the per-hero clock mechanism, the world-ripple convention, the recap
format, and session zero.

## 1. dm20 tool cheat-sheet (real tool names)

Group these by the loop step they serve. Names follow dm20's documented surface;
if one is missing or renamed, adapt from the live tool list.

**CONTEXT (read):** `get_game_state`, `get_character`, `get_npc`,
`get_location`, `list_characters`, `list_quests`, `get_sessions`,
`search_rules`, `get_spell_info`, `get_monster_info`, `get_class_info`.

**EXECUTE (resolve):** `roll_dice` (ALWAYS pass a `label`, e.g.
`label="Aldric Perception check"`), `combat_action`, `start_combat`,
`next_turn`, `end_combat`, `apply_effect`, `remove_effect`.

**PERSIST (write):** `update_character`, `add_item_to_character`,
`update_game_state`, `update_quest`, `add_event`, `add_session_note`,
`create_npc`, `create_location`, `create_quest`, `calculate_experience`,
`long_rest`, `short_rest`.

**SETUP:** `create_campaign`, `load_rulebook`, `create_character`.

Mechanics NEVER come from your imagination — only from these tools.

## 2. Mapping the shared-world model onto dm20

| Concept in this skill | dm20 primitive |
|---|---|
| The shared world | one dm20 campaign |
| A hero | a dm20 character |
| An area / region | a dm20 location |
| World timeline / history | the adventure log (`add_event` / `get_sessions`) |
| A session summary | a session note (`add_session_note`) |
| Open metaplot | quests (`create_quest` / `list_quests`) |
| A hero's current date | a field on that hero's character sheet (see §3) |

## 3. Per-hero clock (the one real piece of glue)

dm20's in-game date in `get_game_state` is **campaign-global** — it can't be
different for two heroes at once. So track each hero's clock on their own sheet:

- Store `Current in-world date: <date>` in the hero's character record — use
  `update_character` to write it into a notes/journal/bio field (whichever the
  sheet exposes). This is the hero's authoritative clock.
- On `/play <hero>`: read that date with `get_character`, then call
  `update_game_state` to set the campaign's active location AND date to that
  hero's values, so dm20's engine operates in the right context for the session.
- As in-world time passes during the session, update the hero's stored date on
  `/recap` (and the global game-state date while they're active).
- Pick one date format at session zero (e.g. "Day 1, 2, 3…" or an in-world
  calendar) and keep it consistent across all heroes so comparisons work.

Result: Hero A can sit on Day 40 while Hero B is on Day 25, in the same world.

## 4. World ripples (cross-thread consistency)

When a hero does something that affects the wider world (kills a noble, burns a
bridge, spreads a rumor, shifts a faction):

- Log it with `add_event`, including the **date** and the **source hero** in the
  text, e.g. `add_event("[Day 38] Aldric exposed the smuggling ring in Silverdale — Harbor Guild now hostile to outsiders. (source: Aldric)")`.
- These entries are the shared timeline. When another hero plays and their clock
  reaches/exceeds that date, surface the aftermath **if** they could plausibly
  know it (same region, news travels, a contact tells them). Otherwise it stays
  off-screen until they'd encounter it.
- If two heroes are in the **same location at overlapping dates**, flag it to the
  player — they can meet, leave word, or affect each other's threads.

## 5. Session Recap format (`/recap`)

Detailed but boiled down — a real chronicle entry, not a transcript. Produce
this text, then store it via `add_session_note`, push ripples via `add_event`,
and advance the hero's date via `update_character`.

```
### Session N — <hero> — [in-world date range] — [location(s)]

**The short of it:** 2–3 sentences capturing the arc of the session.

**Beats:**
- 4–8 bullets of what actually happened, in order. Concrete actions and scenes.

**Decisions & consequences:** Notable choices and what they triggered.

**Met:** NPC name — one line on who they are and the hero's standing with them.

**Gained / lost:** Loot, XP/level-ups, injuries, resources spent.

**Open threads:** Unresolved hooks and goals carried into next session.

**World ripples:** [date] — anything affecting the shared world (→ add_event).
```

## 6. Session zero (new shared world)

Keep it brief and lead from the player's answers:

1. **World:** name, setting, tone, and the date format. `create_campaign`.
2. **Rules:** `load_rulebook source="srd"` (or a custom JSON ruleset for PF2e)
   so character creation auto-populates.
3. **Heroes:** for each, `create_character`; then set their **starting location**
   and **current date** on the sheet (§3). It's fine to start with one hero and
   add others later.
4. **Areas:** `create_location` for each starting area.
5. **Hooks:** `create_quest` for the first goal(s); record world-level threads
   as quests too.
6. **Launch:** `add_session_note` to open Session 1, then begin with `/play <hero>`.

## 7. Failure handling

If a dm20 tool call errors or a tool is absent: read the error, re-check the
available tool list, and adapt (e.g., store the clock in a different sheet field
if the expected one isn't there). Never paper over a missing mechanic by
inventing the result — tell the player what couldn't be resolved and why.
