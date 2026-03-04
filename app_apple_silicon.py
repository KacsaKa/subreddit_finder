#!/usr/bin/env python3
"""Apple Silicon optimized launcher for subreddit finder."""

import os

os.environ.setdefault("APP_PROFILE", "apple_silicon")
os.environ.setdefault("UVLOOP_NO_WARN", "1")

try:
    import uvloop  # type: ignore

    uvloop.install()
except Exception:
    pass

from app import run_server

if __name__ == "__main__":
    run_server()
