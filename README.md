# Subreddit Finder – High Throughput Edition

Ez a verzió egy **több-jobos**, nagy áteresztésű scraper rendszer:

- akár **50+ keyword** egyszerre queue-ba tehető,
- jobonként külön státusz/progress/output,
- közös globális limiter védi az összes kérést,
- csak az `/about` endpointot használja metrikához (nincs post/new scrape).

## Kötelező output séma (pontosan ez)

- `subreddit_name`
- `subreddit_url`
- `description`
- `subscribers`
- `active_users`

## Fő architektúra

1. **Global shared rate limiter**
   - minden job ugyanazt a request budgetet használja.
2. **JobManager + queue + worker pool**
   - job státuszok: `queued`, `running`, `done`, `error`.
3. **Streaming pipeline jobonként**
   - discovery producer → metrics consumer(ek) → streamelt CSV writer.
4. **Közös HTTP kapcsolatpool**
   - jobb keepalive és throughput.

---

## Indítás

### macOS / Linux
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install httpx pyarrow
python app.py
```

### Windows PowerShell
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install httpx pyarrow
python app.py
```

Alap URL: <http://localhost:8080>

---

## UI funkciók

### `/`
- 5 gyors keyword mező (`keyword1...keyword5`)
- bulk textarea (50+ keyword, soronként)

### `/jobs`
Dashboard oszlopok:
- Keyword
- Status
- Phase
- Progress
- Done/Total
- Running since
- Elapsed
- ETA
- Linkek (`view`, `download`)

### `/job?id=...`
Részletes nézet:
- current subreddit
- current discovery term
- 403/429 telemetry endpointonként
- top preview táblázat kattintható `subreddit_url` linkkel

---

## Bulk API

### `POST /jobs/bulk`
Támogatott formátumok:
- `text/plain` vagy form mező (`keywords`) newline-separated
- `application/json` tömb: `{"keywords": [...]} ` helyett közvetlen JSON array (`["housing", "rent"]`)

---

## Teljesítményhangolás

A rendszer automatikusan auth mód alapján választ tuningot:

- **Public mód**: konzervatívabb global RPS + kisebb worker szám
- **OAuth mód**: magasabb throughput (több párhuzamos job és worker)

Fontos: ez I/O-bound workload, a gyorsulás kulcsa a concurrency + connection reuse + global limiter.

---

## Timeout és stabilitás

- subreddit-szintű hard timeout: `SUBREDDIT_TIMEOUT_SECONDS` (default `75`)
- timeout esetén skip, job nem fagy be
- 403/429 warning logok megmaradnak diagnosztikára

---

## Fájlnevek és export

Minden job külön fájlba ír:

- `exports/subreddit_results_{keyword}_{job_id}.csv`

Nagy fájl esetén opcionálisan készülhet parquet is.

---

## Platform launcherek

- Apple Silicon: `python app_apple_silicon.py`
- Windows: `python app_windows.py`

Ezek a launcherek megtartva maradtak.
