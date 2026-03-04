#!/usr/bin/env python3
"""Windows optimized launcher for subreddit finder."""

import os

from app import run_server

os.environ.setdefault("APP_PROFILE", "windows")

if __name__ == "__main__":
    run_server()
