# subreddit_finder

MVP Reddit scraper, amely Excel input alapján subreddit JSON feedekből poszt adatokat gyűjt és `reddit_posts.csv` fájlba ment.

## Telepítés

```bash
pip install requests pandas openpyxl
```

## Input formátum

A bemeneti Excel (`.xlsx`) fájlban lehetnek például az alábbi oszlopok:

- `subreddit_name`
- `subscriber`
- `default reddit json`
- `top reddit json`
- `rising reddit json`
- `new reddit json`

A script minden cellát megvizsgál, és az alábbi formátumú Reddit URL-eket használja fel:

- `https://www.reddit.com/r/<subreddit>.json`
- `https://www.reddit.com/r/<subreddit>/hot.json`
- `https://www.reddit.com/r/<subreddit>/new.json`
- `https://www.reddit.com/r/<subreddit>/top.json`
- `https://www.reddit.com/r/<subreddit>/rising.json`
- `https://www.reddit.com/r/<subreddit>` (automatikusan `hot.json` lesz)

## Futtatás

```bash
python scrape_reddit_posts.py --input subreddits.xlsx --output reddit_posts.csv --max-pages 3
```

Fő opciók:

- `--input`: kötelező bemeneti Excel fájl
- `--output`: kimeneti CSV (alapértelmezett: `reddit_posts.csv`)
- `--log-file`: hibalog fájl (alapértelmezett: `scrape_errors.log`)
- `--max-pages`: feedenként lekért oldalak száma (`data.after` pagination)
- `--request-delay`: késleltetés másodpercben paginált kérések között

## Mit csinál a script?

- `GET` kéréseket küld `User-Agent: reddit-data-research-bot/1.0` headerrel.
- Kérésenként `limit=25` értéket használ.
- Kezeli a rate limit (`429`) és szerver (`5xx`) hibákat retry/backoff stratégiával.
- Hibás lekéréseket logol.
- ID alapján deduplikálja a posztokat.
- Kiszámolja a következő mezőket:
  - `title_length`
  - `word_count`
  - `age_days`
  - `engagement_score`

## Output oszlopok

A `reddit_posts.csv` a következő oszlopokat tartalmazza:

- `title`
- `selftext`
- `url`
- `permalink`
- `ups`
- `downs`
- `score`
- `num_comments`
- `upvote_ratio`
- `subreddit`
- `subreddit_subscribers`
- `is_self`
- `stickied`
- `over_18`
- `quarantine`
- `archived`
- `link_flair_text`
- `author`
- `domain`
- `created_utc`
- `title_length`
- `word_count`
- `age_days`
- `engagement_score`
