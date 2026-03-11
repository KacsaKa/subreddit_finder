"""Core deterministic preprocessing pipeline."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd

from config import PIPELINE_STAGES, REUSABLE_FAQ_KEYWORDS, VECTOR_TYPES
from utils import (
    PipelineIO,
    RunConfig,
    clean_for_match,
    derive_worker_count,
    emit,
    normalize_reddit_df,
    normalize_target_df,
    read_excel_source,
    validate_paths_and_names,
    validate_reddit_columns,
    validate_target_columns,
)

try:
    from rapidfuzz import fuzz
except ImportError:  # optional dependency
    fuzz = None


@dataclass
class PipelineResult:
    outputs: dict[str, pd.DataFrame]
    output_path: str | None
    run_summary: dict[str, object]


class PipelineError(Exception):
    pass


def _score_row(reddit_row: pd.Series, target_df: pd.DataFrame, semantic: bool) -> pd.DataFrame:
    source_text = f"{reddit_row.get('title', '')} {reddit_row.get('selftext', '')}".strip()
    source_clean = clean_for_match(source_text)
    intent_source = reddit_row.get("topic_class_clean", "")

    scored = target_df[[
        "Entity",
        "Cluster",
        "Content_Role",
        "Primary_Query",
        "Intent",
        "Title_HU_SEO",
        "Cannibalization_Group_ID",
        "target_topic_text",
        "target_question_text",
    ]].copy()

    scored["topic_fit"] = scored["target_topic_text"].map(lambda x: _token_overlap(source_clean, x))
    scored["question_usefulness"] = scored["target_question_text"].map(
        lambda x: _token_overlap(source_clean, x)
    )
    scored["intent_compatibility"] = scored["Intent"].map(
        lambda x: 1.0 if clean_for_match(x) and clean_for_match(x) in intent_source else 0.5
    )

    scored["role_compatibility"] = scored["Content_Role"].map(
        lambda x: 1.0 if clean_for_match(x) in source_clean else 0.7
    )

    scored["overlap_awareness"] = scored.apply(
        lambda r: 1.0 - abs(r["topic_fit"] - r["question_usefulness"]) * 0.5, axis=1
    )
    scored["cannibalization_awareness"] = scored["Cannibalization_Group_ID"].map(
        lambda x: 0.9 if pd.notna(x) and str(x).strip() else 0.6
    )

    semantic_bonus = 0.0
    if semantic and fuzz:
        semantic_bonus = scored["target_topic_text"].map(
            lambda x: fuzz.partial_ratio(source_clean, x) / 100.0
        )
    scored["semantic_bonus"] = semantic_bonus

    scored["total_score"] = (
        scored["topic_fit"] * 0.30
        + scored["intent_compatibility"] * 0.15
        + scored["question_usefulness"] * 0.20
        + scored["role_compatibility"] * 0.10
        + scored["overlap_awareness"] * 0.10
        + scored["cannibalization_awareness"] * 0.10
        + scored["semantic_bonus"] * 0.05
    )
    return scored.sort_values("total_score", ascending=False)


def _token_overlap(a: str, b: str) -> float:
    a_tokens = set(a.split())
    b_tokens = set(clean_for_match(b).split())
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / len(a_tokens | b_tokens)


def run_pipeline(
    io_cfg: PipelineIO,
    run_cfg: RunConfig,
    progress: Callable[[float, str], None] | None = None,
    log: Callable[[str], None] | None = None,
    validate_only: bool = False,
) -> PipelineResult:
    start = time.time()

    def update(stage_idx: int, msg: str | None = None) -> None:
        stage = PIPELINE_STAGES[stage_idx]
        progress_value = (stage_idx + 1) / len(PIPELINE_STAGES)
        if progress:
            progress(progress_value, msg or stage)
        emit(log, msg or stage)

    errors = validate_paths_and_names(io_cfg)
    if errors:
        raise PipelineError("\n".join(errors))

    update(0)
    target_df = read_excel_source(io_cfg.target_path, io_cfg.target_sheet, source_tag_column="target_source_sheet")
    reddit_df = read_excel_source(io_cfg.reddit_path, io_cfg.reddit_sheet, source_tag_column="reddit_source_sheet")

    target_errors = validate_target_columns(target_df)
    reddit_errors = validate_reddit_columns(reddit_df)
    if target_errors or reddit_errors:
        raise PipelineError("\n".join(target_errors + reddit_errors))

    if validate_only:
        run_summary = {
            "loaded_target_rows": len(target_df),
            "loaded_reddit_rows": len(reddit_df),
            "target_sheet_mode": io_cfg.target_sheet.strip() or "ALL_SHEETS",
            "reddit_sheet_mode": io_cfg.reddit_sheet.strip() or "ALL_SHEETS",
            "validated_only": True,
        }
        return PipelineResult(outputs={}, output_path=None, run_summary=run_summary)

    update(1)
    target_df = normalize_target_df(target_df)
    reddit_df = normalize_reddit_df(reddit_df)

    update(2)
    pre_filter_count = len(reddit_df)
    filtered = reddit_df[
        (reddit_df["eligable"].str.upper() == "YES")
        & (reddit_df["topic_class_clean"] != "ignore")
        & (~reddit_df["stickied"])
        & (~reddit_df["quarantine"])
        & (reddit_df["age_days"] >= 3)
    ].copy()

    if run_cfg.enable_noise_filtering:
        noise_re = r"daily discussion|megathread|meta|admin"
        filtered = filtered[
            ~filtered["title_clean"].str.contains(noise_re, regex=True, na=False)
        ]

    update(3)
    target_df["target_topic_text"] = (
        target_df["Entity_norm"]
        + " "
        + target_df["Primary_Query_norm"]
        + " "
        + target_df["Title_HU_SEO_norm"]
    ).map(clean_for_match)
    target_df["target_question_text"] = (
        target_df["H1_HU_norm"] + " " + target_df["H2_Questions_norm"]
    ).map(clean_for_match)
    target_df["target_role"] = target_df["Content_Role_norm"]
    target_df["target_cluster"] = target_df["Cluster_norm"]
    target_df["target_cannibalization_group"] = target_df["Cannibalization_Group_ID"]

    filtered["source_topic_text"] = (
        filtered["title_clean"] + " " + filtered["selftext_clean"]
    ).map(clean_for_match)
    filtered["source_question_text"] = filtered["title_clean"]
    filtered["source_quality_bucket"] = pd.cut(
        filtered["engagement_score"],
        bins=[-1, 5, 20, 100000],
        labels=["low", "medium", "high"],
    ).astype(str)

    update(4)
    workers = derive_worker_count(run_cfg.cpu_limit_percent)
    emit(
        log,
        (
            f"CPU strategy: limit={run_cfg.cpu_limit_percent}% | "
            f"logical_cpus best-effort worker cap={workers}."
        ),
    )

    scored_rows: list[dict[str, object]] = []
    primary_assignments: list[dict[str, object]] = []
    secondary_assignments: list[dict[str, object]] = []
    rejection_log: list[dict[str, object]] = []

    for idx, row in filtered.iterrows():
        scored = _score_row(row, target_df, semantic=run_cfg.enable_semantic_matching)
        if scored.empty or scored.iloc[0]["total_score"] < 0.18:
            rejection_log.append(
                {
                    "reddit_index": idx,
                    "title": row.get("title", ""),
                    "reason": "No sufficiently strong deterministic match",
                }
            )
            continue

        top = scored.iloc[0]
        scored_rows.append(
            {
                "reddit_index": idx,
                "reddit_title": row.get("title", ""),
                "primary_entity": top["Entity"],
                "primary_query": top["Primary_Query"],
                "primary_cluster": top["Cluster"],
                "primary_role": top["Content_Role"],
                "primary_score": float(top["total_score"]),
                "topic_fit": float(top["topic_fit"]),
                "intent_compatibility": float(top["intent_compatibility"]),
                "question_usefulness": float(top["question_usefulness"]),
                "role_compatibility": float(top["role_compatibility"]),
                "overlap_awareness": float(top["overlap_awareness"]),
                "cannibalization_awareness": float(top["cannibalization_awareness"]),
            }
        )

        primary_assignments.append(
            {
                "Target_Page": top["Title_HU_SEO"] or top["Primary_Query"],
                "Entity": top["Entity"],
                "Cluster": top["Cluster"],
                "Content_Role": top["Content_Role"],
                "Primary_Query": top["Primary_Query"],
                "Question_Text": row.get("title", ""),
                "Question_Source": "reddit",
                "Assignment_Type": "Primary",
                "Priority": "High",
                "Match_Score": float(top["total_score"]),
            }
        )

        # Secondary assignment rule: next best with different role and similar score.
        secondaries = scored.iloc[1:4]
        for _, sec in secondaries.iterrows():
            if (
                sec["Content_Role"] != top["Content_Role"]
                and sec["Cannibalization_Group_ID"] != top["Cannibalization_Group_ID"]
                and sec["total_score"] >= top["total_score"] * 0.82
            ):
                secondary_assignments.append(
                    {
                        "Target_Page": sec["Title_HU_SEO"] or sec["Primary_Query"],
                        "Entity": sec["Entity"],
                        "Cluster": sec["Cluster"],
                        "Content_Role": sec["Content_Role"],
                        "Primary_Query": sec["Primary_Query"],
                        "Question_Text": row.get("title", ""),
                        "Question_Source": "reddit",
                        "Assignment_Type": "Secondary",
                        "Priority": "Medium",
                        "Match_Score": float(sec["total_score"]),
                    }
                )

    update(5)
    article_question_pool = pd.DataFrame(primary_assignments + secondary_assignments)
    valid_rows = len(primary_assignments)

    question_hub_pool = article_question_pool[
        article_question_pool.get("Content_Role", pd.Series(dtype=str)).eq("Hub")
    ].rename(
        columns={
            "Target_Page": "Target_Article",
            "Cluster": "Cluster",
            "Question_Text": "Question_Text",
        }
    )
    if not question_hub_pool.empty:
        question_hub_pool["Hub_Name"] = question_hub_pool["Target_Article"]
        question_hub_pool = question_hub_pool[
            [
                "Hub_Name",
                "Entity",
                "Cluster",
                "Question_Text",
                "Target_Article",
                "Question_Source",
                "Match_Score",
            ]
        ]

    update(6)
    content_architecture = target_df[
        [
            "Entity",
            "Cluster",
            "Content_Role",
            "Title_HU_SEO",
            "Primary_Query",
            "Intent",
            "Slug_HU",
            "Cannibalization_Group_ID",
            "Architecture_Note",
        ]
    ].copy()
    content_architecture["Parent_Page"] = ""
    content_architecture["Snippet_Target"] = content_architecture["Content_Role"].map(
        lambda x: "Yes" if x == "Supporting" else "No"
    )
    content_architecture = content_architecture[
        [
            "Entity",
            "Cluster",
            "Content_Role",
            "Title_HU_SEO",
            "Primary_Query",
            "Intent",
            "Slug_HU",
            "Cannibalization_Group_ID",
            "Parent_Page",
            "Snippet_Target",
            "Architecture_Note",
        ]
    ]

    if article_question_pool.empty:
        global_faq_pool = pd.DataFrame(columns=["Question_Text", "Category", "Question_Source"])
    else:
        global_faq_pool = article_question_pool[
            article_question_pool["Question_Text"]
            .str.lower()
            .apply(lambda q: any(k in q for k in REUSABLE_FAQ_KEYWORDS))
        ][["Question_Text", "Question_Source"]].copy()
        global_faq_pool["Category"] = "Reusable FAQ"

    answer_vector_plan = pd.DataFrame(
        {
            "Vector_Type": VECTOR_TYPES,
            "Description": [
                "Short definition-style answer",
                "Detailed conceptual explanation",
                "Comparison with alternatives",
                "Risks / caveats / anti-patterns",
                "Practical usage instructions",
                "Legality and compliance framing",
            ],
        }
    )

    candidate_scores = pd.DataFrame(scored_rows)
    rejection_df = pd.DataFrame(rejection_log)

    runtime = round(time.time() - start, 3)
    run_summary = {
        "loaded_target_rows": len(target_df),
        "loaded_reddit_rows": pre_filter_count,
        "filtered_reddit_rows": len(filtered),
        "valid_reddit_rows": valid_rows,
        "primary_assignments": len(primary_assignments),
        "secondary_assignments": len(secondary_assignments),
        "output_sheet_names": ", ".join(
            [
                "content_architecture",
                "article_question_pool",
                "question_hub_pool",
                "global_faq_pool",
                "answer_vector_plan",
                "candidate_scores",
                "rejection_log",
                "run_summary",
            ]
        ),
        "runtime_duration_sec": runtime,
        "cpu_limit_used": run_cfg.cpu_limit_percent,
        "worker_count_used": workers,
        "target_sheet_mode": io_cfg.target_sheet.strip() or "ALL_SHEETS",
        "reddit_sheet_mode": io_cfg.reddit_sheet.strip() or "ALL_SHEETS",
    }

    summary_df = pd.DataFrame(
        [{"metric": key, "value": value} for key, value in run_summary.items()]
    )

    outputs = {
        "content_architecture": content_architecture,
        "article_question_pool": article_question_pool,
        "question_hub_pool": question_hub_pool,
        "global_faq_pool": global_faq_pool,
        "answer_vector_plan": answer_vector_plan,
        "candidate_scores": candidate_scores,
        "rejection_log": rejection_df,
        "run_summary": summary_df,
    }

    update(7)
    output_path = str(Path(io_cfg.output_dir) / io_cfg.output_filename)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for sheet, frame in outputs.items():
            frame.to_excel(writer, sheet_name=sheet[:31], index=False)

    emit(log, f"Export complete: {output_path}")
    return PipelineResult(outputs=outputs, output_path=output_path, run_summary=run_summary)
