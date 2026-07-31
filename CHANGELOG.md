# Changelog

All notable changes to the VF dashboard are recorded here.

> **How this repo is maintained** — This project is developed with AI coding
> agents (Claude Code) driving the work. Before any large change, a phased
> plan is written to `tmp/plans/` and reviewed, then sub-agents implement it
> phase by phase. If you pick this up, **read the plan files first** — they are
> the source of truth for intent, decisions, and deferred work. See
> "Working with the agents" at the bottom.

## [Unreleased] — DP/IRR restored in production

**Context — why this entry exists.** Commit `191b18d` had refactored the
monolithic `server.py` into `vf_app/`, so local dev and Vercel ran the same
Flask app. Commit `aed73a8` ("DP/IRR") was a **partial revert**: it overwrote
`server.py` and `euler_vf.html` with pre-refactor copies and added the new
DP/IRR feature on top of the *old* monolith. Three consequences, all fixed here:

1. The entire DP/IRR backend existed **only** in `server.py`. `vf_app/` — the
   only backend Vercel runs (`vercel.json` → `api/index.py`) — had zero
   occurrences of `dpirr`. Every DP/IRR request 404'd in production; verified
   live: `GET /api/dpirr_months` → `404 {"error":"Not found"}`.
2. `server.py` was again a 494-line `http.server` monolith with its own Sheets
   client and a hardcoded sheet ID, so `make run` booted something production
   never ran. Local dev could not have caught (1).
3. `euler_vf.html`'s request helper had been reverted to swallow errors
   (`catch -> []`), so those 404s rendered as empty tables instead of a visible
   failure. The DP/IRR section looked merely empty, not broken.

### Added
- **DP/IRR in `vf_app`** — 5 worksheets (`DPIRR_Months`, `DPIRR_Entries`,
  `DPIRR_Products`, `DPIRR_Models`, `DPIRR_Variants`) with their frozen header
  orders, match keys, and alias lists. The generic `/api/<alias>` routes now
  serve the alias-shaped reads, upserts, and deletes.
- `AUTO_CREATE_HEADERS` + `_ws_or_create` in `sheets.py` — the DP/IRR sheets are
  created on first use (500 rows, header row written). The original 8 worksheets
  still raise when missing: a missing one there is a real fault.
