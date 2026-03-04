# Subreddit Finder (updated from the original v1 script)

Ez a verzió a "régi, stabil" egyfájlos appra épül, de a kért módosításokkal:

- 10 kulcsszó egyszerre megadható UI-ban
- plusz kompatibilitás: egyetlen `keyword` mezőben is adhatsz meg vesszővel vagy új sorral több kulcsszót
- queue-alapú, alapból szekvenciális futás (`JOB_WORKERS=1`)
- Apple Silicon külön launcher + uvloop támogatás
- `/about`-only metrika (nincs weekly scan, nincs `/new` crawl)
- export séma pontosan 5 oszlop

## Kötelező export oszlopok

A CSV/Parquet ezekkel készül:

- `subreddit_name`
- `subreddit_url`
- `description`
- `subscribers`
- `active_users`

## Mi változott a régi v1-hez képest

1. **Weekly contribution teljesen eltávolítva**
   - nincs `compute_weekly_contribution()`
   - nincs `/r/{sub}/new` lapozás
   - subredditenként 1 API hívás: `/r/{sub}/about`

2. **Subreddit URL bekerült az outputba**
   - `about["url"]` alapján teljes URL képzés: `https://www.reddit.com/...`

3. **UI: 10 keyword mező**
   - `keyword1 ... keyword10`
   - egy submit több jobot queue-ba tesz

4. **Queue + worker modell**
   - nincs több thread-per-job burst
   - alapértelmezés: `JOB_WORKERS=1` (biztonságos)
   - opcionális: `JOB_WORKERS=2`

5. **/jobs dashboard**
   - státusz: `queued/running/done/error`
   - keyword
   - created/started/finished időbélyeg
   - output path, részletek link

6. **Public mód védelmi beállítások**
   - kevesebb retry
   - enyhébb discovery terhelés (`per_term_limit=200`)
   - kisebb keyword expansion tartomány (`8..12`)

---

## Telepítés

### macOS / Linux
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install httpx pyarrow
```

### Windows PowerShell
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install httpx pyarrow
```

---

## Futtatás

### Általános
```bash
python app.py
```

### Apple Silicon (ajánlott)
```bash
python app_apple_silicon.py
```

### Windows
```powershell
python app_windows.py
```

Nyisd meg: <http://localhost:8080>

---

## Környezeti változók

- `JOB_WORKERS` → `1` (default) vagy `2`
- `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` (OAuth app-only/client_credentials is működik)
- `REDDIT_USERNAME`, `REDDIT_PASSWORD` opcionális (user-context)

Ha nincs OAuth, az app public módban fut.

---

## Tippek a stabil futáshoz

- Public módban maradj `JOB_WORKERS=1`-en.
- Public 403 esetén a keresés megpróbálja a `www.reddit.com` mellett az `old.reddit.com` hostot is, egyszer relaxált (`include_over_18` nélkül) JSON kereséssel, majd végső fallbackként HTML keresésből is próbál subreddit neveket gyűjteni.
- Ha sok 403-at látsz, válts OAuth módra.
- Ha sok kulcsszót adsz meg, hagyd queue-ban lefutni (babysitting nélkül).

