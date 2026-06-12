# Continuity Graph v1
**Linear:** https://linear.app/dm21/project/continuity-graph-v1-c99fce64d35e
**Sealed:** 2026-06-11
**Run ID:** wfl-2026-06-11-191601

## What this milestone delivered

dm20-protocol's dormant `claudmaster/consistency` fact graph (FactDatabase, NPCKnowledgeTracker, PartyKnowledge, SessionRecapGenerator) is now wired into the play loop, so the AI DM stops forgetting established events on resume. The milestone originated from a lived bug: the DM contradicted session history on resume (forgot the party had visited the church and met Donavich) because `get_sessions` truncated summaries, the resume flow never read the adventure log, and the fact graph had zero production write paths. v1 closes that loop end to end: every entity/journal write now feeds the graph automatically, explicit tools record what the party learned, `get_session_recap` and upgraded read tools surface it back untruncated, and the DM prompts actually invoke all of it on resume, during play, and at save time. Campaigns that predate the graph self-heal through one idempotent `sync_facts` backfill.

## Tickets included

| Ticket | What it did | PR |
|---|---|---|
| DM2-5 | FactIngest dual-write pipeline: single ingestion adapter (`src/dm20_protocol/consistency/fact_ingest.py`) with deterministic fact ids and merge-preserve upserts, hooked into the five entity/journal write tools; `sync_facts` backfill = journal replay + campaign entity sweep; fact graph cached on storage mirroring the DiscoveryTracker lifecycle | [#1](https://github.com/TandersT/dm20-protocol/pull/1) |
| DM2-7 | Explicit knowledge write tools: `record_party_fact` (content-derived `pfact_` ids + `learn_fact` dedupe) and `record_npc_interaction` (strict NPC resolution, idempotent `ingest_npc` upsert, exact `(npc, summary, session)` dedupe) | [#2](https://github.com/TandersT/dm20-protocol/pull/2) |
| DM2-8 | `get_session_recap` MCP tool wiring SessionRecapGenerator to the cached storage accessors; SessionRecap dataclass gains `verbatim_events` (the session's journal events, untruncated, importance-sorted), populated via an events parameter injected by the tool; `session_number=None` resolves to the latest session with journal data | [#3](https://github.com/TandersT/dm20-protocol/pull/3) |
| DM2-9 | Read-tool upgrades: `get_sessions(detail="full")` expands the latest session untruncated; `get_events(session_number=N)` filter at tool and storage layers; `get_npc` appends a continuity block (first met / last seen / interaction count) after output filtering | [#4](https://github.com/TandersT/dm20-protocol/pull/4) |
| DM2-10 | Prompt activation: recap-centric resume flow in `.claude/commands/dm/start.md` with conditional `sync_facts` self-heal; Continuity Protocol in `.claude/dm-persona.md` wired into the Core Game Loop's PERSIST step and Social pattern; introspective pre-save sweep in `.claude/commands/dm/save.md` | [#5](https://github.com/TandersT/dm20-protocol/pull/5) |

## How the pieces fit together

**Write path.** Every play-loop write reaches the fact graph through one adapter, `FactIngest` (`src/dm20_protocol/consistency/fact_ingest.py`). The five MCP write tools in `src/dm20_protocol/main.py` (`add_event`, `create_npc`, `create_location`, `create_quest`, `update_quest`) dual-write after each storage write: events become `evt_*` facts (with automatic PlayerInteraction records for registered NPCs named in `characters_involved`), entities become facts with deterministic ids — crucially, an NPC's fact id equals its entity id. Two explicit tools cover what auto-ingestion can't infer: `record_party_fact` marks free-text knowledge as party-known, and `record_npc_interaction` records "properly met" semantics. For campaigns whose journals predate all of this, `sync_facts` replays the journal and sweeps campaign entities through the same adapter; deterministic ids plus merge-preserve upserts make every path idempotent, so dual-write, explicit records, and backfill converge instead of duplicating.

**Graph and lifecycle.** The per-campaign FactDatabase, NPCKnowledgeTracker, and PartyKnowledge are held on `DnDStorage` (loaded on campaign load/switch, cleared on close, degrading to `None` on failure), mirroring the pre-existing DiscoveryTracker pattern. Merge-preserve upserts refresh derivable fields (content, category, quest resolution tags) while preserving what other subsystems attached (party-known tags, related facts, first-established session).

**Read path.** `get_session_recap` assembles the full resume picture from the graph — "previously on" narrative, key events, active quests, unresolved threads, NPC reminders — plus the session's journal events verbatim (untruncated, importance-sorted), so established detail can't be contradicted. The NPC reminders join tracker interactions to NPC facts by id, which is why the write path's id equality matters. `get_npc` shows the same met-state as a continuity block, and `get_sessions(detail="full")` / `get_events(session_number=N)` provide the untruncated fallback view when the graph is unavailable.

**Activation.** DM2-10 makes the prompts fire all of it: `/dm:start` resumes recap-first (`get_session_recap` + `party_knowledge`), self-heals pre-graph campaigns with one conditional `sync_facts`, and falls back to `get_sessions(detail="full")` + `get_events(session_number=<last>)` if the graph can't load; the persona's PERSIST step and Social pattern trigger the record tools during play; `/dm:save` runs an idempotency-backed sweep for unrecorded facts before writing session notes. The cross-ticket roundtrips are locked by the seal suite at `tests/test_milestone_seal_continuity_graph.py`.

## Key design decisions

All design axes were pinned by the user during the run; the one override is flagged.

- **DM2-5 — dual-write hook at the tool layer**, not inside storage: `adventures/tools.py` bulk-creates module NPCs/locations/quests on import, and a storage-layer hook would flood the graph with hundreds of un-met NPCs, polluting recap and party-knowledge consumers.
- **DM2-5 — `sync_facts` = journal replay + entity sweep**, beyond the ticket's literal "replay the journal": NPCs/quests/locations live in entity stores, not the journal, so journal-only backfill would leave the recap's NPC-reminder join empty for all historical data.
- **DM2-5 — storage-held graph accessors** mirroring the DiscoveryTracker precedent: one campaign-lifecycle authority, established failure-to-None degradation.
- **DM2-7 — content-derived fact ids** (`pfact_<sha256(content)[:12]>`): repeated identical calls converge end-to-end, extending DM2-5's deterministic-id philosophy to free-text facts.
- **DM2-7 — strict NPC resolution + idempotent `ingest_npc` upsert** in `record_npc_interaction`: keeps entity stores authoritative while guaranteeing the NPC fact id == entity id invariant the recap join depends on, even for pre-graph NPCs.
- **DM2-7 — interaction dedupe on exact `(npc, summary, session)`**: guards against tool retries while keeping legitimate cross-session repeats recordable (PlayerInteraction has no id field; summary is the only key).
- **DM2-8 — "latest" = max session number over journal events**, falling back to the game-state counter: the recap must cover the last session that actually has content, not a freshly bumped counter.
- **DM2-8 — USER OVERRIDE (Q2): verbatim events carried on the `SessionRecap` dataclass** (`verbatim_events` field, events injected by the tool) instead of the recommended tool-side markdown append — the user wanted a single assembly point for the recap; the generator stays storage-free by taking the events as a parameter.
- **DM2-8 — all session events verbatim**, importance desc then timestamp, untruncated: any importance floor or top-N cap can silently drop established details — the exact failure mode the milestone exists to fix.
- **DM2-9 — `detail="full"` expands only the latest session; session filter uniform across tool and storage; continuity block appended after output filtering for all callers** (met-state is party-facing knowledge by definition), omitted gracefully when the tracker is unavailable.
- **DM2-10 — recap-centric resume** rather than the ticket-literal three-tool list: `get_events` truncates descriptions to 150 chars while the recap embeds the same events verbatim; **conditional `sync_facts`** only when recap/party knowledge come back empty but session notes exist; **triggers wired into PERSIST/Social** rather than a standalone section that risks the original inertness; **introspective pre-save sweep** relying on tool idempotency.

## What's NOT in this milestone

- `get_npc` resolution is still exact, case-sensitive name match; the lenient `_resolve_npc` helper (id / case-insensitive name) is only used by `record_npc_interaction` (sweep follow-up candidate).
- `record_npc_interaction` dedupe is exact-summary only — a rephrased summary of the same interaction records a duplicate (sweep info finding).
- ~~The adventure log is global with no campaign attribution: `sync_facts` may ingest events from other campaigns sharing the same data directory (the tool warns about this in its output).~~ Resolved by DM2-14: split-format campaigns now keep a per-campaign log (`campaigns/{name}/adventure_log.json`) with one-shot migration of the legacy global log; `sync_facts` replays only the current campaign's events.
- TimelineTracker exists in `claudmaster/consistency` but is not wired into the recap (`get_session_recap` passes `timeline=None`).
- Entity stores are name-keyed: re-creating an NPC/location/quest under an existing name mints a new entity id and orphans the old fact (flagged in PR #1 as a follow-up for the read path).
- The fact graph is available for split-format campaigns only; legacy-format campaigns degrade to `None` accessors and the tools report "could not be loaded".

## Tests

Seal suite: `tests/test_milestone_seal_continuity_graph.py` — 8 cross-ticket integration tests over real storage + fact graph (tmp-path isolation, no fakes across ticket boundaries):

- `test_party_fact_recorded_during_play_surfaces_in_resume_recap` — explicit party fact (DM2-7) shows up in the resume recap and party_knowledge query (DM2-8).
- `test_events_and_quests_written_during_play_feed_recap_without_sync` — dual-write alone (DM2-5) is enough for the recap (DM2-8); no backfill call needed.
- `test_quest_completed_during_play_drops_out_of_recap_threads` — quest resolution tagging (DM2-5) drives the recap's unresolved-thread filtering (DM2-8).
- `test_npc_met_during_play_is_remembered_by_recap_and_npc_lookup` — NPC writes (DM2-5/DM2-7) join into recap NPC reminders (DM2-8) and the get_npc continuity block (DM2-9) on the NPC fact id == entity id invariant.
- `test_recap_and_session_filtered_events_agree_on_latest_session` — recap latest-session resolution (DM2-8) agrees with `get_events(session_number)` (DM2-9).
- `test_pre_dual_write_campaign_self_heals_with_one_sync_facts_call` — the epic's motivating scenario: storage-seeded pre-graph campaign → one `sync_facts` (DM2-5) → populated recap (DM2-8) and continuity block (DM2-9), the self-heal flow DM2-10's resume prompt encodes.
- `test_explicit_records_converge_with_sync_facts_backfill` — explicit records (DM2-7) survive repeated `sync_facts` replays (DM2-5) without duplication.
- `test_resume_falls_back_to_full_session_detail_when_fact_graph_unavailable` — DM2-10's documented fallback: recap degrades (DM2-8) → `get_sessions(detail="full")` + `get_events(session_number)` (DM2-9) still restore the session untruncated.

Per-ticket tests live in each PR: `tests/test_fact_ingest.py`, `tests/test_fact_graph_lifecycle.py`, `tests/test_fact_dual_write.py` (PR #1), `tests/test_knowledge_write_tools.py` (PR #2), `tests/test_session_recap_tool.py`, `tests/claudmaster/test_recap_generator.py` (PR #3), `tests/test_read_tool_upgrades.py` (PR #4).
