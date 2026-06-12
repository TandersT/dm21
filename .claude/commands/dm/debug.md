---
description: File a bug or polish issue to Linear mid-story, then drop straight back into the narrative.
argument-hint: <short issue description>
allowed-tools: mcp__dm20-protocol__get_game_state, mcp__dm20-protocol__list_characters, mcp__linear-dm21__save_issue
---

# DM Debug

File an issue to Linear without ending or derailing the story.

## Usage
```
/dm:debug <short issue description>
```

One-shot: the issue description comes from the arguments. If invoked with no
arguments, ask exactly one short question — "What's the issue?" — and use the
reply as the description. Ask nothing else.

## Instructions

This is an out-of-character utility, not a scene. Keep it fast: capture,
gather context, file, confirm, resume. Do not narrate the filing.

### 1. Capture the Description

Take the issue description from `$ARGUMENTS` (or the one-question fallback).
Use the player's words — light cleanup is fine, but don't expand, interpret,
or speculate about causes.

### 2. Gather Play Context

Call in parallel:
- `get_game_state` — campaign name, session number, location, in-game date
- `list_characters` — player character(s)

If there is no active session or campaign (e.g. `get_game_state` returns "No
game state available."), skip to step 3 and file anyway — the ticket notes
the missing context instead.

### 3. File the Ticket

```
mcp__linear-dm21__save_issue(
  team="Dm21",
  title="[concise restatement of the issue, ≤ 70 chars]",
  description="[body template below]"
)
```

Set **only** `team`, `title`, and `description`. No project, state, labels,
priority, or assignee — the ticket lands in the team's default state for
later triage.

Body template:

```markdown
## Issue

[player's description]

## Play context

- Campaign: [campaign name]
- Session: [session number]
- Character(s): [player character name(s)]
- Location: [current location]
- In-game date: [in-game date]

*Filed via `/dm:debug`.*
```

With no active session, replace the play-context list with:

```markdown
- No active session at time of filing.
```

### 4. Confirm and Resume

Confirm in a single out-of-character line with the ticket ID and URL:

```
---
**Filed [DM2-NN](url):** [title] — back to the story.
```

Then re-anchor the scene in one short sentence (where the character is and
what was happening) and wait for the player's next action. Do not advance
time, change game state, or re-narrate the scene.

## Error Handling

- **Linear call fails:** report the error in one line, show the composed
  title and body so the player can copy or re-file it, then resume the
  narrative anyway.
- **No description after the fallback question:** don't file an empty
  ticket — say so and resume.
