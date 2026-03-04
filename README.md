# subreddit_finder (Web App)

Single-file application (`app.py`) with a simple browser UI:

- enter one keyword (example: `housing`)
- app runs discovery + analytics in the background
- live progress bar during run
- results table when complete
- export automatically to CSV (or Parquet when CSV exceeds 20MB)

## Reddit API setup (free/public mode supported)

You can run this app in two modes:

1. **Public free mode (no Reddit app required)**
   - The app uses Reddit's public JSON API endpoints (`www.reddit.com/...json`)
   - No `REDDIT_CLIENT_ID` or `REDDIT_CLIENT_SECRET` needed
   - Good for discovery/testing

2. **OAuth mode (recommended for full access)**
   - Required for best reliability and broader access (including NSFW communities if your account is allowed)
   - Create a Reddit app at <https://www.reddit.com/prefs/apps> (type: `script`)
   - Set:

```bash
export REDDIT_CLIENT_ID="..."
export REDDIT_CLIENT_SECRET="..."
export REDDIT_USERNAME="..."
export REDDIT_PASSWORD="..."
export REDDIT_USER_AGENT="subreddit-finder-web/1.0 by <reddit_username>"
```

If these are missing, app automatically falls back to **public** mode.

## Safe performance behavior

- Uses asynchronous requests with worker cap of **up to 10 cores/workers** (`min(os.cpu_count(), 10)`)
- Prioritizes safe behavior with throttling + retry/backoff
- Progress bar updates across phases: start, discovery, metrics, export
- UI shows current mode (`public` or `oauth`)

## What it does

1. Authenticates (OAuth when credentials exist, else public mode)
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
