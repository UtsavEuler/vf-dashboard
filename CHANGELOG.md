# Changelog

All notable changes to the VF dashboard are recorded here.

> **How this repo is maintained** — This project is developed with AI coding
> agents (Claude Code) driving the work. Before any large change, a phased
> plan is written to `tmp/plans/` and reviewed, then sub-agents implement it
> phase by phase. If you pick this up, **read the plan files first** — they are
> the source of truth for intent, decisions, and deferred work. See
> "Working with the agents" at the bottom.

## [Unreleased] — Vercel + Jarvis migration

Refactor to deploy this app on **Vercel** as a Flask function, fronted by the
**Jarvis** external-app gateway. VF no longer has its own login; it trusts a
per-environment proxy secret injected by Jarvis. Data store is unchanged
(Google Sheets). Not yet committed/deployed — code + tests only.

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
- Proxy authentication — every route except `/health` requires header
  `X-Jarvis-Proxy-Token`, compared to `JARVIS_PROXY_SECRET` with a constant-time
  check; missing/wrong → generic `403`.
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

### Removed
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
