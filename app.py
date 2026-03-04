#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import csv
import html
import logging
import os
import platform
import queue
import re
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("subreddit_finder_web")

EXPORT_COLUMNS = [
    "subreddit_name",
    "subreddit_url",
    "description",
    "subscribers",
    "active_users",
]

CURATED_SYNONYMS = {
    "housing": [
        "rent",
        "rental",
        "real estate",
        "tenant",
        "landlord",
        "mortgage",
        "apartment",
        "home",
        "affordable housing",
        "property",
    ]
}


@dataclass(slots=True)
class RedditCredentials:
    client_id: str = ""
    client_secret: str = ""
    username: str = ""
    password: str = ""
    user_agent: str = "subreddit-finder-web/1.0"


class RedditAPIError(RuntimeError):
    pass


class RedditClient:
    BASE_URL = "https://oauth.reddit.com"
    TOKEN_URL = "https://www.reddit.com/api/v1/access_token"

    def __init__(self, creds: RedditCredentials, requests_per_second: float = 2.0) -> None:
        self.creds = creds
        self.auth_mode = bool(creds.client_id and creds.client_secret)
        try:
            import httpx  # type: ignore
        except Exception as exc:
            raise RuntimeError("Missing dependency 'httpx'. Install it with: pip install httpx pyarrow") from exc
        self._client = httpx.AsyncClient(timeout=30.0)
        self._token: str | None = None
        self._public_bases = ["https://www.reddit.com", "https://old.reddit.com"]
        self._token_expires_at = 0.0
        self._last_request = 0.0
        self._rps = requests_per_second
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()

    async def _authenticate(self) -> None:
        data = {"grant_type": "client_credentials"}
        if self.creds.username and self.creds.password:
            data = {
                "grant_type": "password",
                "username": self.creds.username,
                "password": self.creds.password,
            }

        response = await self._client.post(
            self.TOKEN_URL,
            data=data,
            auth=(self.creds.client_id, self.creds.client_secret),
            headers={"User-Agent": self.creds.user_agent},
        )
        response.raise_for_status()
        payload = response.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.time() + int(payload.get("expires_in", 3600)) - 60

    async def _ensure_token(self) -> None:
        if not self.auth_mode:
            return
        if not self._token or time.time() >= self._token_expires_at:
            await self._authenticate()

    async def discover_from_public_html(self, term: str) -> list[str]:
        """Fallback when public JSON search gets blocked."""
        headers = {"User-Agent": self.creds.user_agent}
        names: set[str] = set()
        for base in self._public_bases:
            try:
                url = f"{base}/subreddits/search"
                resp = await self._client.get(url, params={"q": term}, headers=headers)
                if resp.status_code >= 400:
                    continue
                for m in re.finditer(r"/r/([A-Za-z0-9_]+)/", resp.text):
                    names.add(m.group(1))
                if names:
                    break
            except Exception:
                continue
        return sorted(names)

    async def request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with self._lock:
            await self._ensure_token()
            min_delay = 1.0 / self._rps
            elapsed = time.time() - self._last_request
            if elapsed < min_delay:
                await asyncio.sleep(min_delay - elapsed)

            headers = {"User-Agent": self.creds.user_agent, "Accept": "application/json"}
            req_params = dict(params or {})
            req_params.setdefault("raw_json", 1)
            if self.auth_mode:
                req_urls = [f"{self.BASE_URL}{path}"]
                headers["Authorization"] = f"bearer {self._token}"
            else:
                if path.startswith("/subreddits/search"):
                    req_urls = [f"{base}{path}.json" for base in self._public_bases]
                else:
                    req_urls = [f"{self._public_bases[0]}{path}.json"]

            last_error: str | None = None
            for attempt in range(1, 4 if not self.auth_mode else 6):
                for url in req_urls:
                    response = await self._client.get(url, params=req_params, headers=headers)
                    self._last_request = time.time()

                    if response.status_code in {429, 500, 502, 503, 504}:
                        last_error = f"{response.status_code} transient error"
                        continue

                    if response.status_code == 403 and not self.auth_mode:
                        last_error = f"403 from {url}"
                        continue

                    if response.status_code >= 400:
                        raise RedditAPIError(f"{path} failed: {response.status_code} {response.text[:300]}")

                    return response.json()

                await asyncio.sleep(min(2**attempt, 8 if not self.auth_mode else 12))

            raise RedditAPIError(
                f"{path} failed in public mode after retries ({last_error}). "
                "Reddit may block anonymous search; set OAuth env vars for reliable access."
            )


