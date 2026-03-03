from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any

from .reddit_client import RedditAPIError, RedditClient
from .semantic import keyword_frequency_score

logger = logging.getLogger(__name__)


def _to_iso_date(created_utc: float | int | None) -> str:
    if not created_utc:
        return ""
    return dt.datetime.fromtimestamp(float(created_utc), tz=dt.timezone.utc).date().isoformat()


async def compute_weekly_contribution(
    client: RedditClient,
    subreddit_name: str,
    max_pages: int = 20,
) -> int:
    threshold = dt.datetime.now(tz=dt.timezone.utc).timestamp() - (7 * 24 * 3600)

    after: str | None = None
    total = 0
    pages = 0

    while pages < max_pages:
        payload = await client.request(
            f"/r/{subreddit_name}/new",
            params={"limit": 100, "after": after},
        )
        data = payload.get("data", {})
        children: list[dict[str, Any]] = data.get("children", [])
        if not children:
            break

        older_found = False
        for item in children:
            post = item.get("data", {})
            created = float(post.get("created_utc", 0))
            if created >= threshold:
                total += 1
            else:
                older_found = True

        if older_found:
            break

        after = data.get("after")
        pages += 1
        if not after:
            break

    return total


async def build_subreddit_record(
    client: RedditClient,
    subreddit_name: str,
    expanded_terms: list[str],
) -> dict[str, Any] | None:
    try:
        about_payload = await client.request(f"/r/{subreddit_name}/about")
        about = about_payload.get("data", {})
        weekly_contribution = await compute_weekly_contribution(client, subreddit_name)
    except RedditAPIError as err:
        logger.warning("Failed to collect '%s': %s", subreddit_name, err)
        return None

    description = " ".join(filter(None, [about.get("public_description", ""), about.get("description", "")])).strip()
    title = about.get("title", "")
    text_for_rank = f"{subreddit_name} {title} {description}"

    return {
        "subreddit_name": about.get("display_name", subreddit_name),
        "title": title,
        "description": description,
        "subscribers": int(about.get("subscribers", 0) or 0),
        "weekly_contribution": weekly_contribution,
        "weekly_active_users": int(about.get("accounts_active", 0) or 0),
        "date_of_creation": _to_iso_date(about.get("created_utc")),
        "visibility_status": about.get("subreddit_type", "unknown"),
        "nsfw_flag": bool(about.get("over18", False)),
        "score": keyword_frequency_score(text_for_rank, expanded_terms),
    }


async def collect_metrics(
    client: RedditClient,
    subreddits: list[str],
    expanded_terms: list[str],
    concurrency: int = 6,
) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(concurrency)

    async def worker(name: str) -> dict[str, Any] | None:
        async with sem:
            return await build_subreddit_record(client, name, expanded_terms)

    results = await asyncio.gather(*(worker(name) for name in subreddits))
    clean = [item for item in results if item is not None]
    clean.sort(key=lambda row: (row["score"], row["subscribers"]), reverse=True)
    return clean
