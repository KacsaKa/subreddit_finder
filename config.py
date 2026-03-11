"""Configuration and constants for the Streamlit preprocessing app."""

from __future__ import annotations

from pathlib import Path

APP_TITLE = "Reddit → Content Architecture Preprocessor"
APP_SUBTITLE = (
    "Deterministic Step 1 preprocessing for SEO/AEO assignment. "
    "No content generation is performed."
)

DEFAULT_OUTPUT_FILENAME = "reddit_preprocessing_output.xlsx"
DEFAULT_CPU_LIMIT_PERCENT = 75
MIN_CPU_LIMIT_PERCENT = 25
MAX_CPU_LIMIT_PERCENT = 75

PIPELINE_STAGES = [
    "Stage 1/8: Loading Excel files",
    "Stage 2/8: Normalizing columns",
    "Stage 3/8: Filtering Reddit rows",
    "Stage 4/8: Building helper representations",
    "Stage 5/8: Matching Reddit rows to targets",
    "Stage 6/8: Resolving assignments",
    "Stage 7/8: Building output tables",
    "Stage 8/8: Exporting workbook",
]

CONTENT_ROLE_MAP = {
    "Pillar_Candidate": "Pillar",
    "Hub_Candidate": "Hub",
    "Supporting_Candidate": "Supporting",
}

TARGET_COLUMN_ALIASES = {
    "Primary_Query_HU": "Primary_Query",
    "Intent_keyword": "Intent",
    "Content_Role_Candidate": "Content_Role",
}

TARGET_REQUIRED_BASE = ["Entity", "Cluster", "Cannibalization_Group_ID"]
TARGET_REQUIRED_ANY = {
    "Primary_Query": ["Primary_Query", "Primary_Query_HU"],
    "Intent": ["Intent", "Intent_keyword"],
    "Content_Role": ["Content_Role", "Content_Role_Candidate"],
}

REDDIT_REQUIRED_COLUMNS = [
    "title",
    "selftext",
    "score",
    "num_comments",
    "upvote_ratio",
    "subreddit",
    "stickied",
    "quarantine",
    "age_days",
    "engagement_score",
    "eligable",
    "topic_class",
]

REUSABLE_FAQ_KEYWORDS = {"legal", "safety", "beginner", "basics", "how to start"}

VECTOR_TYPES = ["Definition", "Explanation", "Comparison", "Risk", "Usage", "Legality"]

DEFAULT_OUTPUT_DIR = str(Path.cwd())
