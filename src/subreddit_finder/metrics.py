from __future__ import annotations

import asyncio
import logging
import urllib.parse
from typing import Any

from .reddit_client import RedditAPIError, RedditClient
from .semantic import keyword_frequency_score

logger = logging.getLogger(__name__)


async def build_subreddit_record(
    client: RedditClient,
    subreddit_name: str,
    expanded_terms: list[str],
) -> dict[str, Any] | None:
    try:
        about_payload = await client.request(f"/r/{subreddit_name}/about")
        about = about_payload.get("data", {})
    except RedditAPIError as err:
        logger.warning("Failed to collect '%s': %s", subreddit_name, err)
        return None

    display_name = about.get("display_name", subreddit_name)
    relative = about.get("url") or f"/r/{display_name}/"
    description = " ".join(filter(None, [about.get("public_description", ""), about.get("description", "")])).strip()
    text_for_rank = f"{display_name} {description}"

    return {
        "subreddit_name": display_name,
        "subreddit_url": urllib.parse.urljoin("https://www.reddit.com", relative),
        "description": description,
        "subscribers": int(about.get("subscribers", 0) or 0),
        "active_users": int(about.get("accounts_active", 0) or 0),
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
