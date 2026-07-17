#!/usr/bin/env python3
"""Local development runner for the VF dashboard.

This is now a thin wrapper: it imports the SAME Flask app that Vercel serves
(`api.index.app`) and runs it with Flask's built-in server. All routing, auth,
Google Sheets access and error handling live in the `vf_app` package.

    python server.py            # serves on http://localhost:9000

Configuration comes from the environment (see .env.example). A local `.env` is
loaded automatically if python-dotenv is installed.
"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from api.index import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 9000))
    print(f"\n  VF dashboard (Flask) starting on http://localhost:{port}/\n")
    app.run(host="0.0.0.0", port=port)
