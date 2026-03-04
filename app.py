#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import html
import json
import logging
import os
import random
import re
import threading
import time
import urllib.parse
import uuid
from collections import deque
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("subreddit_finder_web")

EXPORT_COLUMNS = ["subreddit_name", "subreddit_url", "description", "subscribers", "active_users"]

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
    user_agent: str = "subreddit-finder-web/3.0"


class RedditAPIError(RuntimeError):
    pass


class AsyncRateLimiter:
    def __init__(self, default_rps: float, endpoint_rps: dict[str, float] | None = None) -> None:
        self.default_rps = max(default_rps, 0.05)
        self.endpoint_rps = endpoint_rps or {}
        self._lock = asyncio.Lock()
        self._next_allowed: dict[str, float] = {}

    async def acquire(self, endpoint_key: str) -> None:
        async with self._lock:
            now = time.time()
            rps = max(self.endpoint_rps.get(endpoint_key, self.default_rps), 0.05)
            wait = max(0.0, self._next_allowed.get(endpoint_key, now) - now)
            self._next_allowed[endpoint_key] = now + wait + (1.0 / rps)
        if wait > 0:
            await asyncio.sleep(wait)


class RedditService:
    BASE_URL = "https://oauth.reddit.com"
    TOKEN_URL = "https://www.reddit.com/api/v1/access_token"

    def __init__(self, creds: RedditCredentials) -> None:
        self.creds = creds
        self.auth_mode = self._resolve_auth_mode()
        import httpx  # type: ignore

        self._httpx = httpx
        limits = httpx.Limits(max_connections=280, max_keepalive_connections=100)
        self.client = httpx.AsyncClient(timeout=30.0, limits=limits)
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()
        cfg = self.mode_config(self.auth_mode)
        self.limiter = AsyncRateLimiter(cfg["global_rps"], endpoint_rps={"search": cfg["global_rps"] * 0.45})
        self.telemetry: dict[str, dict[str, int]] = {}

    @staticmethod
    def mode_config(auth_mode: str) -> dict[str, int | float]:
        if auth_mode == "public":
            return {
                "job_workers": 2,
                "discovery_concurrency": 8,
                "metrics_concurrency": 16,
                "global_rps": 2.2,
                "request_attempts": 3,
            }
        return {
            "job_workers": 5,
            "discovery_concurrency": 20,
            "metrics_concurrency": 40,
            "global_rps": 6.5,
            "request_attempts": 7,
        }

    def _resolve_auth_mode(self) -> Literal["oauth_app", "oauth_user", "public"]:
        if all([self.creds.client_id, self.creds.client_secret, self.creds.username, self.creds.password]):
            return "oauth_user"
        if self.creds.client_id and self.creds.client_secret:
            return "oauth_app"
        return "public"

    async def close(self) -> None:
        await self.client.aclose()

    async def _ensure_token(self) -> None:
        if self.auth_mode == "public":
            return
        if self._token and time.time() < self._token_expires_at:
            return
        async with self._token_lock:
            if self._token and time.time() < self._token_expires_at:
                return
            data = {"grant_type": "client_credentials"}
            if self.auth_mode == "oauth_user":
                data = {
                    "grant_type": "password",
                    "username": self.creds.username,
                    "password": self.creds.password,
                }
            resp = await self.client.post(
                self.TOKEN_URL,
                data=data,
                auth=(self.creds.client_id, self.creds.client_secret),
                headers={"User-Agent": self.creds.user_agent},
            )
            resp.raise_for_status()
            payload = resp.json()
            self._token = payload["access_token"]
            self._token_expires_at = time.time() + int(payload.get("expires_in", 3600)) - 60

    def _record_status(self, path: str, status: int) -> None:
        endpoint = path.split("?")[0]
        bucket = self.telemetry.setdefault(endpoint, {"403": 0, "429": 0})
        if status == 403:
            bucket["403"] += 1
        if status == 429:
            bucket["429"] += 1

    async def request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        req_params = dict(params or {})
        req_params.setdefault("raw_json", 1)
        endpoint_key = "search" if path.startswith("/subreddits/search") else "default"
        await self.limiter.acquire(endpoint_key)
        await self._ensure_token()

        headers = {"User-Agent": self.creds.user_agent, "Accept": "application/json"}
        if self.auth_mode.startswith("oauth"):
            headers["Authorization"] = f"bearer {self._token}"
            urls = [f"{self.BASE_URL}{path}"]
        else:
            urls = [f"https://www.reddit.com{path}.json"]

        cfg = self.mode_config(self.auth_mode)
        max_attempts = int(cfg["request_attempts"])
        is_public = self.auth_mode == "public"

        last_error = "unknown"
        for attempt in range(1, max_attempts + 1):
            for url in urls:
                response = await self.client.get(url, params=req_params, headers=headers)
                code = response.status_code
                if code in {403, 429}:
                    self._record_status(path, code)
                    logger.warning("HTTP %s on %s attempt=%s", code, path, attempt)

                if code == 429:
                    ra = response.headers.get("retry-after")
                    cap = 15 if is_public else 240
                    delay = float(ra) if ra and ra.isdigit() else min(2**attempt, cap)
                    await asyncio.sleep(delay + random.uniform(0.1, 1.0))
                    last_error = "429"
                    continue

                if code == 403:
                    cap = 12 if is_public else 240
                    base = 2 if is_public else 10
                    await asyncio.sleep(min(base * (2 ** (attempt - 1)), cap) + random.uniform(0.1, 0.8))
                    last_error = "403"
                    continue

                if code in {500, 502, 503, 504}:
                    await asyncio.sleep(min(2**attempt, 8 if is_public else 20) + random.uniform(0.1, 0.8))
                    last_error = str(code)
                    continue

                if code >= 400:
                    raise RedditAPIError(f"{path} failed: {code} {response.text[:250]}")

                return response.json()

            await asyncio.sleep(min(2**attempt, 5 if is_public else 12))

        raise RedditAPIError(f"{path} failed after retries ({last_error})")


