#!/usr/bin/env python3
"""Reddit subreddit JSON scraper for building structured post datasets."""

from __future__ import annotations

import argparse
import logging
import os
import platform
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import pandas as pd
import requests

USER_AGENT = "reddit-data-research-bot/1.0"
REQUEST_TIMEOUT = 20
POST_LIMIT = 25
RESERVED_FEEDS = {"hot", "new", "top", "rising"}

OUTPUT_COLUMNS = [
    "title",
    "selftext",
    "url",
    "permalink",
    "ups",
    "downs",
    "score",
    "num_comments",
    "upvote_ratio",
    "subreddit",
    "subreddit_subscribers",
    "is_self",
    "stickied",
    "over_18",
    "quarantine",
    "archived",
    "link_flair_text",
    "author",
    "domain",
    "created_utc",
    "title_length",
    "word_count",
    "age_days",
    "engagement_score",
]


@dataclass
class FetchResult:
    posts: list[dict]
    next_after: Optional[str]


class ProgressBar:
    def __init__(self, total: int) -> None:
        self.total = max(total, 1)
        self.current = 0
        self.started_at = time.time()

    @staticmethod
    def _format_duration(seconds: float) -> str:
        whole = max(int(seconds), 0)
        hours, rem = divmod(whole, 3600)
        minutes, secs = divmod(rem, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}"

    def update(self, step: int = 1) -> None:
        self.current += step
        ratio = min(self.current / self.total, 1.0)
        width = 30
        done = int(width * ratio)
        bar = "#" * done + "-" * (width - done)

        elapsed = time.time() - self.started_at
        avg_per_page = elapsed / self.current if self.current else 0
        remaining_pages = max(self.total - self.current, 0)
        eta_seconds = remaining_pages * avg_per_page

        elapsed_txt = self._format_duration(elapsed)
        eta_txt = self._format_duration(eta_seconds)

        print(
            f"\rProgress: [{bar}] {self.current}/{self.total} page | "
            f"current time spent {elapsed_txt} / ETA {eta_txt}",
            end="",
            flush=True,
        )

    def finish(self) -> None:
        print()


class RequestPacer:
    def __init__(self, min_interval_seconds: float) -> None:
        self.min_interval_seconds = max(min_interval_seconds, 0)
        self.last_request_ts = 0.0

    def wait_before_request(self) -> None:
        if self.min_interval_seconds <= 0:
            return
        elapsed = time.time() - self.last_request_ts
        wait_for = self.min_interval_seconds - elapsed
        if wait_for > 0:
            time.sleep(wait_for)

    def mark_request(self) -> None:
        self.last_request_ts = time.time()


def setup_logging(log_file: Path) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def normalize_feed_url(value: str) -> Optional[str]:
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    if not text.startswith("http"):
        if text.lower() in RESERVED_FEEDS:
            return None
        text = f"https://www.reddit.com/r/{text}"

    parsed = urlparse(text)
    if "reddit.com" not in parsed.netloc:
        return None

    path = parsed.path.rstrip("/")

    if re.fullmatch(r"/r/[^/]+", path):
        subreddit_name = path.split("/")[-1].lower()
        if subreddit_name in RESERVED_FEEDS:
            return None
        path = f"{path}/hot.json"
    elif re.fullmatch(r"/r/[^/]+/(hot|new|top|rising)", path):
        path = f"{path}.json"
    elif re.fullmatch(r"/r/[^/]+\.json", path):
        pass
    elif re.fullmatch(r"/r/[^/]+/(hot|new|top|rising)\.json", path):
        pass
    else:
        return None

    return urlunparse((parsed.scheme or "https", parsed.netloc, path, "", parsed.query, ""))


def extract_urls_from_row(row: pd.Series) -> list[str]:
    urls: list[str] = []
    for val in row.tolist():
        normalized = normalize_feed_url(val)
        if normalized:
            urls.append(normalized)
    return urls


