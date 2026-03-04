#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import html
import logging
import os
import random
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Literal

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("subreddit_finder_web")

EXPORT_COLUMNS = [
    "subreddit_name",
    "subreddit_url",
    "title",
    "description",
    "subscribers",
    "weekly_contribution",
    "weekly_active_users",
    "date_of_creation",
    "visibility_status",
    "nsfw_flag",
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
        "urban planning",
    ]
}


@dataclass(slots=True)
class RedditCredentials:
    client_id: str = ""
    client_secret: str = ""
    username: str = ""
    password: str = ""
    user_agent: str = "subreddit-finder-web/2.0"
    auth_flow: Literal["auto", "client_credentials", "password", "public"] = "auto"


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
            next_time = now + wait + (1.0 / rps)
            self._next_allowed[endpoint_key] = next_time
        if wait > 0:
            await asyncio.sleep(wait)


class RedditClient:
    BASE_URL = "https://oauth.reddit.com"
    TOKEN_URL = "https://www.reddit.com/api/v1/access_token"

    def __init__(
        self,
        creds: RedditCredentials,
        allow_public_mode: bool = True,
        requests_per_second: float = 2.5,
        endpoint_rps: dict[str, float] | None = None,
    ) -> None:
        self.creds = creds
        self.allow_public_mode = allow_public_mode
        self.auth_mode = self._resolve_auth_mode()
        try:
            import httpx  # type: ignore
        except Exception as exc:
            raise RuntimeError("Missing dependency 'httpx'. Install it with: pip install httpx pyarrow") from exc
        self._client = httpx.AsyncClient(timeout=30.0)
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()
        self._public_bases = ["https://www.reddit.com", "https://old.reddit.com"]
        self._rate_limiter = AsyncRateLimiter(default_rps=requests_per_second, endpoint_rps=endpoint_rps)
        self.telemetry: dict[str, dict[str, int]] = {}

    def _resolve_auth_mode(self) -> Literal["oauth_app", "oauth_user", "public"]:
        if self.creds.auth_flow == "public":
            return "public"
        if self.creds.auth_flow == "password":
            if all([self.creds.client_id, self.creds.client_secret, self.creds.username, self.creds.password]):
                return "oauth_user"
            raise RuntimeError("auth_flow=password selected but credentials are incomplete.")
        if self.creds.auth_flow == "client_credentials":
            if self.creds.client_id and self.creds.client_secret:
                return "oauth_app"
            raise RuntimeError("auth_flow=client_credentials selected but client_id/client_secret missing.")

        if all([self.creds.client_id, self.creds.client_secret, self.creds.username, self.creds.password]):
            return "oauth_user"
        if self.creds.client_id and self.creds.client_secret:
            return "oauth_app"
        return "public"

    async def close(self) -> None:
        await self._client.aclose()

    async def _authenticate_user_password(self) -> None:
        response = await self._client.post(
            self.TOKEN_URL,
            data={
                "grant_type": "password",
                "username": self.creds.username,
                "password": self.creds.password,
            },
            auth=(self.creds.client_id, self.creds.client_secret),
            headers={"User-Agent": self.creds.user_agent},
        )
        response.raise_for_status()
        payload = response.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.time() + int(payload.get("expires_in", 3600)) - 60

    async def _authenticate_app_only(self) -> None:
        response = await self._client.post(
            self.TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self.creds.client_id, self.creds.client_secret),
            headers={"User-Agent": self.creds.user_agent},
        )
        response.raise_for_status()
        payload = response.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.time() + int(payload.get("expires_in", 3600)) - 60

    async def _ensure_token(self) -> None:
        if self.auth_mode == "public":
            return
        if self._token and time.time() < self._token_expires_at:
            return
        async with self._token_lock:
            if self._token and time.time() < self._token_expires_at:
                return
            if self.auth_mode == "oauth_user":
                await self._authenticate_user_password()
            else:
                await self._authenticate_app_only()

    def _log_http_issue(self, response: Any, path: str, params: dict[str, Any], attempt: int) -> None:
        headers = response.headers
        compact_headers = {
            k: headers.get(k)
            for k in [
                "retry-after",
                "x-ratelimit-used",
                "x-ratelimit-remaining",
                "x-ratelimit-reset",
                "www-authenticate",
                "cf-ray",
                "x-request-id",
            ]
            if headers.get(k)
        }
        logger.warning(
            "HTTP %s on %s attempt=%s params=%s headers=%s",
            response.status_code,
            path,
            attempt,
            {k: v for k, v in params.items() if k not in {"access_token", "password"}},
            compact_headers,
        )

    def _incr_telemetry(self, path: str, code: int) -> None:
        endpoint = path.split("?")[0]
        bucket = self.telemetry.setdefault(endpoint, {"403": 0, "429": 0})
        if code == 403:
            bucket["403"] += 1
        if code == 429:
            bucket["429"] += 1

    async def request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        req_params = dict(params or {})
        req_params.setdefault("raw_json", 1)
        endpoint_key = "search" if path.startswith("/subreddits/search") else "default"
        await self._rate_limiter.acquire(endpoint_key)
        await self._ensure_token()

        headers = {"User-Agent": self.creds.user_agent, "Accept": "application/json"}
        if self.auth_mode.startswith("oauth"):
            headers["Authorization"] = f"bearer {self._token}"
            req_urls = [f"{self.BASE_URL}{path}"]
        else:
            req_urls = [f"{base}{path}.json" for base in self._public_bases]

        last_error: str | None = None
        for attempt in range(1, 8):
            for url in req_urls:
                response = await self._client.get(url, params=req_params, headers=headers)
                code = response.status_code
                if code in {403, 429}:
                    self._incr_telemetry(path, code)
                    self._log_http_issue(response, path, req_params, attempt)

                if code == 429:
                    retry_after = response.headers.get("retry-after")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else min(2**attempt, 300)
                    delay += random.uniform(0.1, 1.2)
                    await asyncio.sleep(delay)
                    last_error = f"429 rate limited"
                    continue

                if code == 403:
                    backoff = min(10 * (2 ** (attempt - 1)), 300) + random.uniform(0.2, 1.5)
                    await asyncio.sleep(backoff)
                    last_error = f"403 forbidden"
                    continue

                if code in {500, 502, 503, 504}:
                    await asyncio.sleep(min(2**attempt, 20) + random.uniform(0.1, 0.8))
                    last_error = f"{code} transient"
                    continue

                if code >= 400:
                    self._log_http_issue(response, path, req_params, attempt)
                    raise RedditAPIError(f"{path} failed: {code} {response.text[:300]}")

                return response.json()

            await asyncio.sleep(min(2**attempt, 15) + random.uniform(0.1, 0.8))

        raise RedditAPIError(f"{path} failed after retries ({last_error})")


