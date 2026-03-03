from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

CSV_COLUMNS = [
    "subreddit_name",
    "title",
    "description",
    "subscribers",
    "weekly_contribution",
    "weekly_active_users",
    "date_of_creation",
    "visibility_status",
    "nsfw_flag",
]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in CSV_COLUMNS})
    return path


def _write_parquet(path: Path, rows: list[dict[str, Any]]) -> Path:
    import pyarrow as pa
    import pyarrow.parquet as pq

    data = {column: [row.get(column) for row in rows] for column in CSV_COLUMNS}
    table = pa.table(data)
    pq.write_table(table, path)
    return path


def export_results(
    rows: list[dict[str, Any]],
    output_stem: str,
    directory: str = ".",
    size_threshold_mb: int = 20,
) -> Path:
    out_dir = Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / f"{output_stem}.csv"
    _write_csv(csv_path, rows)

    if csv_path.stat().st_size > size_threshold_mb * 1024 * 1024:
        parquet_path = out_dir / f"{output_stem}.parquet"
        _write_parquet(parquet_path, rows)
        csv_path.unlink(missing_ok=True)
        return parquet_path

    return csv_path
