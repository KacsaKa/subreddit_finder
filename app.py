#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import html
import io
import logging
import os
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("subreddit_finder_web")

EXPORT_COLUMNS = [
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
    client_id: str
    client_secret: str
    username: str
    password: str
    user_agent: str


class RedditAPIError(RuntimeError):
    pass


class RedditClient:
    BASE_URL = "https://oauth.reddit.com"
    TOKEN_URL = "https://www.reddit.com/api/v1/access_token"

    def __init__(self, creds: RedditCredentials, requests_per_second: float = 1.0) -> None:
        self.creds = creds
        try:
            import httpx  # type: ignore
        except Exception as exc:
            raise RuntimeError("Missing dependency 'httpx'. Install it with: pip install httpx pyarrow") from exc
        self._client = httpx.AsyncClient(timeout=30.0)
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._last_request = 0.0
        self._rps = requests_per_second
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()

    async def _authenticate(self) -> None:
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

    async def _ensure_token(self) -> None:
        if not self._token or time.time() >= self._token_expires_at:
            await self._authenticate()

    async def request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with self._lock:
            await self._ensure_token()
            min_delay = 1.0 / self._rps
            elapsed = time.time() - self._last_request
            if elapsed < min_delay:
                await asyncio.sleep(min_delay - elapsed)

            headers = {"Authorization": f"bearer {self._token}", "User-Agent": self.creds.user_agent}
            for attempt in range(1, 6):
                response = await self._client.get(f"{self.BASE_URL}{path}", params=params or {}, headers=headers)
                self._last_request = time.time()
                if response.status_code in {429, 500, 502, 503, 504} and attempt < 5:
                    await asyncio.sleep(min(2**attempt, 10))
                    continue
                if response.status_code >= 400:
                    raise RedditAPIError(f"{path} failed: {response.status_code} {response.text[:300]}")
                return response.json()

        raise RedditAPIError(f"{path} failed repeatedly")


def expand_keyword(keyword: str, min_terms: int = 20, max_terms: int = 50) -> list[str]:
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

    fallback = [
        f"{root} advice",
        f"{root} community",
        f"{root} help",
        f"{root} discussion",
        f"{root} guide",
        f"{root} tips",
        f"{root} support",
        f"{root} news",
        f"{root} resources",
        f"{root} questions",
    ]
    for item in fallback:
        if len(terms) >= min_terms:
            break
        if item not in terms:
            terms.append(item)

    return terms[:max_terms]


async def discover_subreddits(client: RedditClient, terms: list[str], per_term_limit: int = 800) -> list[str]:
    discovered: dict[str, str] = {}
    for term in terms:
        after: str | None = None
        fetched = 0
        while True:
            payload = await client.request(
                "/subreddits/search",
                params={"q": term, "type": "sr", "sort": "relevance", "limit": 100, "after": after},
            )
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

    return sorted(discovered.values())


async def compute_weekly_contribution(client: RedditClient, subreddit_name: str, max_pages: int = 20) -> int:
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


async def collect_metrics(client: RedditClient, names: list[str], terms: list[str], concurrency: int = 6) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(concurrency)

    async def worker(name: str) -> dict[str, Any] | None:
        async with sem:
            try:
                about_payload = await client.request(f"/r/{name}/about")
                about = about_payload.get("data", {})
                weekly = await compute_weekly_contribution(client, name)
            except Exception as exc:
                logger.warning("Failed for %s: %s", name, exc)
                return None

            description = " ".join(filter(None, [about.get("public_description", ""), about.get("description", "")])).strip()
            row = {
                "subreddit_name": about.get("display_name", name),
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
            return row

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


async def run_pipeline(keyword: str) -> tuple[list[dict[str, Any]], str]:
    creds = RedditCredentials(
        client_id=os.getenv("REDDIT_CLIENT_ID", ""),
        client_secret=os.getenv("REDDIT_CLIENT_SECRET", ""),
        username=os.getenv("REDDIT_USERNAME", ""),
        password=os.getenv("REDDIT_PASSWORD", ""),
        user_agent=os.getenv("REDDIT_USER_AGENT", "subreddit-finder-web/1.0"),
    )
    missing = [
        name
        for name, val in {
            "REDDIT_CLIENT_ID": creds.client_id,
            "REDDIT_CLIENT_SECRET": creds.client_secret,
            "REDDIT_USERNAME": creds.username,
            "REDDIT_PASSWORD": creds.password,
        }.items()
        if not val
    ]
    if missing:
        raise RuntimeError("Missing env vars: " + ", ".join(missing))

    client = RedditClient(creds)
    try:
        terms = expand_keyword(keyword)
        names = await discover_subreddits(client, terms)
        rows = await collect_metrics(client, names, terms)
    finally:
        await client.close()

    output = export_results(rows, output_stem=f"subreddit_results_{keyword}_{int(time.time())}")
    return rows, output


JOBS: dict[str, dict[str, Any]] = {}


def start_job(keyword: str) -> str:
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"keyword": keyword, "status": "running", "rows": [], "error": "", "output": ""}

    def target() -> None:
        try:
            rows, output = asyncio.run(run_pipeline(keyword))
            JOBS[job_id].update({"status": "done", "rows": rows, "output": output})
        except Exception as exc:
            JOBS[job_id].update({"status": "error", "error": str(exc)})

    threading.Thread(target=target, daemon=True).start()
    return job_id


def page_template(content: str) -> bytes:
    return f"""
    <html>
    <head>
      <title>Subreddit Finder</title>
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
            job = JOBS.get(job_id)
            if not job:
                self._html(page_template("<p>Job not found.</p>"), status=HTTPStatus.NOT_FOUND)
                return

            if job["status"] == "running":
                self._html(
                    page_template(
                        f"<p>Job running for <b>{html.escape(job['keyword'])}</b>... refresh this page.</p>"
                    )
                )
                return

            if job["status"] == "error":
                self._html(page_template(f"<p style='color:red'>Error: {html.escape(job['error'])}</p>"))
                return

            rows = job["rows"]
            output = html.escape(job["output"])
            head = "".join(f"<th>{c}</th>" for c in EXPORT_COLUMNS)
            body = "".join(
                "<tr>" + "".join(f"<td>{html.escape(str(r.get(c, '')))}</td>" for c in EXPORT_COLUMNS) + "</tr>"
                for r in rows[:200]
            )
            table = f"""
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
    server.serve_forever()


if __name__ == "__main__":
    run_server()
