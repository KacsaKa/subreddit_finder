# subreddit_finder

Ez a projekt egy **MVP Reddit adatgyűjtő script**, ami subreddit JSON feedekből (hot/new/top/rising) posztokat gyűjt, majd strukturált CSV datasetet készít.

A script célja, hogy gyorsan előállíts egy elemzésre kész adatfájlt pl.:
- SEO kutatáshoz
- content strategy tervezéshez
- SERP opportunity elemzéshez
- user intent kutatáshoz

---

## 1) Mit csinál a script pontosan?

A `scrape_reddit_posts.py`:
1. Beolvas egy `.xlsx` fájlt.
2. A sorokban található Reddit URL-eket normalizálja (pl. `r/stocks` -> `r/stocks/hot.json`).
3. HTTP GET kérést küld a Reddit JSON endpointokra.
4. Kérésenként maximum 25 posztot kér le (`limit=25`).
5. Ha van további oldal, a `data.after` mezővel lapoz (`--max-pages` szerint).
6. Kimenti a szükséges mezőket + számolt mutatókat.
7. `id` alapján deduplikál.
8. Eredményt ír `reddit_posts.csv` fájlba.

Kötelező request header:
- `User-Agent: reddit-data-research-bot/1.0`

---

## 2) Telepítés

### Előfeltétel
- Python 3.10+

### Függőségek
```bash
pip install requests pandas openpyxl
```

Ha virtuális környezetet használsz:
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install requests pandas openpyxl
```

---

## 3) Input Excel fájl elkészítése

A script nem fix oszlopnévhez kötött: **minden cellát végignéz**, és a Reddit feed URL-eket automatikusan felismeri.

Ajánlott oszlopok (a specifikációd alapján):
- `subreddit_name`
- `subscriber`
- `default reddit json`
- `top reddit json`
- `rising reddit json`
- `new reddit json`

### Támogatott URL formátumok
- `https://www.reddit.com/r/<subreddit>.json`
- `https://www.reddit.com/r/<subreddit>/hot.json`
- `https://www.reddit.com/r/<subreddit>/new.json`
- `https://www.reddit.com/r/<subreddit>/top.json`
- `https://www.reddit.com/r/<subreddit>/rising.json`
- `https://www.reddit.com/r/<subreddit>` (automatikusan `hot.json` lesz)

### Példa Excel sor
| subreddit_name | default reddit json | top reddit json |
|---|---|---|
| stocks | https://www.reddit.com/r/stocks.json | https://www.reddit.com/r/stocks/top.json |

> Tipp: egy sorban több feedet is adhatsz ugyanarra a subredditre (pl. default + top + new), a script mindet feldolgozza.

---

## 4) Futtatás (lépésről lépésre)

### Alap futtatás
```bash
python scrape_reddit_posts.py --input subreddits.xlsx
```

Ez alapból:
- output: `reddit_posts.csv`
- log: `scrape_errors.log`
- pagination: `--max-pages 1`
- lapozások között várakozás: `--request-delay 1.0`

### Javasolt valós futtatás több oldallal
```bash
python scrape_reddit_posts.py \
  --input subreddits.xlsx \
  --output reddit_posts.csv \
  --log-file scrape_errors.log \
  --max-pages 3 \
  --request-delay 1.5
```

### Paraméterek magyarázata
- `--input` (kötelező): bemeneti Excel fájl (`.xlsx`)
- `--output`: kimeneti CSV fájl neve/útvonala
- `--log-file`: hibák és folyamatlog fájlja
- `--max-pages`: feedenként maximum hány lapot kérjen le (`data.after`)
- `--request-delay`: várakozás másodpercben a lapozott kérések között

---

## 5) Kimenet: `reddit_posts.csv`

A CSV minden sora egy Reddit poszt.

### Mentett mezők
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

### Számolt mezők
- `title_length` = cím karakterhossz
- `word_count` = title + selftext összes szó
- `age_days` = jelenlegi idő és `created_utc` különbsége napokban
- `engagement_score` = `score + num_comments`

---

## 6) Hibatűrés és rate limit kezelés

A script kezeli:
- `429 Too Many Requests` válaszokat (Retry-After vagy exponenciális várakozás)
- `5xx` szerverhibákat (retry + backoff)
- hibás feedeket logolja a `--log-file` fájlba

Duplikációk ellen:
- poszt `id` alapján szűr, ezért ugyanaz a poszt csak egyszer kerül be a CSV-be.

---

## 7) Gyakori hibák és megoldások

### 1) `ModuleNotFoundError: No module named 'pandas'`
Telepítsd a függőségeket:
```bash
pip install pandas openpyxl requests
```

### 2) Üres CSV jön létre
Ellenőrizd:
- valóban `.xlsx` fájlt adtál-e meg
- a cellákban helyes Reddit URL-ek vannak-e
- a log fájlban vannak-e HTTP hibák

### 3) Lassú futás
- csökkentsd a `--max-pages` értékét
- csökkentsd a feed URL-ek számát
- óvatosan állítsd a `--request-delay` értéket (túl alacsony érték rate limitet okozhat)

---

## 8) Rövid gyorsstart (copy-paste)

```bash
pip install requests pandas openpyxl
python scrape_reddit_posts.py --input subreddits.xlsx --max-pages 3 --request-delay 1.5
```

Ha lefutott, a projekt mappában keresd:
- `reddit_posts.csv`
- `scrape_errors.log`
