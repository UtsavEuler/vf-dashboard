# VF Dashboard

Flask app serving a dashboard backed by ONE Google Sheet, deployed on Vercel.
`vercel.json` routes every path to `api/index.py`, which builds the app from
`vf_app/`. `server.py` is the local dev runner and imports that same app.

## Google calls are the scarce resource

The Sheets API allows **60 read requests per minute per user**, and the service
account is a single user. Every worksheet read, metadata fetch and write spends
one. This quota, not row count or response size, is what actually breaks this
app: overflow returns 429, which reaches the browser as a 502.

Two rules follow, and both are load-bearing:

- **Google call count must never scale with input size.** Not with row count,
  not with alias count, not with match count. A cascade over 130 rows is ONE
  batched write (`bulk_update`, `bulk_delete`); the initial page load is ONE
  `read_many`. Where a loop over rows or aliases would issue a call each, reach
  for `values_batch_get` or `batch_update` instead.
- **Count the calls when reviewing a change.** Before adding a Sheets
  operation, count what it costs and what the enclosing request now totals.
  `tests/test_sheets.py` asserts these counts directly — extend them for new
  operations, because nothing else catches a regression here. The data stays
  correct as call count grows; only the counting tests notice.

Watch for the hidden ones. `gspread`'s `Spreadsheet.worksheet()` re-fetches the
entire spreadsheet metadata on **every** call — the adapter caches worksheet
handles for this reason.

### Adding a worksheet the dashboard reads on load

Add the alias to `INITIAL_LOAD_ALIASES` and to `READ_PATHS` in `euler_vf.html`,
so it rides the existing batch. Adding a parallel `API.get('/api/<alias>')` to
the page instead is what caused the incident: 15 fan-out reads, 15 cold
instances sharing no cache, ~45 Google calls in a two-second burst.

The page keeps a per-alias fan-out as a fallback, reached **only** on 404/405
from the batch route — a stale cached page hitting a deployment that predates
it. It must never run on a 5xx: the batch route failing usually means the quota
is already gone, and answering that with 15 more requests is the burst itself.

## Every request is a cold instance

Serverless gives no shared process state. `_sh`, `_ws_cache` and `_adapter` are
warm-instance optimisations; correctness must hold when every one of them is
empty. Concurrent requests share nothing, so per-instance caching saves calls
*within* a request and nothing at all across a page load.

## Sheets access rules

- **`vf_app/sheets.py` owns the frozen contract.** `WORKSHEETS`, `MATCH_KEYS`
  and the `*_HEADERS` lists are on-sheet column order. Changing one changes a
  live spreadsheet's layout — treat it as a migration, and update the contract
  tests in the same commit.
- **Catch the specific exception.** On the auto-create path, only
  `WorksheetNotFound` may mean "create it". A broad `except Exception` there
  swallows 429s, tries to create a sheet that already exists, and turns a
  retryable rate limit into a permanent 400.
- **A missing original worksheet is a fault.** The eight pre-DP/IRR sheets must
  raise when absent. Only `AUTO_CREATE_HEADERS` aliases may be conjured up.
- **The batched read never writes.** `read_many` returns `[]` for an absent
  auto-creatable sheet. The per-alias `read()` still creates it through
  `_ws_for`, which `test_dpirr_aliases_auto_create_with_frozen_headers` pins —
  so a new read path should follow `read_many`, not `read`.
- **Bound every Google call.** Timeouts come from `google_timeout_seconds`;
  retries go through `call_google`, which retries only 429s and timeouts and
  caps total backoff.

## Errors and secrets

- Raw exception text, tracebacks and Sheet contents never reach the client.
  Raise the typed errors in `vf_app/errors.py`; the registered handlers map them
  to a stable status and a generic message.
- `pinHash` never leaves the process. `dpirr_users` is deliberately absent from
  `READ_ALIASES` — every route returning users filters it explicitly, batch
  endpoints included.
- `credentials.json` is gitignored and untracked. Production reads
  `GOOGLE_CREDENTIALS`; the file is a local-dev fallback only. Keep it out of
  commits and out of the Vercel bundle (`excludeFiles` in `vercel.json`).

## Frontend

`euler_vf.html` is served as a static file with no build step. Two invariants
`tests/test_static_contract.py` enforces against the Flask routes:

- **Failures surface.** `API._handle` rejects every non-2xx, so a failed read is
  never rendered as an empty table. The initial load uses `allSettled` so one
  failure degrades one section, and says so in a toast.
- **Read paths are literal strings.** The contract test extracts them by
  matching literals in `READ_PATHS` and `BATCH_READ_PATH`; a path built by
  concatenation is invisible to it, and so is the route behind it.
- **A section that failed is never rendered as empty.** Both load paths push
  missing or rejected aliases onto `failedReads` and toast them. An alias absent
  from the batch response counts as failed, not as a clean empty table.

## Commands

`make run` (dev, port 9000) · `python -m pytest -q` (tests, no network)