def expand_keyword(keyword: str, min_terms: int = 15, max_terms: int = 25) -> list[str]:
    root = keyword.strip().lower()
    terms = [root]
    for item in CURATED_SYNONYMS.get(root, []):
        if item not in terms:
            terms.append(item)

    try:
        from nltk.corpus import wordnet as wn  # type: ignore

        for synset in wn.synsets(root):
            for lemma in synset.lemmas():
                value = lemma.name().replace("_", " ").strip().lower()
                if value and value not in terms:
                    terms.append(value)
                if len(terms) >= max_terms:
                    break
            if len(terms) >= max_terms:
                break
    except Exception:
        pass

    fallback = [f"{root} advice", f"{root} discussion", f"{root} help", f"{root} resources", f"{root} support"]
    for item in fallback:
        if len(terms) >= min_terms:
            break
        if item not in terms:
            terms.append(item)

    return terms[:max_terms]


async def discover_subreddits(
    client: RedditClient,
    terms: list[str],
    per_term_limit: int = 300,
    progress_cb: Callable[[int, int], None] | None = None,
) -> list[str]:
    discovered: dict[str, str] = {}
    for idx, term in enumerate(terms, start=1):
        after: str | None = None
        fetched = 0
        same_count_streak = 0
        rate_error_streak = 0
        while True:
            before_count = len(discovered)
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
                rate_error_streak = 0
            except RedditAPIError as err:
                msg = str(err)
                if "403" in msg or "429" in msg:
                    rate_error_streak += 1
                    if rate_error_streak >= 2:
                        logger.warning("Skipping term '%s' after repeated %s", term, msg)
                        await asyncio.sleep(8)
                        break
                    await asyncio.sleep(5)
                    continue
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
            if len(discovered) == before_count:
                same_count_streak += 1
            else:
                same_count_streak = 0

            if not after or fetched >= per_term_limit or same_count_streak >= 2:
                break

        if progress_cb:
            progress_cb(idx, len(terms))

    return sorted(discovered.values())


