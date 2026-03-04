from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime, timezone

from .discovery import discover_subreddits
from .exporter import export_results
from .metrics import collect_metrics
from .reddit_client import RedditClient, RedditCredentials
from .semantic import expand_keyword


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reddit subreddit discovery + analytics")
    parser.add_argument("keyword", help="Search keyword, e.g. 'housing'")
    parser.add_argument("--max-expanded-terms", type=int, default=35)
    parser.add_argument("--per-term-limit", type=int, default=800)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--output-dir", default="exports")
    return parser.parse_args()


def _read_creds_from_env() -> RedditCredentials:
    required = {
        "REDDIT_CLIENT_ID": os.getenv("REDDIT_CLIENT_ID", ""),
        "REDDIT_CLIENT_SECRET": os.getenv("REDDIT_CLIENT_SECRET", ""),
        "REDDIT_USERNAME": os.getenv("REDDIT_USERNAME", ""),
        "REDDIT_PASSWORD": os.getenv("REDDIT_PASSWORD", ""),
        "REDDIT_USER_AGENT": os.getenv("REDDIT_USER_AGENT", "subreddit-finder/0.1"),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    return RedditCredentials(
        client_id=required["REDDIT_CLIENT_ID"],
        client_secret=required["REDDIT_CLIENT_SECRET"],
        username=required["REDDIT_USERNAME"],
        password=required["REDDIT_PASSWORD"],
        user_agent=required["REDDIT_USER_AGENT"],
    )


async def run() -> None:
    args = parse_args()
    creds = _read_creds_from_env()

    terms = expand_keyword(args.keyword, max_terms=args.max_expanded_terms)
    logger.info("Using %s expanded terms", len(terms))

    client = RedditClient(creds)
    try:
        discovered = await discover_subreddits(client, terms, per_term_limit=args.per_term_limit)
        subreddit_names = [item.name for item in discovered.values()]
        logger.info("Unique discovered subreddits: %s", len(subreddit_names))

        rows = await collect_metrics(
            client,
            subreddits=subreddit_names,
            expanded_terms=terms,
            concurrency=args.concurrency,
        )

    finally:
        await client.close()

    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    output = export_results(rows, output_stem=f"subreddit_results_{args.keyword}_{stamp}", directory=args.output_dir)

    logger.info("Exported %s rows to %s", len(rows), output)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
