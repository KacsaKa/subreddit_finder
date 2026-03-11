# subreddit_finder

Local Streamlit app for deterministic **Step 1** SEO/AEO preprocessing.

It loads two Excel files, validates and normalizes schemas, filters Reddit rows,
assigns primary/secondary target pages deterministically, and exports a
multi-sheet Excel workbook.

---

## 1) Project files and what each one does

- `app.py`  
  Streamlit UI. This is the entrypoint you run.
- `pipeline.py`  
  Core 8-stage preprocessing pipeline.
- `utils.py`  
  Validation, text normalization, and CPU helper utilities.
- `config.py`  
  Constants/defaults (stage names, schema aliases, limits, etc.).

---

## 2) Prerequisites (macOS Apple Silicon)

### A. Install Python 3.10+
Use python.org installer or Homebrew.

### B. Create and activate a virtual environment
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### C. Install required packages
```bash
pip install --upgrade pip
pip install streamlit pandas openpyxl
```

### D. (Optional) Install matching quality extras
```bash
pip install rapidfuzz scikit-learn sentence-transformers psutil numpy
```

> The app works without optional packages. Optional semantic matching only applies
> when optional libraries are installed.

---

## 3) Step-by-step execution plan

### Step 1 — Start the app (run this file)
```bash
streamlit run app.py
```

### Step 2 — Fill in source inputs in the UI
In the app:

1. **Embedding / Cannibalization Source**
   - Excel file path
   - Sheet name (optional; leave blank to process all sheets)
2. **Reddit Source**
   - Excel file path
   - Sheet name (optional; leave blank to process all sheets)

### Step 3 — Configure run settings
In **Run Configuration** set:

- Output directory
- Output filename (`.xlsx`)
- CPU usage limit % (default 75, min 25, max 75)
- Optional toggles:
  - semantic matching
  - optional noise filtering
  - preview first rows

### Step 4 — Validate first (recommended)
Click **Validate Inputs Only**.

Use this to confirm:
- file paths are correct,
- sheet names exist,
- required columns are present.

### Step 5 — Run full pipeline
Click **Run Pipeline**.

You will see:
- global progress bar,
- current stage,
- live logs,
- errors (if any),
- final success message with output path.

### Step 6 — Open exported workbook
The app writes one Excel workbook with sheets:

1. `content_architecture`
2. `article_question_pool`
3. `question_hub_pool`
4. `global_faq_pool`
5. `answer_vector_plan`
6. `candidate_scores`
7. `rejection_log`
8. `run_summary`

---

## 4) Which Python file should I execute, and when?

- **Normal usage:** run **only** `app.py` with Streamlit:
  ```bash
  streamlit run app.py
  ```
- `pipeline.py`, `utils.py`, and `config.py` are imported by `app.py`; you do not
  execute them directly for standard operation.

---

## 5) Optional quick syntax check

```bash
python -m py_compile app.py pipeline.py utils.py config.py
```