- New adapter primitives, all under the existing bounded-retry `call_google` and
  all issuing a **fixed** number of Sheets calls regardless of row count:
  `bulk_update` (two reads + one `batch_update`; applies only the fields in
  `data_dict` and writes exactly `len(headers)` columns), `bulk_delete` (two
  reads + one spreadsheet `batch_update` of `deleteDimension` requests, emitted
  in descending order), `append_rows` (one call), `replace_all` (`clear()` then
  one write from A1). The original `server.py` wrote once per matching row; that
  was survivable on a long-lived server but is a **data-loss bug** under
  Vercel's function timeout — a product delete over 8 models and 120 variants
  was 129 sequential writes (~20-40s, and Google 429s partway, which
  `call_google`'s 8s total-delay cap then exhausts into `UpstreamError`),
  leaving the cascade half-applied. Orphaned child rows are then permanently
  invisible: `euler_vf.html` drops any variant whose product is unknown, and
  there is no UI to clean them up. Both cascade scenarios are now **3 writes**.
- `plan_variant_bulk()` — the bulk-upload diff is now a **pure module-level
  function** (no Sheets access), so the case-insensitive matching, skip-on-blank
  rules, and other-product preservation are unit-testable.
- Explicit routes for the shapes the generic alias routes cannot express:
  `POST /api/dpirr_entries_bulk`, `POST /api/dpirr_variants_bulk`,
  `POST /api/dpirr_products_rename`, `POST /api/dpirr_models_rename`,
  `POST /api/dpirr_variants` (matches on `oldVariant` so a rename lands in
  place), `DELETE /api/dpirr_products` and `DELETE /api/dpirr_models` (both
  cascade into child rows).
- **Guard tests** (165 tests total, up from 76):
  - `GoogleSheetsAdapter` now has direct coverage against a stub spreadsheet
    (gspread never imported): the original 8 aliases are never auto-created and
    still raise on a missing worksheet; the DP/IRR aliases auto-create with the
    exact frozen header row; `bulk_update` preserves untouched columns;
    `bulk_delete` removes only matching rows; `replace_all` writes the header
    row plus every preserved row.
  - **Bounded-call-count guards**: 100 matching rows must produce 1 batched
    write, and a 120-row cascade delete must be 1 spreadsheet request. This is
    the regression that matters — without it a future edit can quietly go back
    to one write per row and the suite stays green.
  - Every `/api/...` path referenced in `euler_vf.html` is issued against the
    real Flask app and must not 404/405. This would have caught the DP/IRR bug
    on day one. It requests rather than just matching the URL map, because the
    `/api/<alias>` converter rule matches any alias and then 404s inside the
    handler.
  - `server.py` must import `api.index` and define no HTTP stack of its own
    (`@app.route`, `*HTTPRequestHandler`, `HTTPServer`).
  - `euler_vf.html` must reject non-2xx.
  - Full route coverage for the new DP/IRR endpoints and `plan_variant_bulk`
    against the in-memory `FakeAdapter` (extended with the new primitives).

### Changed
- `server.py` — restored to the thin dev runner from `191b18d`: loads `.env`,
  imports `api.index.app`, serves on `PORT` (default 9000). `make run`,
  `Procfile`, and `README.md` were already correct and are unchanged.
- `euler_vf.html` — `API._handle` rejects non-2xx again, so a failed read is
  never a silently empty dataset and a failed write is never reported as
  success. The `APP_BASE_PATH` prefixing added by the revert is kept. Every
  DP/IRR call site already wraps its call and shows `setSaveStatus('error')`.
- `euler_vf.html` — the initial load switched from `Promise.all` to
  `Promise.allSettled` over a `READ_PATHS` list. Rejecting non-2xx while keeping
  `Promise.all` would have made the dashboard all-or-nothing: one read returning
  502/504 blanked all 13 sections, where the old swallow-and-`[]` still rendered
  the other 12. (Note the monolith deliberately soft-failed
  `GET /api/snapshots`; `vf_app` has no such special case, so a
  `Monthly_Snapshots` problem would have taken the whole app down.) A failed read
  now empties **that** section and is reported by name in a toast plus a red save
  status; only *every* read failing is a hard error. Writes still fail loud.
- `GET /api/bootstrap` now iterates a new `BOOTSTRAP_ALIASES` (the original 8)
  rather than `READ_ALIASES`. Fanning one request out to 13 sequential Google
  round-trips risks the Vercel function timeout; the DP/IRR section loads via
  its own per-alias reads.

### Decisions & deferred work
- **`/api/taif` is still unimplemented, deliberately.** `euler_vf.html` posts to
  it and no backend has ever had a route or worksheet for it — it was broken
  before this bug and the intended persistence behaviour is an open product
  question. It is now an explicit, commented allowlist entry
  (`UNBACKED_FRONTEND_ROUTES`) in the coverage guard, so the gap is recorded
  rather than silently passing. The old test asserted `API.post('api/taif'`
  *without* the leading slash — a string that never appeared in the file — so it
  passed vacuously; it now asserts reality.
- **Two deliberate behaviour changes vs the monolith:**
  - `esp` is now **required** on `POST /api/dpirr_variants`. The monolith read
    `d["esp"]` (KeyError → 500). An optional `esp` is silently destructive
    because `upsert` rewrites the whole row: omitting it turns an existing
    `['P','M','V1','500']` into `['P','M','V2','']`, destroying the price. The
    frontend already rejects a blank ESP, so there is no live behaviour change.
    A JSON `0` is accepted as `"0"` (a truthiness check would have blanked it).
  - `_require` **strips whitespace** from match keys and required fields, where
    the monolith matched on the raw value. `" Cargo"` and `"Cargo"` are now the
    same identity for these routes. This is deliberate but it *is* a semantic
    difference; if pre-existing rows have leading/trailing spaces in a product,
    model or variant name, they will no longer be matched.
- **Deferred, NOT fixed — `dpirr_variants_bulk` rebuild is read-dependent.**
  It calls `replace_all`, which rewrites the whole `DPIRR_Variants` sheet from
  what the preceding read returned. If that read ever under-reports, the missing
  rows are permanently deleted — and `_manual_parse` does under-report: it skips
  all-blank rows. A safer design would diff-and-patch instead of rewriting.
- **Deferred — `_manual_parse` is effectively dead code for real failures.**
  It is the fallback in `_rows_to_dicts`, but the inner call goes through
  `call_google`, which reclassifies every Google failure into `UpstreamError` /
  `UpstreamTimeoutError`, and `_rows_to_dicts` re-raises those before the
  fallback can run. Only a locally-raised duplicate-header `ValueError` reaches
  it. Left alone: rewiring the retry/fallback boundary is out of scope here.
- **Deferred — `/api/bootstrap` is dead code.** It is implemented, tested, and
  now narrowed to `BOOTSTRAP_ALIASES`, but no frontend calls it: the HTML does
  its own per-alias reads. Either adopt it or delete it; left as-is (out of
  scope, per the brief).

### Frozen data contract — DP/IRR additions
| Endpoint alias | Worksheet | Upsert match keys | Delete key |
|---|---|---|---|
| dpirr_months | DPIRR_Months | `id` | — |
| dpirr_entries | DPIRR_Entries | `id` | `id` |
| dpirr_products | DPIRR_Products | `name` | `name` (cascades to models + variants) |
| dpirr_models | DPIRR_Models | `product,name` | `product,name` (cascades to variants) |
| dpirr_variants | DPIRR_Variants | `product,model,variant` (via `oldVariant`) | `product,model,variant` |

---

## [Unreleased] — Vercel + Jarvis migration

Refactor to deploy this app on **Vercel** as a public Flask function embedded
inside Jarvis. VF no longer has its own login; Jarvis controls who can discover
and open the dashboard. Data store is unchanged (Google Sheets). Not yet
committed/deployed — code + tests only.

Plans: `tmp/plans/01-vf-dashboard-vercel-deployment.md` (this repo),
`02-jarvis-external-apps-gateway.md` and `03-vf-on-jarvis-rollout.md` (the
Jarvis side, implemented in the separate `jarvis` repo).

### Added
- `api/index.py` — Vercel-detected WSGI entry point; exports the Flask `app`, no logic.
- `vf_app/` — the app, split into small modules:
  - `config.py` — env-var validation, fails fast on missing config.
  - `routes.py` — explicit HTML + JSON routes only (no filesystem serving).
  - `sheets.py` — Google Sheets adapter with **bounded** retry (attempt count + total-delay cap; retries 429/timeout only).
  - `errors.py` — stable outward error mapping (400/404/413/502/504), never leaks raw exceptions or secrets.
- `GET /api/bootstrap` — returns all initial dashboard data in one response (was many parallel calls).
- `GET /health` — unauthenticated readiness only; touches no Sheets, exposes no secrets.
- Public Jarvis embed headers — every response includes a `frame-ancestors`
  Content Security Policy allowing production and staging Jarvis origins.
- `vercel.json` — routes to the Flask function, excludes `tests/`, `setup_sheets.py`,
  `credentials.json`, `tmp/`, and docs from the bundle; sets basic security headers.
- `.gitignore` (real one), `.env.example` (names only), `requirements-dev.txt`, `pytest.ini`.
- `tests/` — 83 tests against a fake in-memory Sheets adapter (no live Sheet touched):
  auth/403, route shapes, match-key validation, body-size limits, bootstrap
  contract, error mapping, log redaction, and a static contract check on the HTML.

### Changed
- `server.py` — now a thin **local dev runner** that imports the same Flask app
  (`from api.index import app`). Run locally with `python server.py`.
- `euler_vf.html` / `euler_loan_eligibility.html` — one relative request helper
  that rejects non-2xx (no silent empty datasets); initial load uses `/api/bootstrap`.
- `requirements.txt` — added `Flask`, `python-dotenv`; kept `gspread`, `google-auth`.
- Sheet ID moved from hardcoded constant to `GOOGLE_SHEET_ID` env var.
- `/`, `/eligibility`, and `/api/*` are now public routes; Jarvis controls
  dashboard discovery and entry instead of forwarding requests through a proxy.

### Removed
- Jarvis proxy-secret auth: `JARVIS_PROXY_SECRET` and `X-Jarvis-Proxy-Token`.
- Local login/session system: `LOGIN_ID`/`LOGIN_PASS`, in-memory sessions,
  `/api/login`, `/api/logout`, `X-Session-Token`, `vf_token`, inactivity timer,
  login/logout UI. Auth is now Jarvis's responsibility.
- Hardcoded Railway URL in the eligibility page.
- Wildcard CORS.
- `credentials.json` untracked (`git rm --cached`; file kept on disk, now gitignored).

### Decisions & deferred work
- **TA/IF write disabled.** The frontend called `/api/taif` but no backend route
  or worksheet ever existed. The edit stays in-memory for the session and writes
  nothing. See `tmp/phase0-contract.md`.
- **Google key rotation deferred** (per plan). The credential was un-tracked but
  not rotated — rotate it as a follow-up.
- **Not yet done:** commit + Vercel deploy (plan phases 7–8), and the Jarvis-side
  infra (wildcard DNS/TLS/Ingress) + matching secrets. Deploy steps are in
  `tmp/plans/03-vf-on-jarvis-rollout.md`.

### Frozen data contract (do not change without updating both sides)
| Endpoint alias | Worksheet | Upsert match keys | Delete key |
|---|---|---|---|
| fi_master | FI_Master | `name` | `name` |
| dealer_master | Dealer_Master | `dealerName,location` | `dealerName,location` |
| added_dealers | Added_Dealers | `dealer,location` | `dealer,location` |
| onboarding | FI_Onboarding | `dealer,location,financier` | `dealer,location,financier` |
| fi_policy | FI_Policy | `financier,productKey` | — |
| fi_policy_geo | FI_Policy_Geo | `financier,productKey,seg,state,city` | same 5 |
| dealer_health | Dealer_Health | `dealer,location` | — |
| snapshots | Monthly_Snapshots | append-only | — |

---

## Working with the agents

- **Plans live in `tmp/plans/`.** They are phased. Each phase has explicit exit
  criteria. Read them before editing.
- **Run the tests** before and after any change: `pip install -r requirements-dev.txt`
  then `python -m pytest`. Tests use a fake Sheets adapter, so they never touch a
  real spreadsheet.
- **The HTML files are huge** (`euler_vf.html` ~440 KB). Edit them surgically
  (targeted find/replace), never regenerate wholesale.
- **The proxy contract is shared with Jarvis.** The header name
  `X-Jarvis-Proxy-Token` and the `JARVIS_PROXY_SECRET` value must match the
  corresponding Jarvis environment. Don't rename one side alone.
- **Secrets never go in the repo.** Local values live in an untracked `.env`;
  Preview/Production values live in Vercel env settings.
- **`setup_sheets.py` is an admin-only tool** — never import or run it from
  runtime code, and never run it during a deploy or rollback.
