# DM2-16 — Implementation plan

**Spec:** `docs/superpowers/specs/dm2-16-adventure-parser-tests.md`

## Task 1 — Repair the two stale cache-path tests

File: `tests/test_adventure_parser.py`

1. `test_download_caching` (~line 573): change the asserted cache path from
   `cache_dir / "adventures" / "cache" / "content" / "LMoP.json"` to
   `... / "lmop.json"`.
2. `test_use_cached_data` (~line 586): change the pre-populated cache path the same
   way, so the parser's lowercase lookup hits the cache and never enters the
   download path.

No product-code changes. No new tests.

## Verification (TDD cycle: red → green on the two stale tests)

1. Red (pre-change, already established): both tests fail on the stale path.
2. Green: `uv run pytest tests/test_adventure_parser.py` — all 27 tests pass.
