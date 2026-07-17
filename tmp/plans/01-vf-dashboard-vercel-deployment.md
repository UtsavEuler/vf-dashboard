# VF dashboard: phased Vercel deployment plan

## Outcome

Deploy this repository as a small Flask application on Vercel. Jarvis proxies every page and API request to it. The VF app no longer has its own user login; it authenticates the Jarvis gateway with a per-environment secret and continues to use Google Sheets as its data store.

```text
Jarvis iframe
  -> vf-dashboard.apps.jarvis.eulerlogistics.com/...
  -> Jarvis external-app gateway
  -> https://<vf-project>.vercel.app/...
  -> Flask + Google Sheets
```

Vercel currently supports Flask through its Python runtime and detects a top-level WSGI `app`. Its Python runtime is still documented as Beta, so preview validation and a rollback target matter. See [Vercel's Flask guide](https://vercel.com/kb/guide/ship-a-flask-app-on-vercel) and [Python runtime documentation](https://vercel.com/docs/functions/runtimes/python).

## Current state

| Area | Current implementation | Deployment implication |
|---|---|---|
| Runtime | `server.py` uses `HTTPServer` | Cannot be deployed as a continuously running server on Vercel; extract a WSGI Flask app. |
| Static pages | `euler_vf.html` and `euler_loan_eligibility.html` | Both should initially be served by protected Flask routes. |
| Data | Google Sheets through `gspread` | Works from a function, but quota, retries and cold starts need limits. |
| Authentication | Local login plus in-memory sessions | Remove after Jarvis proxy authentication exists. |
| Credentials | `credentials.json` is tracked; ignore file is named `gitignore` | Create a real `.gitignore`; production secrets live in Vercel settings. Key rotation is intentionally outside this plan for now. |
| Configuration | Sheet ID is hardcoded | Move it to environment configuration. |
| Eligibility page | Contains a hardcoded Railway URL | Replace with the same relative request helper as the dashboard. |
| TA/IF write | Frontend calls `/api/taif` | No matching route exists in `server.py`; implement it or disable that mutation before release. |
| Tests | None | Add contract tests around the Flask routes and Sheets adapter. |
| Sensitive content | Main HTML contains embedded operational data | Do not publish it as an unrestricted static CDN asset in the first release. |

## Target module shape

```text
api/index.py               # Vercel-detected Flask entry point
vf_app/
  config.py                # validated environment settings
  routes.py                # HTML and JSON HTTP interface
  sheets.py                # Google Sheets adapter
  errors.py                # stable outward error mapping
tests/
  test_routes.py
  test_sheets.py
  test_static_contract.py
vercel.json
.env.example
.gitignore
```

The Flask module's external interface is deliberately small:

```text
GET    /
GET    /eligibility
GET    /health
GET    /api/bootstrap
POST   /api/<supported-resource>
DELETE /api/<supported-resource>
```

All routes except `/health` require the gateway credential. The implementation hides Google authentication, worksheet names, upsert/delete mechanics, retry policy and error translation.

## Phase 0 — Freeze the current contract

### Changes

- Inventory every route used by both HTML pages and map it to its worksheet and read/write behavior.
- Record expected request and response shapes for:
  - financier master
  - dealer master
  - added dealers
  - onboarding
  - financier policy
  - financier policy geography
  - dealer health
  - monthly snapshots
  - TA/IF, if it remains enabled
- Capture a sanitized fixture workbook or fake Sheets adapter for tests.
- Decide whether TA/IF writes are real functionality. If yes, define worksheet and match keys. If no, hide/disable the edit action.
- Keep `setup_sheets.py` as an explicit administrative tool; do not import or execute it from runtime code.

### Exit criteria

- Every browser request has a documented backend route.
- The missing `/api/taif` behavior has an explicit decision.
- No production Sheet mutation is needed during automated tests.

## Phase 1 — Establish safe configuration

### Files

- Add `.gitignore` using the contents of the existing misnamed `gitignore`, plus `.vercel/`, virtual environments and test caches.
- Add `.env.example` containing names only.
- Add `vf_app/config.py` with startup validation.
- Move the hardcoded Sheet ID out of `server.py`.

### Environment interface

```dotenv
GOOGLE_SHEET_ID=
GOOGLE_CREDENTIALS=
JARVIS_PROXY_SECRET=
MAX_REQUEST_BYTES=1048576
GOOGLE_REQUEST_TIMEOUT_SECONDS=10
```

Rules:

- Local values may live in untracked `.env`.
- Preview and production values live in separate Vercel environment settings. Vercel applies environment changes only to new deployments; redeploy after changes. See [Vercel environment variables](https://vercel.com/docs/environment-variables).
- `GOOGLE_CREDENTIALS` remains the complete service-account JSON value.
- Preview and production use different `JARVIS_PROXY_SECRET` values.
- `.env.example` never contains a real Sheet ID, private key or proxy secret.
- Moving the current credential out of the repository is in scope; rotating the already exposed key is deferred per the current instruction.

### Exit criteria

- Import fails fast with a clear configuration error when a required setting is missing.
- `.env`, `.vercel/` and `credentials.json` are ignored by the correctly named `.gitignore`.
- No runtime code depends on `credentials.json` being present.

## Phase 2 — Extract the Flask module

### Files and responsibilities

- `api/index.py`: create/export the WSGI `app`; no business logic.
- `vf_app/routes.py`: define explicit HTML and JSON routes; no generic filesystem serving.
- `vf_app/sheets.py`: move `get_sheet`, worksheet lookup, reads, upserts, deletes and retry behavior.
- `vf_app/errors.py`: map validation failures to `400`, missing resources to `404`, Google failures to `502`, and timeouts to `504` without returning raw exception text.
- `server.py`: temporarily become a thin local runner importing the same Flask app; remove after Vercel parity is proven.
- `requirements.txt`: add pinned compatible versions of Flask and any local environment loader; retain only runtime packages.

### Required behavior

- `/` serves only `euler_vf.html`.
- `/eligibility` serves only `euler_loan_eligibility.html`.
- Unknown file paths return `404`; repository files are never exposed.
- `/health` reports process/config readiness without touching or disclosing Sheet content or secrets.
- Limit JSON body size before parsing.
- Cache the authorized Sheets client only as a warm-instance optimization; correctness cannot depend on warm state.
- Bound retry duration. The current exponential backoff may consume too much of a serverless request.

### Vercel configuration

Add `vercel.json` to:

- bind the supported routes to the Flask entry point;
- set the selected Python function duration/region within the available plan;
- exclude `tests/**`, `setup_sheets.py`, local credentials and other non-runtime files from the Python bundle;
- avoid a catch-all rewrite that accidentally publishes arbitrary repository files.

Vercel's current zero-configuration Flask detection may reduce routing configuration, but keep `vercel.json` for explicit bundle exclusions and security headers. Vercel documents a 500 MB uncompressed Python function bundle limit.

### Exit criteria

- `vercel dev` serves both pages and all documented routes.
- Only the allowlisted pages are downloadable.
- Flask route tests pass against a fake Sheets adapter.

## Phase 3 — Replace duplicate authentication

This phase lands only when the Jarvis gateway can inject the upstream secret.

### Backend removal

From `server.py`/the extracted Flask implementation, remove:

- `LOGIN_ID`, `LOGIN_PASS`, `SESSION_TTL`;
- `_sessions` and its lock;
- session creation, validation and logout;
- `/api/login` and `/api/logout`;
- `X-Session-Token` handling;
- wildcard CORS.

### New upstream authentication

- Require `X-Jarvis-Proxy-Token` on `/`, `/eligibility` and every `/api/*` route.
- Compare with `JARVIS_PROXY_SECRET` using constant-time comparison.
- Reject missing or incorrect values with a generic `403`.
- Do not expose this secret to browser JavaScript. Jarvis adds it after stripping any same-named inbound header.
- Keep `/health` minimal and unauthenticated only if required by Vercel/Jarvis health checks; otherwise use a separate non-sensitive readiness check.

### Frontend removal

From `euler_vf.html` and, where applicable, the eligibility page, remove:

- login overlay and login form;
- password visibility and login handlers;
- logout UI;
- inactivity timer;
- `sessionStorage.vf_token`;
- `X-Session-Token` headers;
- startup session validation.

### Exit criteria

- Direct Vercel requests to HTML and data routes without the proxy secret return `403`.
- A correctly authenticated gateway request works.
- Searching the repository finds no active VF login/session implementation.

## Phase 4 — Normalize browser routing

The app receives its own origin, `vf-dashboard.apps.jarvis.eulerlogistics.com`, so both root-absolute `/api/...` and relative `api/...` requests remain inside VF. Prefer one relative request helper anyway so local, preview and gateway behavior share one implementation.

### Changes

- Introduce one browser-side request helper that resolves against the current document base.
- Replace every root-absolute `/api/...` call with relative URLs such as `api/fi_master`.
- Remove the hardcoded Railway URL from `euler_loan_eligibility.html`.
- Ensure links between dashboard and eligibility remain on the current origin.
- Add `<base>` only if verified against every relative resource; prefer naturally relative URLs.
- Do not register a service worker.
- Make the request helper reject every non-2xx response and show a useful user error. Do not silently convert failed reads into empty datasets.
- Ensure unsafe retries cannot repeat a Sheet write.

### Exit criteria

- Both pages work at Vercel root and through the dedicated VF gateway hostname without HTML/JavaScript rewriting inside Jarvis.
- Refresh and query strings stay on the VF app origin.
- Failed writes are visible and are not presented as successful.

## Phase 5 — Reduce Google Sheets and serverless pressure

### Changes

- Add `GET /api/bootstrap` returning the initial read model needed by the dashboard in one response.
- Change initial page loading from many parallel resource requests to one bootstrap request.
- Keep resource-specific mutation routes for locality and understandable errors.
- Validate required fields and composite match keys before any write/delete.
- Put a maximum total duration on Google retries; retry rate limits and transient upstream failures only.
- Add structured, redacted logs containing route, operation, worksheet alias, outcome and latency—not request/response bodies.

### Exit criteria

- Initial load performs one application data request.
- A Google `429` produces bounded retries and an eventual clear failure.
- Snapshot creation sends exactly one response and does not hide an exception.

## Phase 6 — Tests and release gates

### Python tests

- configuration rejects missing/invalid values;
- wrong/missing proxy secret returns `403`;
- correct secret serves only `/`, `/eligibility` and supported routes;
- malformed and oversized JSON returns `400`/`413`;
- each GET returns its defined shape;
- each POST validates and uses the intended match keys;
- each DELETE requires the complete composite key;
- Google error/timeout maps to stable `502`/`504`;
- bootstrap returns the complete contract;
- logs never contain credentials, proxy secrets or Sheet rows.

### Static contract checks

- Python import/compile;
- extract inline JavaScript and run a syntax check;
- no Railway URL;
- no `/api/login`, `/api/logout`, `vf_token` or `X-Session-Token`;
- all app requests stay on the current origin and no hardcoded deployment hostname remains;
- no service worker;
- no real secrets in tracked configuration.

### Controlled integration checks

- read from a staging/test Sheet;
- perform and roll back one upsert;
- perform and roll back one delete;
- confirm eligibility results;
- confirm TA/IF behavior matches the Phase 0 decision.

## Phase 7 — Vercel deployment setup

### Preview

1. Create/link a Vercel project for this repository.
2. Select Python 3.11 from `.python-version`, subject to current Vercel support.
3. Configure Preview values for `GOOGLE_SHEET_ID`, `GOOGLE_CREDENTIALS`, `JARVIS_PROXY_SECRET` and limits.
4. Use a non-production Sheet where possible.
5. Deploy a preview and record its immutable URL for Jarvis staging.
6. Confirm direct HTML/API requests without the secret return `403`.
7. Confirm `/health` exposes no sensitive fields.

Vercel supports environment-specific and branch-specific values; use separate Preview and Production values rather than copying `.env` into the deployment. See [managing Vercel environments](https://vercel.com/docs/environment-variables/manage-across-environments).

### Production

1. Configure production Sheet credentials and a production-only proxy secret.
2. Deploy to the stable Vercel production hostname.
3. Do not make Vercel's URL a user-facing link.
4. Add only that hostname to the Jarvis production manifest/allowlist.
5. Promote after the end-to-end staging runbook passes.

### Observability

Monitor:

- function invocation failures and duration;
- cold-start latency;
- Google `429`, authentication and timeout failures;
- `403` counts, separated into expected direct traffic and gateway failures;
- bootstrap response size;
- mutation error rate.

## Phase 8 — Cutover and rollback

### Cutover

1. Keep the previous deployment available but remove it from user navigation.
2. Grant VF view permission to a pilot Jarvis role.
3. Grant VF manage permission to a smaller pilot group.
4. Run read, controlled write, delete rollback and eligibility smoke tests.
5. Expand permissions gradually.
6. Retire Railway/old hosting only after a stable observation window.

### Rollback

- Disable/remove the VF manifest from Jarvis first; this blocks new launches.
- Reject existing gateway sessions for inactive apps.
- Roll Vercel back to the previous deployment if the failure is in VF.
- Roll Jarvis back if the gateway itself is faulty.
- Preserve the Sheet; deployment rollback must never run `setup_sheets.py` or mutate schema/data.

## Deferred work

- Rotating the already exposed Google key, per current instruction.
- Rewriting the large HTML application into React or another framework.
- Migrating Google Sheets to a database.
- Publishing static files through the Vercel CDN. Reconsider only after embedded operational data is removed and access expectations are explicit.
- WebSockets, SSE, streaming uploads and browser cookie-based upstream sessions.