def expand_keyword(keyword: str, max_terms: int = 20) -> list[str]:
    root = keyword.strip().lower()
    terms = [root]
    for item in CURATED_SYNONYMS.get(root, []):
        if item not in terms:
            terms.append(item)
    fallback = [f"{root} help", f"{root} advice", f"{root} discussion", f"{root} community"]
    for item in fallback:
        if len(terms) >= max_terms:
            break
        if item not in terms:
            terms.append(item)
    return terms[:max_terms]


def sanitize_keyword(keyword: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", keyword.strip())
    return cleaned[:60] or "keyword"


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.service: RedditService | None = None
        self.startup_error: str = ""
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        self.ready.wait(timeout=15)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.create_task(self._bootstrap())
        self.loop.run_forever()

    async def _bootstrap(self) -> None:
        try:
            creds = RedditCredentials(
                client_id=os.getenv("REDDIT_CLIENT_ID", ""),
                client_secret=os.getenv("REDDIT_CLIENT_SECRET", ""),
                username=os.getenv("REDDIT_USERNAME", ""),
                password=os.getenv("REDDIT_PASSWORD", ""),
                user_agent=os.getenv("REDDIT_USER_AGENT", "subreddit-finder-web/3.0"),
            )
            self.service = RedditService(creds)
            cfg = RedditService.mode_config(self.service.auth_mode)
            self.job_queue: asyncio.Queue[str] = asyncio.Queue()
            self.job_workers = int(cfg["job_workers"])
            self.discovery_concurrency = int(cfg["discovery_concurrency"])
            self.metrics_concurrency = int(cfg["metrics_concurrency"])
            for i in range(self.job_workers):
                self.loop.create_task(self._job_worker(i + 1))
        except Exception as exc:
            self.startup_error = str(exc)
            logger.error("JobManager bootstrap failed: %s", exc)
            self.job_queue = asyncio.Queue()
            self.job_workers = 0
            self.discovery_concurrency = 0
            self.metrics_concurrency = 0
        finally:
            self.ready.set()

    def enqueue_keywords(self, keywords: list[str]) -> list[str]:
        if self.startup_error or self.service is None:
            return []
        ids: list[str] = []
        for kw in keywords:
            keyword = kw.strip()
            if not keyword:
                continue
            job_id = str(uuid.uuid4())
            now = time.time()
            with self.lock:
                self.jobs[job_id] = {
                    "job_id": job_id,
                    "keyword": keyword,
                    "status": "queued",
                    "phase": "queued",
                    "progress": 0,
                    "done": 0,
                    "total": 0,
                    "current_subreddit": "",
                    "current_term": "",
                    "created_at": now,
                    "started_at": 0.0,
                    "finished_at": 0.0,
                    "eta_seconds": None,
                    "output": "",
                    "telemetry": {},
                    "error": "",
                    "rows": [],
                    "rate_samples": [],
                }
            asyncio.run_coroutine_threadsafe(self.job_queue.put(job_id), self.loop)
            ids.append(job_id)
        return ids

    def snapshot(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            job = self.jobs.get(job_id)
            return dict(job) if job else None

    def snapshot_all(self) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(v) for v in self.jobs.values()]

    def _update_job(self, job_id: str, **kwargs: Any) -> None:
        with self.lock:
            if job_id in self.jobs:
                self.jobs[job_id].update(kwargs)

    def _tick_eta(self, job_id: str, done: int, total: int) -> None:
        now = time.time()
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            samples: list[tuple[float, int]] = job.setdefault("rate_samples", [])
            samples.append((now, done))
            cutoff = now - 25
            samples[:] = [s for s in samples if s[0] >= cutoff]
            eta = None
            if len(samples) >= 2 and total > done:
                dt_s = samples[-1][0] - samples[0][0]
                dd = samples[-1][1] - samples[0][1]
                if dt_s > 0 and dd > 0:
                    rate = dd / dt_s
                    eta = int((total - done) / rate)
            job["eta_seconds"] = eta

    async def _job_worker(self, worker_id: int) -> None:
        while True:
            job_id = await self.job_queue.get()
            try:
                await self._run_job(job_id)
            except Exception as exc:
                logger.exception("Job worker %s crashed on %s", worker_id, job_id)
                self._update_job(job_id, status="error", phase="error", error=str(exc), finished_at=time.time())
            finally:
                self.job_queue.task_done()

    async def _run_job(self, job_id: str) -> None:
        job = self.snapshot(job_id)
        if not job:
            return
        keyword = str(job["keyword"])
        safe = sanitize_keyword(keyword)
        output_path = Path("exports") / f"subreddit_results_{safe}_{job_id}.csv"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        self._update_job(
            job_id,
            status="running",
            phase="discovering",
            started_at=time.time(),
            output=str(output_path),
            telemetry=self.service.telemetry,
        )

        names_q: asyncio.Queue[str | None] = asyncio.Queue(maxsize=3000)
        rows_q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=3000)
        discovered: set[str] = set()
        discovered_lock = asyncio.Lock()

        async def discover_term(term: str) -> None:
            self._update_job(job_id, current_term=term)
            after: str | None = None
            same_streak = 0
            fetched = 0
            while True:
                before = len(discovered)
                try:
                    payload = await self.service.request(
                        "/subreddits/search",
                        params={"q": term, "type": "sr", "sort": "relevance", "limit": 100, "after": after},
                    )
                except Exception as exc:
                    logger.warning("Discover term failed '%s': %s", term, exc)
                    break
                data = payload.get("data", {})
                children: list[dict[str, Any]] = data.get("children", [])
                if not children:
                    break
                async with discovered_lock:
                    for item in children:
                        name = item.get("data", {}).get("display_name")
                        if name and name.lower() not in discovered:
                            discovered.add(name.lower())
                            await names_q.put(name)
                fetched += len(children)
                after = data.get("after")
                if len(discovered) == before:
                    same_streak += 1
                else:
                    same_streak = 0
                self._update_job(job_id, total=len(discovered), progress=min(40, int((len(discovered) / 250) * 40)))
                if not after or same_streak >= 2 or fetched >= 400:
                    break

        async def discovery_stage() -> None:
            sem = asyncio.Semaphore(self.discovery_concurrency)
            terms = expand_keyword(keyword)

            async def wrapped(term: str) -> None:
                async with sem:
                    await discover_term(term)

            await asyncio.gather(*(wrapped(t) for t in terms))
            for _ in range(self.metrics_concurrency):
                await names_q.put(None)

        async def metrics_worker() -> None:
            while True:
                name = await names_q.get()
                if name is None:
                    await rows_q.put(None)
                    return
                self._update_job(job_id, current_subreddit=name, phase="collecting")
                try:
                    payload = await asyncio.wait_for(
                        self.service.request(f"/r/{name}/about"),
                        timeout=float(os.getenv("SUBREDDIT_TIMEOUT_SECONDS", "75")),
                    )
                    about = payload.get("data", {})
                    relative = about.get("url") or f"/r/{about.get('display_name', name)}/"
                    row = {
                        "subreddit_name": about.get("display_name", name),
                        "subreddit_url": urllib.parse.urljoin("https://www.reddit.com", relative),
                        "description": " ".join(
                            filter(None, [about.get("public_description", ""), about.get("description", "")])
                        ).strip(),
                        "subscribers": int(about.get("subscribers", 0) or 0),
                        "active_users": int(about.get("accounts_active", 0) or 0),
                    }
                    await rows_q.put(row)
                except asyncio.TimeoutError:
                    logger.warning("Timeout for %s; skipped", name)
                except Exception as exc:
                    logger.warning("Metrics failed for %s: %s", name, exc)

                snap = self.snapshot(job_id) or {}
                done = int(snap.get("done", 0)) + 1
                total = max(int(snap.get("total", 0)), done)
                self._update_job(job_id, done=done, total=total, progress=40 + int((done / max(1, total)) * 55))
                self._tick_eta(job_id, done, total)
                self._print_terminal_progress(job_id)

        async def writer_stage() -> None:
            with output_path.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=EXPORT_COLUMNS)
                w.writeheader()
                done_workers = 0
                preview: list[dict[str, Any]] = []
                while done_workers < self.metrics_concurrency:
                    row = await rows_q.get()
                    if row is None:
                        done_workers += 1
                        continue
                    w.writerow({k: row.get(k, "") for k in EXPORT_COLUMNS})
                    if len(preview) < 200:
                        preview.append(row)
                    self._update_job(job_id, rows=preview)

            # optional parquet on large files
            if output_path.exists() and output_path.stat().st_size > 20 * 1024 * 1024:
                try:
                    import pyarrow as pa
                    import pyarrow.parquet as pq

                    table_rows = self.snapshot(job_id).get("rows", []) if self.snapshot(job_id) else []
                    cols = {c: [r.get(c) for r in table_rows] for c in EXPORT_COLUMNS}
                    pq_path = output_path.with_suffix(".parquet")
                    pq.write_table(pa.table(cols), pq_path)
                except Exception:
                    pass

        metrics_tasks = [asyncio.create_task(metrics_worker()) for _ in range(self.metrics_concurrency)]
        await asyncio.gather(discovery_stage(), writer_stage(), *metrics_tasks)

        self._update_job(
            job_id,
            status="done",
            phase="done",
            progress=100,
            current_subreddit="",
            current_term="",
            finished_at=time.time(),
            telemetry=self.service.telemetry,
        )
        self._print_terminal_progress(job_id, final=True)

    def _print_terminal_progress(self, job_id: str, final: bool = False) -> None:
        job = self.snapshot(job_id)
        if not job:
            return
        done = int(job.get("done", 0))
        total = int(job.get("total", 0))
        width = 24
        ratio = (done / total) if total > 0 else 0.0
        fill = int(width * ratio)
        bar = "#" * fill + "-" * (width - fill)
        msg = f"[job {job_id[:8]}] [{bar}] {done}/{total} {job.get('phase')}"
        cur = str(job.get("current_subreddit", ""))
        if cur:
            msg += f" | current: r/{cur}"
        if final:
            print(msg)
        else:
            print(msg, end="\r", flush=True)


