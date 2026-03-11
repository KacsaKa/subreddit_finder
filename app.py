"""Streamlit app for deterministic Reddit-to-target preprocessing."""

from __future__ import annotations

import streamlit as st

from config import (
    APP_SUBTITLE,
    APP_TITLE,
    DEFAULT_CPU_LIMIT_PERCENT,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_OUTPUT_FILENAME,
    MAX_CPU_LIMIT_PERCENT,
    MIN_CPU_LIMIT_PERCENT,
)
from pipeline import PipelineError, run_pipeline
from utils import PipelineIO, RunConfig, detect_cpu_count, read_excel_source

st.set_page_config(page_title=APP_TITLE, layout="wide")

st.title(APP_TITLE)
st.caption(APP_SUBTITLE)

cpu_count = detect_cpu_count()
status_col1, status_col2, status_col3 = st.columns(3)
status_col1.metric("Platform", "macOS/Apple Silicon-ready")
status_col2.metric("Detected logical CPUs", cpu_count)
status_col3.metric("Default CPU limit", f"{DEFAULT_CPU_LIMIT_PERCENT}%")

st.markdown("---")

left_col, right_col = st.columns(2)

with left_col:
    st.subheader("Embedding / Cannibalization Source")
    target_path = st.text_input("Excel file path", value="", key="target_path")
    target_sheet = st.text_input("Sheet name (optional; blank = all sheets)", value="", key="target_sheet")

with right_col:
    st.subheader("Reddit Source")
    reddit_path = st.text_input("Excel file path", value="", key="reddit_path")
    reddit_sheet = st.text_input("Sheet name (optional; blank = all sheets)", value="", key="reddit_sheet")

st.markdown("### Run Configuration")
out_col1, out_col2 = st.columns(2)
with out_col1:
    output_dir = st.text_input("Output directory", value=DEFAULT_OUTPUT_DIR)
    output_filename = st.text_input("Output filename", value=DEFAULT_OUTPUT_FILENAME)

with out_col2:
    cpu_limit = st.slider(
        "CPU usage limit percent (best-effort)",
        min_value=MIN_CPU_LIMIT_PERCENT,
        max_value=MAX_CPU_LIMIT_PERCENT,
        value=DEFAULT_CPU_LIMIT_PERCENT,
        step=5,
    )
    enable_semantic = st.checkbox(
        "Enable semantic matching if optional libraries are available", value=False
    )
    enable_noise = st.checkbox("Enable optional noise filtering", value=True)
    preview_rows = st.checkbox("Preview first rows before run", value=True)

run_col, validate_col = st.columns([1, 1])
run_clicked = run_col.button("Run Pipeline", type="primary", use_container_width=True)
validate_clicked = validate_col.button(
    "Validate Inputs Only", type="secondary", use_container_width=True
)

progress_bar = st.progress(0)
stage_placeholder = st.empty()
log_placeholder = st.empty()
error_placeholder = st.empty()
success_placeholder = st.empty()

if "logs" not in st.session_state:
    st.session_state.logs = []


def append_log(message: str) -> None:
    st.session_state.logs.append(message)
    st.session_state.logs = st.session_state.logs[-300:]
    log_placeholder.code("\n".join(st.session_state.logs), language="text")


def update_progress(value: float, message: str) -> None:
    progress_bar.progress(min(max(float(value), 0.0), 1.0))
    stage_placeholder.info(message)


def show_preview(path: str, sheet_name: str, title: str) -> None:
    try:
        df = read_excel_source(path, sheet_name)
        mode = sheet_name.strip() or "ALL_SHEETS"
        st.markdown(f"#### Preview: {title}")
        st.caption(f"Sheet mode: {mode}")
        st.write("Columns:", list(df.columns))
        st.dataframe(df.head(5), use_container_width=True)
    except Exception as exc:  # preview should not stop main workflow
        st.warning(f"Could not preview {title}: {exc}")


if preview_rows:
    preview_col1, preview_col2 = st.columns(2)
    with preview_col1:
        if target_path:
            show_preview(target_path, target_sheet, "Embedding / Cannibalization Source")
    with preview_col2:
        if reddit_path:
            show_preview(reddit_path, reddit_sheet, "Reddit Source")

if run_clicked or validate_clicked:
    st.session_state.logs = []
    error_placeholder.empty()
    success_placeholder.empty()

    io_cfg = PipelineIO(
        target_path=target_path,
        target_sheet=target_sheet,
        reddit_path=reddit_path,
        reddit_sheet=reddit_sheet,
        output_dir=output_dir,
        output_filename=output_filename,
    )
    run_cfg = RunConfig(
        cpu_limit_percent=cpu_limit,
        enable_semantic_matching=enable_semantic,
        enable_noise_filtering=enable_noise,
        preview_rows=preview_rows,
    )

    try:
        result = run_pipeline(
            io_cfg=io_cfg,
            run_cfg=run_cfg,
            progress=update_progress,
            log=append_log,
            validate_only=validate_clicked,
        )

        if validate_clicked:
            success_placeholder.success(
                "Validation succeeded. Files and required columns look correct."
            )
            st.json(result.run_summary)
        else:
            success_placeholder.success(
                f"Pipeline complete. Workbook exported to: {result.output_path}"
            )
            st.markdown("### Run Summary")
            st.json(result.run_summary)

            st.markdown("### Output Preview")
            for sheet_name, frame in result.outputs.items():
                with st.expander(sheet_name, expanded=False):
                    st.dataframe(frame.head(20), use_container_width=True)

    except PipelineError as exc:
        error_placeholder.error(f"Validation / pipeline error:\n{exc}")
        append_log(f"ERROR: {exc}")
    except Exception as exc:  # safeguard for unexpected runtime issues
        error_placeholder.exception(exc)
        append_log(f"UNEXPECTED ERROR: {exc}")

st.markdown("---")
st.caption(
    "Best-effort CPU limiting is implemented via worker-count capping and predictable "
    "processing strategy, not hard realtime CPU enforcement."
)