def expand_keyword(keyword: str, min_terms: int = 8, max_terms: int = 12) -> list[str]:
    root = keyword.strip().lower()
    terms = [root]
    for item in CURATED_SYNONYMS.get(root, []):
        if item not in terms:
            terms.append(item)

    fallback = [
        f"{root} advice",
        f"{root} community",
        f"{root} help",
        f"{root} discussion",
        f"{root} support",
        f"{root} resources",
    ]
    for item in fallback:
        if len(terms) >= min_terms:
            break
        if item not in terms:
            terms.append(item)

    return terms[:max_terms]


async def discover_subreddits(
    client: RedditClient,
    terms: list[str],
    per_term_limit: int = 100,
    progress_cb: Callable[[int, int], None] | None = None,
) -> list[str]:
    discovered: dict[str, str] = {}
    for idx, term in enumerate(terms, start=1):
        after: str | None = None
        fetched = 0
        while True:
            try:
                payload = await client.request(
                    "/subreddits/search",
                    params={
                        "q": term,
                        "type": "sr",
                        "sort": "relevance",
                        "limit": 100,
                        "after": after,
                        "include_over_18": "on",
                    },
                )
            except RedditAPIError as err:
                if not client.auth_mode and "403" in str(err):
                    logger.warning("Public mode blocked for term '%s' with over18=on, retrying relaxed search.", term)
                    try:
                        payload = await client.request(
                            "/subreddits/search",
                            params={
                                "q": term,
                                "type": "sr",
                                "sort": "relevance",
                                "limit": 100,
                                "after": after,
                            },
                        )
                    except RedditAPIError as err2:
                        logger.warning("JSON search still blocked for '%s' (%s). Trying HTML fallback.", term, err2)
                        names = await client.discover_from_public_html(term)
                        if names:
                            for n in names:
                                discovered.setdefault(n.lower(), n)
                        break
                else:
                    raise
            data = payload.get("data", {})
            children: list[dict[str, Any]] = data.get("children", [])
            if not children:
                break

            for item in children:
                name = item.get("data", {}).get("display_name")
                if name:
                    discovered.setdefault(name.lower(), name)

            fetched += len(children)
            after = data.get("after")
            if not after or fetched >= per_term_limit:
                break

        if progress_cb:
            progress_cb(idx, len(terms))

    return sorted(discovered.values())


def keyword_frequency_score(text: str, terms: list[str]) -> float:
    lowered = text.lower()
    return float(sum(lowered.count(term.lower()) for term in terms))


