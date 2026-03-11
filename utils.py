"""Validation and helper utilities for the preprocessing pipeline."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd

from config import (
    CONTENT_ROLE_MAP,
    REDDIT_REQUIRED_COLUMNS,
    TARGET_COLUMN_ALIASES,
    TARGET_REQUIRED_ANY,
    TARGET_REQUIRED_BASE,
)


@dataclass
class PipelineIO:
    target_path: str
    target_sheet: str
    reddit_path: str
    reddit_sheet: str
    output_dir: str
    output_filename: str


@dataclass
class RunConfig:
    cpu_limit_percent: int
    enable_semantic_matching: bool
    enable_noise_filtering: bool
    preview_rows: bool


def detect_cpu_count() -> int:
    """Return logical CPU count with a defensive minimum."""
    count = os.cpu_count() or 1
    return max(1, int(count))


def derive_worker_count(cpu_limit_percent: int, cpu_count: int | None = None) -> int:
    """Best-effort worker cap from CPU limit.

    This intentionally *does not* attempt hard realtime CPU enforcement. Instead,
    it caps potential parallel work by reducing worker count from logical CPUs.
    """
    cpus = cpu_count or detect_cpu_count()
    bounded = max(1, min(int(cpu_limit_percent), 100))
    workers = max(1, int(cpus * (bounded / 100.0)))
    return min(workers, cpus)


def normalize_text(value: object) -> str:
    text = "" if pd.isna(value) else str(value)
    return re.sub(r"\s+", " ", text).strip()


def clean_for_match(value: object) -> str:
    text = normalize_text(value).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def to_bool(value: object) -> bool:
    val = normalize_text(value).lower()
    return val in {"true", "1", "yes", "y"}


def validate_paths_and_names(io_cfg: PipelineIO) -> list[str]:
    errors: list[str] = []
    if not Path(io_cfg.target_path).exists():
        errors.append(f"Target file not found: {io_cfg.target_path}")
    if not Path(io_cfg.reddit_path).exists():
        errors.append(f"Reddit file not found: {io_cfg.reddit_path}")

    # Sheet names are optional: blank means read and combine all sheets in the workbook.

    if not io_cfg.output_dir.strip():
        errors.append("Output directory is required.")
    else:
        Path(io_cfg.output_dir).mkdir(parents=True, exist_ok=True)

    if not io_cfg.output_filename.strip().lower().endswith(".xlsx"):
        errors.append("Output filename must end with .xlsx")

    return errors


def read_excel_source(path: str, sheet_name: str, source_tag_column: str = "source_sheet") -> pd.DataFrame:
    """Read one sheet if provided, else combine all sheets from the workbook.

    If `sheet_name` is blank, all sheets are loaded and concatenated with a helper
    column that records originating sheet names.
    """
    workbook = pd.ExcelFile(path, engine="openpyxl")
    selected_sheet = normalize_text(sheet_name)

    if selected_sheet:
        if selected_sheet not in workbook.sheet_names:
            available = ", ".join(workbook.sheet_names)
            raise ValueError(
                f"Sheet '{selected_sheet}' not found in {path}. Available sheets: {available}"
            )
        frame = pd.read_excel(workbook, sheet_name=selected_sheet, engine="openpyxl")
        frame[source_tag_column] = selected_sheet
        return frame

    frames: list[pd.DataFrame] = []
    for name in workbook.sheet_names:
        frame = pd.read_excel(workbook, sheet_name=name, engine="openpyxl")
        frame[source_tag_column] = name
        frames.append(frame)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def validate_target_columns(df: pd.DataFrame) -> list[str]:
    errors: list[str] = []
    cols = set(df.columns)
    for col in TARGET_REQUIRED_BASE:
        if col not in cols:
            errors.append(f"Target source missing required column: {col}")

    for normalized_name, options in TARGET_REQUIRED_ANY.items():
        if not any(opt in cols for opt in options):
            errors.append(
                f"Target source requires one of {options} to populate '{normalized_name}'"
            )
    return errors


def validate_reddit_columns(df: pd.DataFrame) -> list[str]:
    missing = [c for c in REDDIT_REQUIRED_COLUMNS if c not in df.columns]
    return [f"Reddit source missing required column: {col}" for col in missing]


def normalize_target_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for source, target in TARGET_COLUMN_ALIASES.items():
        if source in out.columns and target not in out.columns:
            out[target] = out[source]

    if "Content_Role" not in out.columns:
        out["Content_Role"] = ""

    out["Content_Role"] = (
        out["Content_Role"].fillna("").astype(str).replace(CONTENT_ROLE_MAP)
    )

    helper_cols = [
        "Entity",
        "Primary_Query",
        "Intent",
        "Cluster",
        "Title_HU_SEO",
        "H1_HU",
        "H2_Questions",
        "Slug_HU",
        "Content_Role",
    ]
    for col in helper_cols:
        if col not in out.columns:
            out[col] = ""
        out[f"{col}_norm"] = out[col].map(normalize_text)
        out[f"{col}_clean"] = out[col].map(clean_for_match)

    return out


def normalize_reddit_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ["title", "selftext", "subreddit", "topic_class", "eligable"]:
        out[col] = out[col].map(normalize_text)
        out[f"{col}_clean"] = out[col].map(clean_for_match)

    for col in ["stickied", "quarantine"]:
        out[col] = out[col].map(to_bool)

    for col in ["age_days", "score", "num_comments", "engagement_score", "upvote_ratio"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)

    return out


def emit(log: Callable[[str], None], message: str) -> None:
    if log:
        log(message)
