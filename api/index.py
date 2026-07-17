"""Vercel Python entry point.

Vercel's Python runtime detects a top-level WSGI callable named `app`. This
module only wires the application together; all behaviour lives in `vf_app`.
"""

import os
import sys

# Ensure the repository root is importable when Vercel loads this file directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vf_app import create_app  # noqa: E402

app = create_app()
