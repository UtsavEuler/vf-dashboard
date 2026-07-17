# VF Dashboard

Flask app that serves the Vehicle Finance (VF) dashboard, backed by a Google
Sheet. The same app (`api/index.py`) runs both locally and on Vercel.

## Prerequisites

- Python 3.11 (see `.python-version`)
- A `.env` file with the required configuration (see `.env.example`)

## Configuration

Copy `.env.example` to `.env` and fill in the values:

| Variable | Required | Description |
| --- | --- | --- |
| `GOOGLE_SHEET_ID` | yes | ID of the Google Sheet (from its URL) |
| `GOOGLE_CREDENTIALS` | yes | Full service-account JSON as a single-line string |
| `JARVIS_PROXY_SECRET` | yes | Shared secret sent by Jarvis as `X-Jarvis-Proxy-Token` |
| `MAX_REQUEST_BYTES` | no | Max request body size (default `1048576`) |
| `GOOGLE_REQUEST_TIMEOUT_SECONDS` | no | Google API timeout (default `10`) |

Locally, if `GOOGLE_CREDENTIALS` is unset, the app falls back to a
`credentials.json` file if present. Production must always set the env var.

## Running

```sh
make run          # create venv, install deps, start on http://localhost:9000
make run PORT=8080
```

Or manually:

```sh
pip install -r requirements.txt
python server.py
```

## Testing

```sh
make test
```

## Endpoints

- `GET /health` — health check, returns 200, no secrets.
- `GET /` — serves the dashboard. Requires the `X-Jarvis-Proxy-Token` header
  matching `JARVIS_PROXY_SECRET`; returns 403 otherwise.

## Make targets

```sh
make help
```
