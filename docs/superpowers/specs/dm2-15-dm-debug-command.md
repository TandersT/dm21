# Spec: /dm:debug — file issues mid-story (DM2-15)

## Problem

During a story session there is no quick way to capture a bug or polish item.
Stopping to file a ticket breaks narrative flow, so issues either derail the
scene or go unrecorded.

## Decision summary (design review, pinned)

| Axis | Decision | Rejected alternatives |
|---|---|---|
| Command name | `/dm:debug` at `.claude/commands/dm/debug.md` | `/dm:bug`, `/dm:report` |
| Filing mechanics | Inline synchronous filing — 3 quick MCP calls, one-line OOC confirmation with ticket ID, resume narrative | Background subagent filing; local capture log flushed at session end |
| Linear destination | Team **Dm21** only, no project — lands in team default state for later triage | Pinning to the "Polish & Fixes" project; auto-labeling |

Settled by the ticket text:

- One-shot interface: `/dm:debug <description>`. Invoked without args, ask
  exactly one short question ("What's the issue?") and use the reply.
- Context payload: campaign name, session number, active character, plus
  location and in-game date (free from `get_game_state`).
- No active session: still file the ticket, noting "no active session" in
  place of play context.

## Behaviour

1. **Capture** the issue description from `$ARGUMENTS` (or the one-question
   fallback). Use the player's words; no editorializing.
2. **Gather context** — two parallel MCP calls:
   - `get_game_state` → campaign name, session number, location, in-game date
   - `list_characters` → active character(s)
3. **File** via `mcp__linear-dm21__save_issue` with `team: "Dm21"`, a concise
   title, and a markdown body containing the description and play context.
   No project, state, labels, priority, or assignee — default triage state.
4. **Confirm and resume** — one out-of-character line with the ticket ID and
   URL, one short scene re-anchor sentence, then await the player's next
   action. No state changes, no time advancement.

## Acceptance criteria mapping

| AC | How it's met |
|---|---|
| Files a Linear ticket on the Dm21 team without derailing the story | Synchronous 3-call flow, no narration of the filing, single OOC confirmation line |
| Ticket auto-includes campaign name, session number, active character | Play-context section in the ticket body from `get_game_state` + `list_characters` |
| Narrative resumes where it left off, no state lost | Command performs no state mutations; ends with a scene re-anchor and waits |

## Error handling

- **Linear call fails:** report in one line, show the composed title/body so
  the player can copy or re-file it, resume the narrative anyway.
- **No description after fallback question:** do not file an empty ticket;
  say so and resume.
- **No active session:** file anyway with "No active session at time of
  filing." in place of play context.

## Out of scope

- Screenshots/attachments, deduplication against existing tickets,
  configurable team/project, batch filing.
