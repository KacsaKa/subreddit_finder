from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .reddit_client import RedditClient

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DiscoveredSubreddit:
    name: str
    matched_term: str


async def discover_subreddits(
    client: RedditClient,
    terms: list[str],
    per_term_limit: int = 1000,
) -> dict[str, DiscoveredSubreddit]:
    """Discover subreddits by search query expansion and pagination."""
    discovered: dict[str, DiscoveredSubreddit] = {}

    for term in terms:
        after: str | None = None
        fetched = 0
        while True:
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
            data = payload.get("data", {})
            children: list[dict[str, Any]] = data.get("children", [])

            if not children:
                break

            for item in children:
                sr = item.get("data", {})
                name = sr.get("display_name")
                if not name:
                    continue
                key = name.lower()
                if key not in discovered:
                    discovered[key] = DiscoveredSubreddit(name=name, matched_term=term)

            fetched += len(children)
            after = data.get("after")
            if not after or fetched >= per_term_limit:
                break

        logger.info("Discovered %s results for term '%s'", fetched, term)

    return discovered
