# olmca-music-downloader

Automates authenticated downloads from a protected HTML site and keeps a persistent record of which files were found/downloaded.

## Features
- Loads credentials and config from `.env` (DO NOT commit).
- Crawls a site and downloads files matching configured extensions.
- Persistent tracking with a SQLite DB (`.omd_state.sqlite`) to avoid re-downloading files.
- Uses ETag / Last-Modified to detect changes and redownload when needed.
- CLI flags: `--dry-run`, `--force`, `--status`.

## Requirements
- Python 3.8+
- Recommended: use a virtual environment (venv)

## Setup (recommended)
From the project root:
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Copy and configure your secrets:
```bash
cp .env.example .env
# Edit .env and fill START_URL, USERNAME, PASSWORD, DOWNLOAD_DIR, FILE_EXTENSIONS
```

## Files of interest
- `omd.py` — main crawler / downloader
- `.env.example` — template for `.env` (DO NOT commit `.env`)
- `requirements.txt` — python-dotenv, requests, beautifulsoup4
- `.omd_state.sqlite` — persistent DB in project root (created automatically)

If you want the DB ignored in git, add `.omd_state.sqlite` to `.gitignore`.

## Usage
Dry-run (recommended first; will HEAD check remote files but won't save):
```bash
python3 omd.py --dry-run
```

Actual run (downloads new/changed files according to DB records):
```bash
python3 omd.py
```

Force re-download even if previously downloaded:
```bash
python3 omd.py --force
```

Show DB status and recent records:
```bash
python3 omd.py --status
```

You can combine flags, e.g. `--dry-run --force` for a safe verification of what would be re-downloaded.

## Scheduling
Run weekly or every 2 weeks using cron or launchd (macOS). Example cron (weekly at 03:00 Monday):
```
0 3 * * 1 /full/path/to/project/.venv/bin/python /full/path/to/project/omd.py >> /full/path/to/project/omd.log 2>&1
```

Example (approx. every 2 weeks) using cron (run every 14 days):
```
0 3 */14 * * /full/path/to/project/.venv/bin/python /full/path/to/project/omd.py >> /full/path/to/project/omd.log 2>&1
```

## Notes / security
- Never commit `.env` or any secrets. Use `.env.example` as the template.
- `.omd_state.sqlite` contains metadata; back it up if you want history but consider adding it to `.gitignore` if you don't want it in the repo.
- Respect target sites' terms of service and rate limits. Adjust the crawler delay (time.sleep) if needed.

If you want, I can also:
- add `.omd_state.sqlite` to `.gitignore`
- add an example launchd plist for macOS to run the script on a schedule
- commit these changes to the repo