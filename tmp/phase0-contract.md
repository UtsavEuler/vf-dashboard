# Phase 0 — Frozen backend contract

This is the authoritative inventory of every HTTP route the two HTML pages call,
mapped to its Google Sheets worksheet, read/write behaviour and composite match
keys. These names and shapes are load-bearing: Jarvis and the Sheet depend on
them, so they must not change during the refactor.

## Worksheet aliases

| API alias (`/api/<alias>`) | Worksheet title    |
|----------------------------|--------------------|
| `fi_master`                | `FI_Master`        |
| `dealer_master`            | `Dealer_Master`    |
| `added_dealers`            | `Added_Dealers`    |
| `onboarding`               | `FI_Onboarding`    |
| `fi_policy`                | `FI_Policy`        |
| `fi_policy_geo`            | `FI_Policy_Geo`    |
| `dealer_health`            | `Dealer_Health`    |
| `snapshots`                | `Monthly_Snapshots`|

## Routes

| Method | Route                    | Worksheet          | Behaviour           | Match / composite keys                                   |
|--------|--------------------------|--------------------|---------------------|----------------------------------------------------------|
| GET    | `/api/fi_master`         | FI_Master          | read all rows       | —                                                        |
| GET    | `/api/dealer_master`     | Dealer_Master      | read all rows       | —                                                        |
| GET    | `/api/added_dealers`     | Added_Dealers      | read all rows       | —                                                        |
| GET    | `/api/onboarding`        | FI_Onboarding      | read all rows       | —                                                        |
| GET    | `/api/fi_policy`         | FI_Policy          | read all rows       | —                                                        |
| GET    | `/api/fi_policy_geo`     | FI_Policy_Geo      | read all rows       | —                                                        |
| GET    | `/api/dealer_health`     | Dealer_Health      | read all rows       | —                                                        |
| GET    | `/api/snapshots`         | Monthly_Snapshots  | read all rows       | —                                                        |
| GET    | `/api/bootstrap`         | all eight above    | read model (new)    | — (returns all eight lists in one response)              |
| POST   | `/api/fi_master`         | FI_Master          | upsert              | `name`                                                   |
| POST   | `/api/dealer_master`     | Dealer_Master      | upsert              | `dealerName`, `location`                                 |
| POST   | `/api/added_dealers`     | Added_Dealers      | upsert              | `dealer`, `location`                                     |
| POST   | `/api/onboarding`        | FI_Onboarding      | upsert              | `dealer`, `location`, `financier`                        |
| POST   | `/api/fi_policy`         | FI_Policy          | upsert              | `financier`, `productKey`                                |
| POST   | `/api/dealer_health`     | Dealer_Health      | upsert              | `dealer`, `location`                                     |
| POST   | `/api/fi_policy_geo`     | FI_Policy_Geo      | upsert              | `financier`, `productKey`, `seg`, `state`, `city`        |
| POST   | `/api/snapshots`         | Monthly_Snapshots  | append (fixed cols) | — (appends a row using the fixed snapshot header order)  |
| DELETE | `/api/fi_master`         | FI_Master          | delete row          | `name`                                                   |
| DELETE | `/api/dealer_master`     | Dealer_Master      | delete row          | `dealerName`, `location`                                 |
| DELETE | `/api/added_dealers`     | Added_Dealers      | delete row          | `dealer`, `location`                                     |
| DELETE | `/api/onboarding`        | FI_Onboarding      | delete row          | `dealer`, `location`, `financier`                        |
| DELETE | `/api/fi_policy_geo`     | FI_Policy_Geo      | delete row          | `financier`, `productKey`, `seg`, `state`, `city`        |

Notes:
- `fi_policy` and `dealer_health` have **no** DELETE route (matches `server.py`).
- `snapshots` has **no** DELETE route and its POST **appends** (never upserts);
  it writes exactly the fixed column order captured in `SNAPSHOT_HEADERS`.
- DELETE keys arrive as query-string args; POST keys arrive in the JSON body.

## Response shapes

- Every GET returns a JSON **array of row objects** (`list[dict]`); keys are the
  worksheet's header row. An empty/missing worksheet returns `[]`.
- `GET /api/bootstrap` returns a JSON **object** with exactly these keys, each a
  `list[dict]`: `fi_master`, `dealer_master`, `added_dealers`, `onboarding`,
  `fi_policy`, `fi_policy_geo`, `dealer_health`, `snapshots`.
- POST/DELETE return `{"ok": true}` on success.

## TA/IF decision

The dashboard's TA/IF editor called `POST /api/taif`, but `server.py` never had a
matching route and there is no `TA/IF` worksheet in the workbook. Per the plan we
do **not** invent a worksheet.

**Decision: the TA/IF mutation (the "save edit" write) is DISABLED in the
frontend.** The TA/IF view/table still renders from in-page data; the edit modal's
save no longer performs a network write. This is documented in a code comment at
the former `API.post('/api/taif', ...)` call site in `euler_vf.html`. No
`/api/taif` route exists on the backend.

## Pages

| Route         | File                          |
|---------------|-------------------------------|
| `/`           | `euler_vf.html`               |
| `/eligibility`| `euler_loan_eligibility.html` |
| `/health`     | readiness JSON (no Sheets, no secrets, unauthenticated) |

All routes except `/health` require the `X-Jarvis-Proxy-Token` header equal to
`JARVIS_PROXY_SECRET`.

## Test data

Tests use a fake in-memory Sheets adapter (see `tests/conftest.py`); no real Sheet
is touched. `setup_sheets.py` stays an admin-only tool and is never imported by
runtime code.
