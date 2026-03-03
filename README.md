# subreddit_finder (Web App)

Single-file application (`app.py`) with a simple browser UI:

- enter one keyword (example: `housing`)
- app runs discovery + analytics in the background
- live progress bar during run
- results table when complete
- export automatically to CSV (or Parquet when CSV exceeds 20MB)

## Do you need a Reddit API account?

Yes. You must create a Reddit app and provide credentials via environment variables.

1. Go to: <https://www.reddit.com/prefs/apps>
2. Create app type: **script**
3. Set:

```bash
export REDDIT_CLIENT_ID="..."
export REDDIT_CLIENT_SECRET="..."
export REDDIT_USERNAME="..."
export REDDIT_PASSWORD="..."
export REDDIT_USER_AGENT="subreddit-finder-web/1.0 by <reddit_username>"
```

Without these values, the job cannot authenticate and will fail.

## Safe performance behavior

- Uses asynchronous requests with a CPU worker cap of **up to 10 workers** (`min(os.cpu_count(), 10)`)
- Still prioritizes safe Reddit usage with throttling + retry/backoff
- Progress bar updates across phases: expansion, discovery, metrics, export

## What it does

1. OAuth-authenticates with Reddit
2. Expands your keyword semantically (curated + optional WordNet)
3. Discovers subreddits via `/subreddits/search` pagination
4. Deduplicates by subreddit name
5. Fetches subreddit metadata (`/r/{sub}/about`)
6. Computes weekly contribution from `/r/{sub}/new` over last 7 days
7. Ranks results
8. Exports to `exports/`

## Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install httpx pyarrow
```

Optional semantic upgrade (WordNet):

```bash
pip install nltk
python -m nltk.downloader wordnet
```

If your environment requires proxy settings:

```bash
export HTTPS_PROXY="http://<proxy-host>:<proxy-port>"
export HTTP_PROXY="http://<proxy-host>:<proxy-port>"
```

## Run

```bash
python app.py
```

Then open:

- `http://localhost:8080`

## Output fields

- subreddit_name
- title
- description
- subscribers
- weekly_contribution
- weekly_active_users
- date_of_creation
- visibility_status
- nsfw_flag
