# subreddit_finder

CLI application for **Reddit subreddit discovery + analytics export**.

It accepts one keyword (example: `housing`), expands it semantically, discovers subreddits via Reddit search pagination, computes weekly contribution counts from `/new`, and exports to CSV or Parquet.

## Features

- OAuth-authenticated Reddit API access
- Best-effort subreddit discovery (`/subreddits/search`, paginated)
- Semantic expansion (curated terms + optional WordNet)
- Deduplication by subreddit name
- Per-subreddit metric collection
- Weekly contribution computation (submissions in last 7 days)
- Ranking via keyword-frequency score
- Export:
  - CSV by default
  - Parquet if CSV would exceed 20MB
- Includes NSFW communities when your authenticated account can access them

## Output columns

- `subreddit_name`
- `title`
- `description`
- `subscribers`
- `weekly_contribution`
- `weekly_active_users`
- `date_of_creation`
- `visibility_status`
- `nsfw_flag`

## Project structure

- `src/subreddit_finder/discovery.py` — search + pagination
- `src/subreddit_finder/semantic.py` — query expansion and scoring
- `src/subreddit_finder/metrics.py` — `/about` + weekly contribution computation
- `src/subreddit_finder/exporter.py` — CSV/Parquet output switch
- `src/subreddit_finder/reddit_client.py` — OAuth + retries + rate throttling
- `src/subreddit_finder/cli.py` — user entrypoint

## Setup

### 1) Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2) Reddit OAuth configuration

Create a Reddit app at <https://www.reddit.com/prefs/apps>:

- Type: **script**
- Collect:
  - client ID
  - client secret

Set environment variables:

```bash
export REDDIT_CLIENT_ID="..."
export REDDIT_CLIENT_SECRET="..."
export REDDIT_USERNAME="..."
export REDDIT_PASSWORD="..."
export REDDIT_USER_AGENT="subreddit-finder/0.1 by <reddit_username>"
```

> Important: use an account allowed to view 18+ communities if you want NSFW subreddits included.

## Run

```bash
subreddit-finder housing --max-expanded-terms 35 --per-term-limit 800 --concurrency 6 --output-dir exports
```

### Key CLI options

- `keyword` (positional): base search term
- `--max-expanded-terms`: cap for semantic expansion list
- `--per-term-limit`: max search results processed per expanded term
- `--concurrency`: concurrent subreddit metric workers
- `--output-dir`: output folder

## How weekly contribution is computed

For each discovered subreddit:

1. Fetch `/r/{subreddit}/new` posts.
2. Count submissions where `created_utc >= now - 7 days`.
3. Stop paging when older posts are encountered.

## Notes & limits

- Reddit does not provide full subreddit enumeration; discovery is best-effort.
- `accounts_active` is used for `weekly_active_users` when available, though it may reflect currently active users.
- Some private/restricted subreddits may fail metadata fetch; failures are logged.
- WordNet expansion is optional and only used when `nltk` + corpus are available locally.

## Development checks

```bash
python -m compileall src
python -m subreddit_finder.cli --help
```
