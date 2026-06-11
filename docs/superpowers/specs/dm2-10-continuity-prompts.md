# DM2-10 — DM prompts: continuity protocol

Ticket: [DM2-10](https://linear.app/dm21/issue/DM2-10/update-dm-prompts-with-continuity-protocol)
Size: Standard. Branch: `sta/dm2-10-continuity-prompts` (base: `sta/dm2-9-read-tool-upgrades`).

## Problem

The prompts are why the DM never read stored history — the original
continuity failure happened with all the data already in storage. DM2-5
through DM2-9 shipped the tools (fact graph dual-write, explicit knowledge
write tools, the recap tool, read upgrades), but no prompt tells the DM to
use them:

- `start.md`'s resume flow (Step 4, ~lines 337-348) says "read the last
  session note" — which means truncated `get_sessions` output and nothing
  from the fact graph.
- `dm-persona.md` never mentions `record_party_fact` /
  `record_npc_interaction`, and its Resume Session subsection still
  describes the pre-recap flow.
- `save.md` writes the session note but never checks whether facts learned
  or meaningful NPC interactions during the session were recorded.

New tools without prompt changes leave the bug live.

## Decisions (user-pinned)

1. **Resume flow in start.md is recap-centric**: `get_session_recap()` +
   `party_knowledge()` upfront; `get_sessions(detail="full")` +
   `get_events(session_number=<last>)` only as FALLBACK when the recap is
   unavailable or empty. *Justified deviation from the ticket's literal
   three-tool list:* DM2-8's recap already embeds the session's journal
   events verbatim, while `get_events` truncates descriptions to 150 chars —
   calling both upfront would be redundant and the recap is strictly richer.
   Noted in the PR description; Linear description corrected if warranted.
2. **Self-heal is conditional**: if the recap or `party_knowledge` come back
   empty BUT session notes exist (the campaign has history), run
   `sync_facts()` once and retry the recap. Not run unconditionally.
3. **dm-persona.md gets a dedicated Continuity Protocol section**, with the
   triggers wired into the Core Game Loop's PERSIST step and the Social
   tool-usage pattern, and the stale Resume Session subsection updated to
   match the new start.md flow. Guidance framing ("a fact the party would
   act on later", "an interaction that changes the relationship"), not
   hard MUST-everything rules.
4. **save.md gets an introspective pre-save sweep**: before
   `add_session_note`, review the session for unrecorded facts / meaningful
   interactions and record each via `record_party_fact` /
   `record_npc_interaction`, relying on their idempotency (duplicates
   converge to no-ops).

Carried-forward implementation details (prior run, not design axes): new
tools go into each command's `allowed-tools` frontmatter where the prompt
calls them (start.md: `get_session_recap`, `party_knowledge`, `sync_facts`,
`get_events` — `get_sessions` already listed; save.md: `record_party_fact`,
`record_npc_interaction`); prompt edits follow each file's existing voice
and step structure.

## Design

### 1. `start.md` — Step 4 resume flow (pins 1 + 2)

Frontmatter: append `mcp__dm20-protocol__get_session_recap`,
`mcp__dm20-protocol__party_knowledge`, `mcp__dm20-protocol__sync_facts`,
`mcp__dm20-protocol__get_events` to `allowed-tools`.

The "If resuming" numbered list is rewritten:

1. `get_session_recap()` — narrative recap, key events, active quests,
   unresolved threads, NPC reminders, plus the last session's journal
   events verbatim.
2. `party_knowledge()` — everything the party has learned; established
   canon the recap narration must not contradict.
3. Conditional self-heal (pin 2): recap or party knowledge empty but
   session notes exist → `sync_facts()` once, retry `get_session_recap()`.
4. Fallback (pin 1): recap still unavailable (e.g. fact graph can't load)
   → reconstruct from `get_sessions(detail="full")` +
   `get_events(session_number=<last session>)`.
5.–8. Existing steps preserved: `get_location`, `get_character` per PC,
   "Previously..." recap woven into narrative (now anchored to the recap
   output — established details are canon), re-establish the scene.

The "If new session" branch and the session-existence check are untouched.

### 2. `dm-persona.md` — Continuity Protocol + wiring (pin 3)

- **New `## Continuity Protocol` section** after Tool Usage Patterns:
  the campaign's memory lives in the fact graph, not the conversation.
  Two triggers, guidance-framed:
  - party learns a fact they would act on later → `record_party_fact`
    (content, category, source, method);
  - an interaction changes the relationship with an NPC → 
    `record_npc_interaction` (npc, interaction_type, summary).
  Plus the negative space (scenery, small talk, mechanical results — the
  journal already captures those via `add_event`) and the idempotency note
  (when in doubt, record — duplicates converge).
- **PERSIST step** (Core Game Loop 4): two new bullets for
  `record_party_fact` / `record_npc_interaction`, each pointing at the
  Continuity Protocol for the trigger.
- **Social pattern** (Tool Usage Patterns): chain extended with the
  Continuity Protocol writes between `add_event` and narrate.
- **Resume Session subsection** (Session Management): rewritten to the
  recap-centric flow — `get_session_recap` + `party_knowledge`, conditional
  `sync_facts` retry, `get_sessions(detail="full")` +
  `get_events(session_number=<last>)` fallback — mirroring start.md Step 4
  in the persona's terse numbered style.

### 3. `save.md` — pre-save sweep (pin 4)

Frontmatter: append `mcp__dm20-protocol__record_party_fact`,
`mcp__dm20-protocol__record_npc_interaction` to `allowed-tools`.

New step **3. Continuity Sweep** between "Generate Session Summary" and
"Save Session Note" (subsequent steps renumber 4–8): review the session
for (a) facts the party learned that they'd act on later and that were
never recorded → `record_party_fact`; (b) meaningful NPC interactions that
changed a relationship → `record_npc_interaction`. Idempotency makes
over-recording safe; if nothing qualifies, move on — don't invent facts.

## Tool signatures referenced (verified against `src/dm20_protocol/main.py`)

- `get_session_recap(session_number=None, length="standard", style="narrative")`
- `party_knowledge(topic="", source_filter=None, method_filter=None)`
- `sync_facts()`
- `get_sessions(detail: "summary"|"full" = "summary")`
- `get_events(limit=None, event_type=None, search=None, session_number=None)`
- `record_party_fact(content, category, source, method, session=None, location=None, notes=None)`
- `record_npc_interaction(npc, interaction_type, summary, session=None, player_characters=None, location=None)`

## Acceptance criteria → design trace

- Resume flow calls the recap/knowledge tools instead of truncated
  summaries → Design 1 (pin 1; recap-centric deviation noted).
- Continuity Protocol in the persona: fact learned → `record_party_fact`,
  meaningful NPC interaction → `record_npc_interaction` → Design 2 (pin 3).
- Pre-save sweep in save.md ("any facts learned this session not yet
  recorded?") → Design 3 (pin 4).

## Out of scope

- Any Python/tool changes — this ticket is prompt-only.
- Adding `record_*` tools to start.md's `allowed-tools` (gameplay writes
  happen after the command completes; the persona carries the guidance).
- Other `/dm:*` commands (refrill, party-mode, profile, …).

## Testing

Prompt-only change — no new automated tests. Verification:

- Full repo test suite once (regression guard — `!`cat`` include paths and
  tool registration untouched).
- Grep `src/dm20_protocol/main.py` to confirm every tool name + parameter
  referenced by the edited prompts exists with that exact signature.