async def collect_metrics(
    client: RedditClient,
    names: list[str],
    terms: list[str],
    concurrency: int,
    progress_cb: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(concurrency)
    done = 0

    async def worker(name: str) -> dict[str, Any] | None:
        nonlocal done
        async with sem:
            try:
                about_payload = await client.request(f"/r/{name}/about")
                about = about_payload.get("data", {})
            except Exception as exc:
                logger.warning("Failed for %s: %s", name, exc)
                result = None
            else:
                description = " ".join(
                    filter(None, [about.get("public_description", ""), about.get("description", "")])
                ).strip()
                relative = about.get("url") or f"/r/{about.get('display_name', name)}/"
                subreddit_url = urllib.parse.urljoin("https://www.reddit.com", relative)
                row: dict[str, Any] = {
                    "subreddit_name": about.get("display_name", name),
                    "subreddit_url": subreddit_url,
                    "description": description,
                    "subscribers": int(about.get("subscribers", 0) or 0),
                    "active_users": int(about.get("accounts_active", 0) or 0),
                }
                row["score"] = keyword_frequency_score(f"{row['subreddit_name']} {row['description']}", terms)
                result = row

            done += 1
            if progress_cb:
                progress_cb(done, len(names))
            return result

    data = [row for row in await asyncio.gather(*(worker(name) for name in names)) if row is not None]
    data.sort(key=lambda x: (x["score"], x["subscribers"]), reverse=True)
    return data


def export_results(rows: list[dict[str, Any]], output_stem: str = "results", out_dir: str = "exports") -> str:
    path = Path(out_dir)
    path.mkdir(parents=True, exist_ok=True)

    csv_path = path / f"{output_stem}.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in EXPORT_COLUMNS})

    if csv_path.stat().st_size > 20 * 1024 * 1024:
        import pyarrow as pa
        import pyarrow.parquet as pq

        parquet_path = path / f"{output_stem}.parquet"
        table = pa.table({column: [row.get(column) for row in rows] for column in EXPORT_COLUMNS})
        pq.write_table(table, parquet_path)
        csv_path.unlink(missing_ok=True)
        return str(parquet_path)

    return str(csv_path)


JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()
JOB_QUEUE: queue.Queue[str] = queue.Queue()
WORKERS_STARTED = False


def update_job(job_id: str, **kwargs: Any) -> None:
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(kwargs)


def get_job_workers() -> int:
    raw = os.getenv("JOB_WORKERS", "1").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 1
    return max(1, min(2, n))


def compute_safe_concurrency() -> int:
    cpu_count = os.cpu_count() or 1
    profile = os.getenv("APP_PROFILE", "default").strip().lower()
    if profile == "apple_silicon" and platform.system() == "Darwin":
        return max(6, min(12, cpu_count))
    return max(4, min(10, cpu_count))


async def run_pipeline(keyword: str, job_id: str) -> tuple[list[dict[str, Any]], str]:
    creds = RedditCredentials(
        client_id=os.getenv("REDDIT_CLIENT_ID", ""),
        client_secret=os.getenv("REDDIT_CLIENT_SECRET", ""),
        username=os.getenv("REDDIT_USERNAME", ""),
        password=os.getenv("REDDIT_PASSWORD", ""),
        user_agent=os.getenv("REDDIT_USER_AGENT", "subreddit-finder-web/1.0"),
    )

    concurrency = compute_safe_concurrency()
    client = RedditClient(creds)
    mode = "oauth" if client.auth_mode else "public"
    update_job(job_id, cpu_workers=concurrency, phase=f"starting ({mode} mode)", progress=5, auth_mode=mode)
    try:
        terms = expand_keyword(keyword)

        def discovery_progress(done: int, total: int) -> None:
            pct = 5 + int((done / max(1, total)) * 30)
            update_job(job_id, phase="discovering subreddits", progress=min(pct, 35), done=done, total=total)

        names = await discover_subreddits(client, terms, progress_cb=discovery_progress)
        if not names:
            mode_msg = "OAuth app credentials recommended" if not client.auth_mode else "Try a broader keyword"
            raise RuntimeError(f"No subreddits discovered for '{keyword}'. Public search likely blocked (403). {mode_msg}.")
        update_job(job_id, phase="collecting metrics", progress=35, done=0, total=len(names))

        def metrics_progress(done: int, total: int) -> None:
            pct = 35 + int((done / max(1, total)) * 55)
            update_job(job_id, phase="collecting metrics", progress=min(pct, 90), done=done, total=total)

        rows = await collect_metrics(client, names, terms, concurrency=concurrency, progress_cb=metrics_progress)
    finally:
        await client.close()

    update_job(job_id, phase="exporting", progress=95)
    output = export_results(rows, output_stem=f"subreddit_results_{keyword}_{job_id}")
    update_job(job_id, phase="done", progress=100)
    return rows, output


def _queue_worker() -> None:
    while True:
        job_id = JOB_QUEUE.get()
        with JOBS_LOCK:
            job = JOBS.get(job_id)
        if not job:
            JOB_QUEUE.task_done()
            continue

        update_job(job_id, status="running", started_at=time.time(), phase="starting")
        try:
            rows, output = asyncio.run(run_pipeline(str(job["keyword"]), job_id))
            update_job(job_id, status="done", rows=rows, output=output, progress=100, finished_at=time.time())
        except Exception as exc:
            update_job(job_id, status="error", error=str(exc), finished_at=time.time())
        finally:
            JOB_QUEUE.task_done()


def ensure_workers_started() -> None:
    global WORKERS_STARTED
    if WORKERS_STARTED:
        return
    for _ in range(get_job_workers()):
        threading.Thread(target=_queue_worker, daemon=True).start()
    WORKERS_STARTED = True


def start_job(keyword: str) -> str:
    ensure_workers_started()
    job_id = str(uuid.uuid4())
    with JOBS_LOCK:
        JOBS[job_id] = {
            "keyword": keyword,
            "status": "queued",
            "rows": [],
            "error": "",
            "output": "",
            "phase": "queued",
            "progress": 0,
            "done": 0,
            "total": 0,
            "cpu_workers": 0,
            "auth_mode": "public",
            "created_at": time.time(),
            "started_at": 0.0,
            "finished_at": 0.0,
        }
    JOB_QUEUE.put(job_id)
    return job_id


def progress_widget(job: dict[str, Any]) -> str:
    progress = int(job.get("progress", 0))
    phase = html.escape(str(job.get("phase", "running")))
    done = int(job.get("done", 0))
    total = int(job.get("total", 0))
    workers = int(job.get("cpu_workers", 0))
    auth_mode = html.escape(str(job.get("auth_mode", "public")))

    return f"""
    <p><b>Mode:</b> {auth_mode}</p>
    <p><b>Phase:</b> {phase}</p>
    <p><b>CPU worker limit:</b> {workers} (safe cap: {'max 12 (apple)' if os.getenv('APP_PROFILE') == 'apple_silicon' else 'max 10'})</p>
    <div style='background:#e9ecef;border-radius:8px;overflow:hidden;height:22px;max-width:560px;'>
      <div style='height:22px;width:{progress}%;background:#2f9e44;color:white;text-align:center;line-height:22px;font-size:12px;'>
        {progress}%
      </div>
    </div>
    <p>{done}/{total} items processed</p>
    """


def _format_ts(ts: float) -> str:
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def parse_submitted_keywords(payload: dict[str, list[str]]) -> list[str]:
    values: list[str] = []

    # legacy single field compatibility
    raw_single = payload.get("keyword", [""])[0]
    if raw_single:
        for part in raw_single.replace(",", "\n").splitlines():
            part = part.strip()
            if part:
                values.append(part)

    # multi-input form fields
    for i in range(1, 11):
        kw = payload.get(f"keyword{i}", [""])[0].strip()
        if kw:
            values.append(kw)

    # de-duplicate preserving order
    seen: set[str] = set()
    out: list[str] = []
    for item in values:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def page_template(content: str) -> bytes:
    inputs = "".join(
        f"<input type='text' name='keyword{i}' placeholder='Keyword {i} (e.g. housing)' style='display:block;margin:6px 0;' />"
        for i in range(1, 11)
    )
    return f"""
    <html>
    <head>
      <title>Subreddit Finder</title>
      <meta http-equiv="refresh" content="8">
      <style>
        body {{ font-family: Arial, sans-serif; margin: 30px; }}
        input[type=text] {{ width: 360px; padding: 8px; }}
        button {{ padding: 8px 12px; }}
        table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background: #f4f4f4; }}
      </style>
    </head>
    <body>
      <h1>Reddit Subreddit Discovery & Analytics</h1>
      <form method="POST" action="/search">
        <p><b>Quick multi-run:</b> use the 10 boxes below or paste comma/newline separated list in the first box.</p>
        <input type='text' name='keyword' placeholder='Optional list: housing, rent, mortgage' style='display:block;margin:6px 0;width:520px;' />
        {inputs}
        <button type="submit">Queue Jobs</button>
      </form>
      <p><a href="/jobs">View all jobs</a></p>
      {content}
    </body>
    </html>
    """.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._html(page_template(""))
            return

        if parsed.path == "/jobs":
            with JOBS_LOCK:
                items = list(JOBS.items())
            rows = []
            for job_id, job in sorted(items, key=lambda kv: kv[1].get("created_at", 0), reverse=True):
                output = html.escape(str(job.get("output", "")))
                dl = f"<code>{output}</code>" if output else "-"
                rows.append(
                    "<tr>"
                    f"<td>{html.escape(job_id)}</td>"
                    f"<td>{html.escape(str(job.get('keyword', '')))}</td>"
                    f"<td>{html.escape(str(job.get('status', '')))}</td>"
                    f"<td>{html.escape(str(job.get('phase', '')))}</td>"
                    f"<td>{_format_ts(float(job.get('created_at', 0) or 0))}</td>"
                    f"<td>{_format_ts(float(job.get('started_at', 0) or 0))}</td>"
                    f"<td>{_format_ts(float(job.get('finished_at', 0) or 0))}</td>"
                    f"<td><a href='/job?id={job_id}'>view</a></td>"
                    f"<td>{dl}</td>"
                    "</tr>"
                )
            table = (
                "<h2>Jobs</h2>"
                "<table><thead><tr><th>Job ID</th><th>Keyword</th><th>Status</th><th>Phase</th>"
                "<th>Created</th><th>Started</th><th>Finished</th><th>Details</th><th>Output</th>"
                "</tr></thead><tbody>"
                + "".join(rows)
                + "</tbody></table>"
            )
            self._html(page_template(table))
            return

        if parsed.path == "/job":
            params = urllib.parse.parse_qs(parsed.query)
            job_id = params.get("id", [""])[0]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                self._html(page_template("<p>Job not found.</p>"), status=HTTPStatus.NOT_FOUND)
                return

            if job["status"] in {"queued", "running"}:
                self._html(
                    page_template(
                        f"<p>Job {html.escape(job_id)} for <b>{html.escape(job['keyword'])}</b>.</p>{progress_widget(job)}"
                    )
                )
                return

            if job["status"] == "error":
                self._html(page_template(f"{progress_widget(job)}<p style='color:red'>Error: {html.escape(job['error'])}</p>"))
                return

            rows = job["rows"]
            output = html.escape(job["output"])
            head = "".join(f"<th>{c}</th>" for c in EXPORT_COLUMNS)

            def cell(c: str, v: Any) -> str:
                if c == "subreddit_url" and v:
                    e = html.escape(str(v))
                    return f"<td><a href='{e}' target='_blank' rel='noopener noreferrer'>{e}</a></td>"
                return f"<td>{html.escape(str(v))}</td>"

            body = "".join(
                "<tr>" + "".join(cell(c, r.get(c, "")) for c in EXPORT_COLUMNS) + "</tr>" for r in rows[:200]
            )
            table = f"""
              {progress_widget(job)}
              <p>Done. Rows: <b>{len(rows)}</b></p>
              <p>Export file: <code>{output}</code></p>
              <table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>
            """
            self._html(page_template(table))
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/search":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length).decode("utf-8")
        payload = urllib.parse.parse_qs(body)

        keywords = parse_submitted_keywords(payload)
        job_ids = [start_job(kw) for kw in keywords]

        if not job_ids:
            self._html(page_template("<p>Please provide at least one keyword.</p>"), status=HTTPStatus.BAD_REQUEST)
            return

        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/jobs")
        self.end_headers()

    def _html(self, data: bytes, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("HTTP %s", format % args)


def run_server(host: str = "0.0.0.0", port: int = 8080) -> None:
    ensure_workers_started()
    logger.info("Job workers started: %s", get_job_workers())
    server = ThreadingHTTPServer((host, port), Handler)
    logger.info("Server running at http://%s:%s", host, port)
    server.serve_forever()


if __name__ == "__main__":
    run_server()
