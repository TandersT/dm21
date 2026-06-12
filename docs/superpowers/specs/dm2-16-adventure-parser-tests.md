# DM2-16 — Fix pre-existing test failures in test_adventure_parser.py

**Ticket:** [DM2-16](https://linear.app/dm21/issue/DM2-16/fix-pre-existing-test-failures-in-test-adventure-parserpy)
**Size:** Small
**Base:** sta/dm2-15-dm-debug-command

## Problem

Two tests in `tests/test_adventure_parser.py` fail on `main` (pre-existing, confirmed
on 842d8b7). The other 25 tests in the file pass. (The ticket says "36 others / all
38" — a triage-time miscount; the file collects 27 tests.)

- `test_download_caching` — asserts the cache file at
  `adventures/cache/content/LMoP.json`, which is never created.
- `test_use_cached_data` — pre-populates the cache at the same mixed-case path, then
  fails with `TypeError: Object of type coroutine is not JSON serializable`.

## Root cause (verified)

`AdventureParser` intentionally normalizes adventure IDs to lowercase for both cache
reads (`src/dm20_protocol/adventures/parser.py:138-140`) and cache writes (line 191) —
introduced in commit 716024e to keep URL/cache naming consistent with 5etools'
lowercase IDs (see `docs/assets/BUGREPORT-load_adventure.md`). The tests still use the
stale mixed-case path `LMoP.json`:

- Write side (`test_download_caching`): the parser writes `lmop.json`; the assertion
  on `LMoP.json` fails.
- Read side (`test_use_cached_data`): the parser looks for `lmop.json`, misses the
  pre-populated `LMoP.json`, and falls into the download path. The bare
  `patch("httpx.AsyncClient")` there yields AsyncMock coroutines, which reach
  `json.dumps` — the `TypeError` is a downstream symptom of the cache miss, not a
  separate bug.

## Decision (pinned in design review)

**Fix tests only: switch both tests to the normalized `lmop.json` cache path.**

- No product-code change — the lowercase normalization is intentional, documented
  behavior.
- No additional regression test — the two repaired tests themselves pin the
  normalization contract: write-side in `test_download_caching`, read-side in
  `test_use_cached_data`.

### Alternatives considered (rejected in design review)

- Change the parser to preserve original-case cache filenames — rejected: the
  normalization is intentional (716024e) and matches 5etools URL conventions.
- Add a dedicated case-normalization regression test — rejected: redundant; the two
  repaired tests already cover both sides of the contract.

## Acceptance criteria

- [ ] `uv run pytest tests/test_adventure_parser.py` passes with no failures (all 27)
