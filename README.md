# Reddit Subreddit Discovery & Analytics (Beginner Guide)

This app gives you a simple webpage where you can type a keyword (for example `housing`) and get subreddit results.

If you are not technical, follow this file **exactly in order**.

---

## What you should see when it works

After setup, opening `http://localhost:8080` should show:

- a title: **Reddit Subreddit Discovery & Analytics**
- one text box: **Enter keyword, e.g. housing**
- a **Run** button

If you do not see this, go to **Troubleshooting** below.

---

## 1) Install Python (one-time)

You need **Python 3.10+**.

- Check if installed:

```bash
python --version
```

or on some computers:

```bash
python3 --version
```

If command is not found, install Python from: <https://www.python.org/downloads/>

---

## 2) Put `app.py` in a folder

Example folder name:

- `subreddit_finder`

Open Terminal (or Command Prompt/PowerShell on Windows), then go to that folder.

Example:

```bash
cd /path/to/subreddit_finder
```

---

## 3) Create an isolated environment (recommended, safe)

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### Windows (Command Prompt)

```cmd
python -m venv .venv
.venv\Scripts\activate.bat
```

After activation, your terminal usually shows `(.venv)` at the start.

---

## 4) Install dependencies (required)

Run:

```bash
pip install httpx pyarrow
```

Optional (better semantic expansion):

```bash
pip install nltk
python -m nltk.downloader wordnet
```

---

## 5) Start the app

Run:

```bash
python app.py
```

If successful, terminal shows:

```text
Server running at http://0.0.0.0:8080
```

⚠️ **Important:** keep this terminal window open while using the app.

---

## 6) Open the website

Open your browser and go to:

- <http://localhost:8080>

Type your keyword in the input box and click **Run**.

---

## API modes (easy explanation)

This app supports 2 modes:

1. **Public mode (free, no Reddit app needed)**
   - Works without API keys
   - Good for normal use/testing

2. **OAuth mode (optional, better coverage)**
   - Recommended if you want maximum reliability and NSFW coverage (if your account allows it)
   - Requires Reddit app credentials

If you do **nothing**, app runs in public mode automatically.

---

## Optional: OAuth setup (only if you want it)

Create a Reddit app at <https://www.reddit.com/prefs/apps> (`script` type), then set:

```bash
export REDDIT_CLIENT_ID="..."
export REDDIT_CLIENT_SECRET="..."
export REDDIT_USERNAME="..."
export REDDIT_PASSWORD="..."
export REDDIT_USER_AGENT="subreddit-finder-web/1.0 by <reddit_username>"
```

Windows PowerShell version:

```powershell
$env:REDDIT_CLIENT_ID="..."
$env:REDDIT_CLIENT_SECRET="..."
$env:REDDIT_USERNAME="..."
$env:REDDIT_PASSWORD="..."
$env:REDDIT_USER_AGENT="subreddit-finder-web/1.0 by <reddit_username>"
```

---

## Troubleshooting (for your exact errors)

### Error A: `Missing dependency 'httpx'. Install it with: pip install httpx pyarrow`

Cause: dependencies were not installed in the current environment.

Fix:

1. Activate your virtual environment
2. Run:

```bash
pip install httpx pyarrow
```

3. Start again:

```bash
python app.py
```

---

### Error B: `This site can’t be reached` / `localhost refused to connect`

Cause: server is not running, crashed, or wrong terminal/environment.

Fix checklist:

1. In terminal, run:

```bash
python app.py
```

2. Confirm you see:

```text
Server running at http://0.0.0.0:8080
```

3. Keep terminal open (do not close it)
4. Open browser at <http://localhost:8080>
5. If still failing, try:
   - <http://127.0.0.1:8080>
   - restart terminal and repeat steps 3–6 from this README

---

### Error C: `pip` cannot download packages (proxy/network)

If you are behind corporate proxy/firewall, set:

```bash
export HTTPS_PROXY="http://<proxy-host>:<proxy-port>"
export HTTP_PROXY="http://<proxy-host>:<proxy-port>"
```

Then retry:

```bash
pip install httpx pyarrow
```

---


### Error D: `403` on `/subreddits/search` in public mode

Cause: Reddit sometimes blocks anonymous search traffic from certain networks/IPs.

Fix options (best to worst):

1. Use OAuth mode (recommended): set Reddit credentials, then run again
2. Try again later (temporary blocks can clear)
3. Change network/VPN/proxy settings if your current network is restricted

Good news: the app now tries both `www.reddit.com` and `old.reddit.com` in public mode before failing.

---

## Safety/performance defaults in this app

- Uses safe request throttling + retry/backoff
- Uses up to 10 CPU workers max (safe cap)
- Shows progress bar and phase while running

---

## Output file location

Results are saved automatically into:

- `exports/`

Format:

- CSV by default
- switches to Parquet if file size is bigger than 20MB

---

## Output columns

- subreddit_name
- title
- description
- subscribers
- weekly_contribution
- weekly_active_users
- date_of_creation
- visibility_status
- nsfw_flag