MANAGER = JobManager()


def fmt_ts(ts: float) -> str:
    if not ts:
        return "-"
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def fmt_elapsed(started_at: float, finished_at: float | None = None) -> str:
    if not started_at:
        return "-"
    end = finished_at or time.time()
    seconds = max(0, int(end - started_at))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def page_template(content: str) -> bytes:
    return f"""
    <html><head>
      <title>Subreddit Finder</title>
      <meta http-equiv=\"refresh\" content=\"6\">
      <style>
        body {{ font-family: Arial, sans-serif; margin: 24px; }}
        input[type=text], textarea {{ width: 420px; padding: 8px; margin: 4px 0; }}
        button {{ padding: 8px 12px; margin-top: 8px; }}
        table {{ border-collapse: collapse; width: 100%; margin-top: 18px; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background: #f5f5f5; }}
        .bar-wrap {{ background:#e9ecef;border-radius:8px;overflow:hidden;height:20px;min-width:120px; }}
        .bar {{ height:20px;background:#2f9e44;color:#fff;text-align:center;line-height:20px;font-size:12px; }}
      </style>
    </head><body>
      <h1>Subreddit Finder – High Throughput</h1>
      {content}
    </body></html>
    """.encode("utf-8")


def render_main_page() -> bytes:
    startup_note = ""
    if MANAGER.startup_error:
        startup_note = f"<p style='color:red'><b>Startup error:</b> {html.escape(MANAGER.startup_error)}</p>"
    fields = "".join(
        f'<div><input type="text" name="keyword{i}" placeholder="keyword {i}"></div>' for i in range(1, 6)
    )
    body = f"""
      {startup_note}
      <h3>Quick run (up to 5 keywords)</h3>
      <form method="POST" action="/search">{fields}<button type="submit">Queue Jobs</button></form>
      <h3>Bulk queue (50+ keywords, one per line)</h3>
      <form method="POST" action="/jobs/bulk">
        <textarea name="keywords" rows="8" placeholder="housing\nrent\nmortgage"></textarea><br/>
        <button type="submit">Queue Bulk Jobs</button>
      </form>
      <p><a href="/jobs">Open jobs dashboard</a></p>
    """
    return page_template(body)