async def compute_weekly_contribution(client: RedditClient, subreddit_name: str, max_pages: int = 12) -> int:
    threshold = dt.datetime.now(tz=dt.timezone.utc).timestamp() - 7 * 24 * 3600
    after: str | None = None
    total = 0

    for _ in range(max_pages):
        payload = await client.request(f"/r/{subreddit_name}/new", params={"limit": 100, "after": after})
        data = payload.get("data", {})
        children = data.get("children", [])
        if not children:
            break

        older_found = False
        for item in children:
            created = float(item.get("data", {}).get("created_utc", 0))
            if created >= threshold:
                total += 1
            else:
                older_found = True

        if older_found:
            break

        after = data.get("after")
        if not after:
            break

    return total


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
                weekly = await compute_weekly_contribution(client, name)
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
                    "title": about.get("title", ""),
                    "description": description,
                    "subscribers": int(about.get("subscribers", 0) or 0),
                    "weekly_contribution": weekly,
                    "weekly_active_users": int(about.get("accounts_active", 0) or 0),
                    "date_of_creation": dt.datetime.fromtimestamp(
                        float(about.get("created_utc", 0) or 0), tz=dt.timezone.utc
                    ).date().isoformat()
                    if about.get("created_utc")
                    else "",
                    "visibility_status": about.get("subreddit_type", "unknown"),
                    "nsfw_flag": bool(about.get("over18", False)),
                }
                row["score"] = keyword_frequency_score(
                    f"{row['subreddit_name']} {row['title']} {row['description']}", terms
                )
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


def update_job(job_id: str, **kwargs: Any) -> None:
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(kwargs)


def get_profile() -> Literal["default", "apple_silicon", "windows"]:
    profile = os.getenv("APP_PROFILE", "default").strip().lower()
    if profile in {"apple_silicon", "windows"}:
        return profile
    return "default"


def compute_safe_concurrency() -> int:
    cpu_count = os.cpu_count() or 1
    profile = get_profile()
    if profile == "apple_silicon":
        return max(4, min(12, cpu_count))
    if profile == "windows":
        return max(4, min(12, cpu_count - 1 if cpu_count > 4 else cpu_count))
    return max(3, min(12, cpu_count))


def make_client(creds: RedditCredentials) -> RedditClient:
    allow_public = os.getenv("ALLOW_PUBLIC_MODE", "1") == "1"
    profile = get_profile()
    base_rps = 2.5
    if profile == "apple_silicon":
        base_rps = 3.2
    elif profile == "windows":
        base_rps = 2.8
    return RedditClient(
        creds,
        allow_public_mode=allow_public,
        requests_per_second=base_rps,
        endpoint_rps={"search": max(0.8, base_rps * 0.45), "default": base_rps},
    )


async def run_pipeline(keyword: str, job_id: str) -> tuple[list[dict[str, Any]], str]:
    creds = RedditCredentials(
        client_id=os.getenv("REDDIT_CLIENT_ID", ""),
        client_secret=os.getenv("REDDIT_CLIENT_SECRET", ""),
        username=os.getenv("REDDIT_USERNAME", ""),
        password=os.getenv("REDDIT_PASSWORD", ""),
        user_agent=os.getenv("REDDIT_USER_AGENT", "subreddit-finder-web/2.0"),
        auth_flow=os.getenv("REDDIT_AUTH_FLOW", "auto"),
    )

    concurrency = compute_safe_concurrency()
    client = make_client(creds)
    update_job(
        job_id,
        cpu_workers=concurrency,
        phase=f"starting ({client.auth_mode} mode)",
        progress=5,
        auth_mode=client.auth_mode,
        profile=get_profile(),
    )
    try:
        terms = expand_keyword(keyword)

        def discovery_progress(done: int, total: int) -> None:
            pct = 5 + int((done / max(1, total)) * 30)
            update_job(job_id, phase="discovering subreddits", progress=min(pct, 35), done=done, total=total)

        names = await discover_subreddits(client, terms, progress_cb=discovery_progress)
        update_job(job_id, phase="collecting metrics", progress=35, done=0, total=len(names))

        def metrics_progress(done: int, total: int) -> None:
            pct = 35 + int((done / max(1, total)) * 55)
            update_job(job_id, phase="collecting metrics", progress=min(pct, 90), done=done, total=total)

        rows = await collect_metrics(client, names, terms, concurrency=concurrency, progress_cb=metrics_progress)
    finally:
        await client.close()

    update_job(job_id, phase="exporting", progress=95)
    output = export_results(rows, output_stem=f"subreddit_results_{keyword}_{int(time.time())}")
    update_job(job_id, phase="done", progress=100, telemetry=client.telemetry)
    return rows, output


