#!/usr/bin/env python3
"""Apple Silicon optimized launcher for subreddit finder."""

import importlib
import importlib.util
import os

os.environ.setdefault("APP_PROFILE", "apple_silicon")

if importlib.util.find_spec("uvloop") is not None:
    uvloop = importlib.import_module("uvloop")
    uvloop.install()

from app import run_server

if __name__ == "__main__":
    run_server()