def render_jobs_dashboard() -> bytes:
    jobs = sorted(MANAGER.snapshot_all(), key=lambda j: j.get("created_at", 0), reverse=True)
    rows = []
    for j in jobs:
        started = float(j.get("started_at", 0) or 0)
        finished = float(j.get("finished_at", 0) or 0)
        eta = j.get("eta_seconds")
        eta_text = f"{int(eta)}s" if isinstance(eta, int) and eta >= 0 else "estimating..."
        p = int(j.get("progress", 0))
        bar = f"<div class='bar-wrap'><div class='bar' style='width:{p}%'>{p}%</div></div>"
        dl = f"<a href='/download?id={j['job_id']}'>download</a>" if j.get("status") == "done" else "-"
        rows.append(
            "<tr>"
            f"<td>{html.escape(j['keyword'])}</td>"
            f"<td>{html.escape(j['status'])}</td>"
            f"<td>{html.escape(j.get('phase','-'))}</td>"
            f"<td>{bar}</td>"
            f"<td>{int(j.get('done',0))}/{int(j.get('total',0))}</td>"
            f"<td>{fmt_ts(started)}</td>"
            f"<td>{fmt_elapsed(started, finished if finished else None)}</td>"
            f"<td>{eta_text}</td>"
            f"<td><a href='/job?id={j['job_id']}'>view</a> | {dl}</td>"
            "</tr>"
        )
    table = (
        "<p><a href='/'>Back</a></p>"
        "<table><thead><tr><th>Keyword</th><th>Status</th><th>Phase</th><th>Progress</th>"
        "<th>Done/Total</th><th>Running since</th><th>Elapsed</th><th>ETA</th><th>Links</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )
    return page_template(table)


def render_job_detail(job: dict[str, Any]) -> bytes:
    rows = job.get("rows", [])
    head = "".join(f"<th>{c}</th>" for c in EXPORT_COLUMNS)

    def td(col: str, val: Any) -> str:
        if col == "subreddit_url" and val:
            e = html.escape(str(val))
            return f"<td><a href='{e}' target='_blank' rel='noopener noreferrer'>{e}</a></td>"
        return f"<td>{html.escape(str(val))}</td>"

    body_rows = "".join("<tr>" + "".join(td(c, r.get(c, "")) for c in EXPORT_COLUMNS) + "</tr>" for r in rows)
    started = float(job.get("started_at", 0) or 0)
    finished = float(job.get("finished_at", 0) or 0)
    eta = job.get("eta_seconds")
    eta_text = f"{int(eta)}s" if isinstance(eta, int) and eta >= 0 else "estimating..."

    content = f"""
      <p><a href='/jobs'>Back to jobs</a></p>
      <h2>{html.escape(job['keyword'])}</h2>
      <p><b>Status:</b> {html.escape(job['status'])}</p>
      <p><b>Phase:</b> {html.escape(job.get('phase','-'))}</p>
      <p><b>Current subreddit:</b> {html.escape(job.get('current_subreddit','-') or '-')}</p>
      <p><b>Current term:</b> {html.escape(job.get('current_term','-') or '-')}</p>
      <p><b>Running since:</b> {fmt_ts(started)} | <b>Elapsed:</b> {fmt_elapsed(started, finished if finished else None)} | <b>ETA:</b> {eta_text}</p>
      <p><b>Output:</b> <code>{html.escape(job.get('output',''))}</code></p>
      <p><b>Telemetry 403/429:</b> <code>{html.escape(str(job.get('telemetry', {})))}</code></p>
      <table><thead><tr>{head}</tr></thead><tbody>{body_rows}</tbody></table>
    """
    return page_template(content)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/":
            return self._html(render_main_page())

        if parsed.path == "/jobs":
            return self._html(render_jobs_dashboard())

        if parsed.path == "/job":
            job_id = params.get("id", [""])[0]
            job = MANAGER.snapshot(job_id)
            if not job:
                return self._html(page_template("<p>Job not found.</p>"), HTTPStatus.NOT_FOUND)
            return self._html(render_job_detail(job))

        if parsed.path == "/download":
            job_id = params.get("id", [""])[0]
            job = MANAGER.snapshot(job_id)
            if not job:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            p = Path(str(job.get("output", "")))
            if not p.exists():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            data = p.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f"attachment; filename={p.name}")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")

        if self.path == "/search":
            payload = urllib.parse.parse_qs(body)
            kws = [payload.get(f"keyword{i}", [""])[0].strip() for i in range(1, 6)]
            ids = MANAGER.enqueue_keywords([k for k in kws if k])
            if not ids:
                return self._html(page_template("<p>Please provide at least one keyword.</p>"), HTTPStatus.BAD_REQUEST)
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/jobs")
            self.end_headers()
            return

        if self.path == "/jobs/bulk":
            keywords: list[str] = []
            ctype = (self.headers.get("Content-Type") or "").lower()
            if "application/json" in ctype:
                try:
                    data = json.loads(body)
                    if isinstance(data, list):
                        keywords = [str(x).strip() for x in data]
                except Exception:
                    keywords = []
            else:
                payload = urllib.parse.parse_qs(body)
                blob = payload.get("keywords", [body])[0]
                keywords = [line.strip() for line in blob.splitlines() if line.strip()]

            ids = MANAGER.enqueue_keywords(keywords)
            if not ids:
                return self._html(page_template("<p>No keywords parsed.</p>"), HTTPStatus.BAD_REQUEST)
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/jobs")
            self.end_headers()
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def _html(self, data: bytes, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("HTTP %s", format % args)


def run_server(host: str = "0.0.0.0", port: int = 8080) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    logger.info("Server running at http://%s:%s", host, port)
    if MANAGER.service is not None:
        logger.info("Auth mode: %s", MANAGER.service.auth_mode)
        cfg = RedditService.mode_config(MANAGER.service.auth_mode)
        logger.info(
            "Workers=%s discovery=%s metrics=%s global_rps=%s",
            cfg["job_workers"],
            cfg["discovery_concurrency"],
            cfg["metrics_concurrency"],
            cfg["global_rps"],
        )
    else:
        logger.warning("Job system disabled due to startup error: %s", MANAGER.startup_error)
    server.serve_forever()


if __name__ == "__main__":
    run_server()