def start_job(keyword: str) -> str:
    job_id = str(uuid.uuid4())
    with JOBS_LOCK:
        JOBS[job_id] = {
            "keyword": keyword,
            "status": "running",
            "rows": [],
            "error": "",
            "output": "",
            "phase": "starting",
            "progress": 0,
            "done": 0,
            "total": 0,
            "cpu_workers": 0,
            "auth_mode": "unknown",
            "profile": get_profile(),
            "telemetry": {},
        }

    def target() -> None:
        try:
            rows, output = asyncio.run(run_pipeline(keyword, job_id))
            update_job(job_id, status="done", rows=rows, output=output, progress=100)
        except Exception as exc:
            update_job(job_id, status="error", error=str(exc))

    threading.Thread(target=target, daemon=True).start()
    return job_id


def progress_widget(job: dict[str, Any]) -> str:
    progress = int(job.get("progress", 0))
    phase = html.escape(str(job.get("phase", "running")))
    done = int(job.get("done", 0))
    total = int(job.get("total", 0))
    workers = int(job.get("cpu_workers", 0))
    auth_mode = html.escape(str(job.get("auth_mode", "unknown")))
    profile = html.escape(str(job.get("profile", "default")))

    return f"""
    <p><b>Mode:</b> {auth_mode}</p>
    <p><b>Optimization profile:</b> {profile}</p>
    <p><b>Phase:</b> {phase}</p>
    <p><b>CPU worker limit:</b> {workers} (safe cap: max 12)</p>
    <div style='background:#e9ecef;border-radius:8px;overflow:hidden;height:22px;max-width:560px;'>
      <div style='height:22px;width:{progress}%;background:#2f9e44;color:white;text-align:center;line-height:22px;font-size:12px;'>
        {progress}%
      </div>
    </div>
    <p>{done}/{total} items processed</p>
    """


def page_template(content: str) -> bytes:
    return f"""
    <html>
    <head>
      <title>Subreddit Finder</title>
      <meta http-equiv="refresh" content="8">
      <style>
        body {{ font-family: Arial, sans-serif; margin: 30px; }}
        input[type=text] {{ width: 300px; padding: 8px; }}
        button {{ padding: 8px 12px; }}
        table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background: #f4f4f4; }}
      </style>
    </head>
    <body>
      <h1>Reddit Subreddit Discovery & Analytics</h1>
      <form method="POST" action="/search">
        <input type="text" name="keyword" placeholder="Enter keyword, e.g. housing" required />
        <button type="submit">Run</button>
      </form>
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

        if parsed.path == "/job":
            params = urllib.parse.parse_qs(parsed.query)
            job_id = params.get("id", [""])[0]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                self._html(page_template("<p>Job not found.</p>"), status=HTTPStatus.NOT_FOUND)
                return

            if job["status"] == "running":
                self._html(
                    page_template(
                        f"<p>Job running for <b>{html.escape(job['keyword'])}</b>.</p>{progress_widget(job)}"
                    )
                )
                return

            if job["status"] == "error":
                self._html(page_template(f"{progress_widget(job)}<p style='color:red'>Error: {html.escape(job['error'])}</p>"))
                return

            rows = job["rows"]
            output = html.escape(job["output"])
            telemetry = html.escape(str(job.get("telemetry", {})))
            head = "".join(f"<th>{c}</th>" for c in EXPORT_COLUMNS)

            def cell(c: str, v: Any) -> str:
                if c == "subreddit_url" and v:
                    e = html.escape(str(v))
                    return f"<td><a href='{e}' target='_blank' rel='noopener noreferrer'>{e}</a></td>"
                return f"<td>{html.escape(str(v))}</td>"

            body = "".join(
                "<tr>" + "".join(cell(c, r.get(c, "")) for c in EXPORT_COLUMNS) + "</tr>"
                for r in rows[:200]
            )
            table = f"""
              {progress_widget(job)}
              <p>Done. Rows: <b>{len(rows)}</b></p>
              <p>Export file: <code>{output}</code></p>
              <p>Telemetry (403/429 by endpoint): <code>{telemetry}</code></p>
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
        keyword = payload.get("keyword", [""])[0].strip()

        if not keyword:
            self._html(page_template("<p>Please provide a keyword.</p>"), status=HTTPStatus.BAD_REQUEST)
            return

        job_id = start_job(keyword)
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", f"/job?id={job_id}")
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
    server = ThreadingHTTPServer((host, port), Handler)
    logger.info("Server running at http://%s:%s", host, port)
    logger.info("Active profile: %s", get_profile())
    server.serve_forever()


if __name__ == "__main__":
    run_server()