def request_feed(
    session: requests.Session,
    base_url: str,
    after: Optional[str],
    pacer: RequestPacer,
) -> FetchResult:
    parsed = urlparse(base_url)
    params = parse_qs(parsed.query)
    params["limit"] = [str(POST_LIMIT)]
    if after:
        params["after"] = [after]
    else:
        params.pop("after", None)

    url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", urlencode(params, doseq=True), ""))

    retries = 5
    for attempt in range(1, retries + 1):
        pacer.wait_before_request()
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        pacer.mark_request()

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            sleep_seconds = int(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
            logging.warning("Rate limit reached for %s. Waiting %s seconds.", url, sleep_seconds)
            time.sleep(sleep_seconds)
            continue

        if 500 <= response.status_code < 600:
            sleep_seconds = 2**attempt
            logging.warning(
                "Server error %s for %s (attempt %s/%s). Waiting %s seconds.",
                response.status_code,
                url,
                attempt,
                retries,
                sleep_seconds,
            )
            time.sleep(sleep_seconds)
            continue

        response.raise_for_status()
        payload = response.json()
        children = payload.get("data", {}).get("children", [])
        posts = [child.get("data", {}) for child in children if isinstance(child, dict)]
        next_after = payload.get("data", {}).get("after")
        return FetchResult(posts=posts, next_after=next_after)

    raise requests.HTTPError(f"Failed to fetch {url} after {retries} retries")


def compute_word_count(title: str, selftext: str) -> int:
    text = f"{title or ''} {selftext or ''}".strip()
    return len(text.split()) if text else 0


def transform_post(post: dict, now_ts: float) -> dict:
    created_utc = post.get("created_utc")
    created_ts = float(created_utc) if created_utc else 0.0
    age_days = max((now_ts - created_ts) / 86400, 0)

    title = post.get("title") or ""
    selftext = post.get("selftext") or ""
    score = int(post.get("score") or 0)
    num_comments = int(post.get("num_comments") or 0)

    return {
        "id": post.get("id"),
        "title": title,
        "selftext": selftext,
        "url": post.get("url"),
        "permalink": post.get("permalink"),
        "ups": post.get("ups"),
        "downs": post.get("downs"),
        "score": score,
        "num_comments": num_comments,
        "upvote_ratio": post.get("upvote_ratio"),
        "subreddit": post.get("subreddit"),
        "subreddit_subscribers": post.get("subreddit_subscribers"),
        "is_self": post.get("is_self"),
        "stickied": post.get("stickied"),
        "over_18": post.get("over_18"),
        "quarantine": post.get("quarantine"),
        "archived": post.get("archived"),
        "link_flair_text": post.get("link_flair_text"),
        "author": post.get("author"),
        "domain": post.get("domain"),
        "created_utc": created_utc,
        "title_length": len(title),
        "word_count": compute_word_count(title, selftext),
        "age_days": round(age_days, 4),
        "engagement_score": score + num_comments,
    }


def transform_post_worker(payload: tuple[dict, float]) -> dict:
    post, now_ts = payload
    return transform_post(post, now_ts)


def collect_feed_urls(input_df: pd.DataFrame) -> list[str]:
    urls: list[str] = []
    for _, row in input_df.iterrows():
        urls.extend(extract_urls_from_row(row))
    return urls


def auto_cpu_workers() -> int:
    cores = os.cpu_count() or 1
    if platform.system() == "Darwin" and platform.machine().lower() in {"arm64", "aarch64"}:
        return max(cores, 1)
    return max(cores - 1, 1)


def transform_posts_parallel(unique_posts: list[dict], now_ts: float, cpu_workers: int) -> list[dict]:
    if not unique_posts:
        return []

    if cpu_workers <= 1 or len(unique_posts) < 200:
        return [transform_post(post, now_ts) for post in unique_posts]

    payloads = [(post, now_ts) for post in unique_posts]
    chunk_size = max(25, len(payloads) // (cpu_workers * 4))
    with ProcessPoolExecutor(max_workers=cpu_workers) as executor:
        return list(executor.map(transform_post_worker, payloads, chunksize=chunk_size))


def scrape(
    input_file: Path,
    output_file: Path,
    max_pages: int,
    request_delay: float,
    cpu_workers: int,
) -> None:
    df = pd.read_excel(input_file)
    feed_urls = collect_feed_urls(df)

    headers = {"User-Agent": USER_AGENT}
    session = requests.Session()
    session.headers.update(headers)

    unique_posts_by_id: dict[str, dict] = {}

    progress = ProgressBar(total=max(len(feed_urls) * max_pages, 1))
    pacer = RequestPacer(min_interval_seconds=request_delay)

    for feed_url in feed_urls:
        logging.info("Fetching feed: %s", feed_url)
        after = None

        for page in range(max_pages):
            progress.update()
            try:
                result = request_feed(session, feed_url, after, pacer)
            except Exception as exc:
                logging.error("Failed feed request for %s page %s: %s", feed_url, page + 1, exc)
                break

            if not result.posts:
                break

            for post in result.posts:
                post_id = post.get("id")
                if not post_id or post_id in unique_posts_by_id:
                    continue
                unique_posts_by_id[post_id] = post

            if not result.next_after:
                break

            after = result.next_after

    progress.finish()

    now_ts = datetime.now(tz=timezone.utc).timestamp()
    unique_posts = list(unique_posts_by_id.values())
    logging.info("Transforming %s unique posts with cpu_workers=%s", len(unique_posts), cpu_workers)
    all_rows = transform_posts_parallel(unique_posts=unique_posts, now_ts=now_ts, cpu_workers=cpu_workers)

    if not all_rows:
        logging.warning("No posts were collected. Writing empty output CSV.")

    out_df = pd.DataFrame(all_rows)
    if not out_df.empty:
        out_df = out_df.drop(columns=["id"], errors="ignore")
        out_df = out_df.reindex(columns=OUTPUT_COLUMNS)
    else:
        out_df = pd.DataFrame(columns=OUTPUT_COLUMNS)

    out_df.to_csv(output_file, index=False)
    logging.info("Wrote %s rows to %s", len(out_df), output_file)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bulk scrape Reddit JSON feeds from an Excel source.")
    parser.add_argument(
        "--input",
        type=Path,
        help="Path to input Excel file (.xlsx). If omitted, the script prompts for it at startup.",
    )
    parser.add_argument("--output", type=Path, default=Path("reddit_posts.csv"), help="Output CSV file path")
    parser.add_argument("--log-file", type=Path, default=Path("scrape_errors.log"), help="Log file path")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=1,
        help="Maximum number of paginated requests per feed endpoint",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=1.5,
        help="Minimum delay in seconds between all HTTP requests",
    )
    parser.add_argument(
        "--cpu-workers",
        type=int,
        default=0,
        help="CPU worker processes for post transformation (0 = auto, Apple Silicon uses all cores)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.log_file)

    if args.max_pages < 1:
        raise ValueError("--max-pages must be at least 1")

    cpu_workers = auto_cpu_workers() if args.cpu_workers <= 0 else args.cpu_workers
    if cpu_workers < 1:
        raise ValueError("--cpu-workers must be at least 1")

    input_file = args.input
    while input_file is None:
        raw_input_path = input("Input file path (.xlsx): ").strip().strip('"').strip("'")
        if not raw_input_path:
            print("Input file path is required. Please provide a valid .xlsx file path.")
            continue

        candidate = Path(raw_input_path).expanduser()
        if not candidate.exists():
            print(f"File not found: {candidate}")
            continue
        if candidate.suffix.lower() != ".xlsx":
            print("Please provide an .xlsx Excel file.")
            continue
        input_file = candidate

    logging.info("Using input file: %s", input_file)

    scrape(
        input_file=input_file,
        output_file=args.output,
        max_pages=args.max_pages,
        request_delay=max(args.request_delay, 0),
        cpu_workers=cpu_workers,
    )


if __name__ == "__main__":
    main()
