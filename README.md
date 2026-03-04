# Subreddit Finder – 100% Beginner Safe Guide

Ez a projekt egy egyszerű weboldalt indít el, ahol **egy mezőbe beírod a kulcsszót**, és a háttérben lefut az adatgyűjtés.

## Fontos változás
- Alapértelmezésben most **public mód** fut automatikusan (OAuth nélkül is).
- Ha megadod a Reddit OAuth adatokat, az app automatikusan OAuth módra vált (`oauth_app` vagy `oauth_user`).
- Cél: OAuth nélkül is induljon "seamlessly", de OAuth módban stabilabb és megbízhatóbb marad nagy terhelésnél.

---

## 1) Mire lesz szükséged

1. Python 3.10+
2. Terminál (Windows: PowerShell)
3. Internet kapcsolat
4. (Erősen ajánlott) Reddit API adatok:
   - `REDDIT_CLIENT_ID`
   - `REDDIT_CLIENT_SECRET`

> OAuth adatok nélkül is fut (public), de sok 403 esetén érdemes OAuth-ra váltani a stabilitásért.

---

## 2) Telepítés (mindenkinek ajánlott)

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

## 3) Reddit OAuth beállítás (ajánlott, production)

Hozz létre Reddit appot (`script` vagy read-only app), majd állítsd be:

### macOS / Linux
```bash
export REDDIT_CLIENT_ID="..."
export REDDIT_CLIENT_SECRET="..."
export REDDIT_USER_AGENT="subreddit-finder-web/2.0 by <reddit_username>"
# opcionális user-context
export REDDIT_USERNAME="..."
export REDDIT_PASSWORD="..."
```

### Windows PowerShell
```powershell
$env:REDDIT_CLIENT_ID="..."
$env:REDDIT_CLIENT_SECRET="..."
$env:REDDIT_USER_AGENT="subreddit-finder-web/2.0 by <reddit_username>"
# opcionális user-context
$env:REDDIT_USERNAME="..."
$env:REDDIT_PASSWORD="..."
```

Auth mód automatikus:
- `client_id + client_secret` => `oauth_app` (client_credentials)
- + `username + password` => `oauth_user` (password flow)

---

## 4) Melyik scriptet indítsd?

Mostantól **két optimalizált indító script** van:

- Apple Silicon gépen:
  ```bash
  python app_apple_silicon.py
  ```
  Ez a profil a teljesítmény-magokra (performance cores) optimalizálja a worker számot, és ha elérhető, `uvloop`-ot használ.
- Windows gépen:
  ```powershell
  python app_windows.py
  ```

Ha ezek helyett `app.py`-t indítod, akkor általános profil fut.

---

## 4.1) Apple Silicon sebességhangolás

- A rendszer megpróbálja automatikusan kiolvasni a performance core számot (`hw.perflevel0.physicalcpu`).
- Ebből számolja a worker limitet (I/O workload miatt tipikusan `perf_cores * 2`, max 12).
- A request rate budgetet a státusz oldalon is látod (`Request rate budget`).

---

## 5) Hol írd be a kulcsszót?

1. Nyisd meg a böngészőben:
   - <http://localhost:8080>
2. A főoldalon látni fogsz egy beviteli mezőt:
   - `Enter keyword, e.g. housing`
3. Írd be a kulcsszót, kattints `Run`.

Ha fut a munka:
- látszik a **progress bar**
- látszik a fázis (`discovering`, `collecting`, `exporting`)
- látszik az auth mód (`oauth_app`, `oauth_user`, vagy `public`)

---

## 6) Public mód (alapértelmezett)

Nem kell külön beállítás, automatikusan működik:

```bash
python app.py
```

Ha mégis szeretnéd kikapcsolni a public módot (csak OAuth engedélyezése):

### macOS / Linux
```bash
export ALLOW_PUBLIC_MODE=0
python app.py
```

### Windows PowerShell
```powershell
$env:ALLOW_PUBLIC_MODE="0"
python app.py
```

---

## 7) Mit javítottunk a stabilitáson?

- 403-ra exponenciális backoff + jitter
- 429-ra `Retry-After` figyelembevétele + jitter
- részletes hibalogok (`x-ratelimit-*`, `retry-after`, `cf-ray`, stb.)
- endpoint telemetria (403/429 számláló endpointonként)
- valódi párhuzamosság lock nélküli globális/endpoint rate limiterrel
- keresés terhelésének csökkentése (kevesebb term + stop condition)

---

## 8) Output

Az eredmények az `exports/` mappába kerülnek.

- alapértelmezett: CSV
- ha >20MB: Parquet

Oszlopok:
- `subreddit_name`
- `subreddit_url`
- `description`
- `subscribers`
- `active_users`

A webes táblázatban a `subreddit_url` kattintható link.

Megjegyzés a teljesítményhez:
- A frissített script **nem számol weekly contribution metrikát**.
- Minden subreddithez csak az `/r/{subreddit}/about` adatokat használja, ezért jóval gyorsabb.

---

## 9) Gyors hibakeresés

### `localhost refused to connect`
- Nincs futó szerver. Indítsd újra: `python app_apple_silicon.py` vagy `python app_windows.py`.

### Sok 403 a logban
- Nincs OAuth vagy túl agresszív hálózati környezet.
- Ellenőrizd, hogy `oauth_app` / `oauth_user` mód fut-e.

### `Missing dependency 'httpx'`
- Nem telepítetted a csomagokat az aktív virtuális környezetbe.
