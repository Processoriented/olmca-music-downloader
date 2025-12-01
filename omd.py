#!/usr/bin/env python3

import os
import re
import sqlite3
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from dotenv import load_dotenv
import time
import argparse
from datetime import datetime

# Load .env (if present)
load_dotenv()

# --- Configuration (from environment/.env) ---
START_URL = os.getenv("START_URL", "http://example.com")
USERNAME = os.getenv("USERNAME", "")
PASSWORD = os.getenv("PASSWORD", "")
DOWNLOAD_DIR = os.getenv(
    "DOWNLOAD_DIR",
    os.path.join(os.path.expanduser("~"), "Downloads", "Automated_Web_Files")
)
# Accept pipe or comma separated extensions (with or without leading dot)
FILE_EXTENSIONS = os.getenv("FILE_EXTENSIONS", ".pdf|.zip|.exe|.doc|.docx|.xlsx|.pptx")

# Local DB in project root for persistent tracking
DB_PATH = os.path.join(os.path.dirname(__file__), ".omd_state.sqlite")

def normalize_extensions(ext_string):
    parts = [p.strip() for p in re.split(r"[|,]", ext_string) if p.strip()]
    return tuple((p if p.startswith(".") else "." + p).lower() for p in parts)

DOWNLOAD_EXTENSIONS = normalize_extensions(FILE_EXTENSIONS)

VISITED_URLS = set()
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; OMD/1.0; +https://github.com/processoriented/olmca-music-downloader)'
}

# Create a session and attach basic auth if credentials are present
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
if USERNAME and PASSWORD:
    SESSION.auth = (USERNAME, PASSWORD)

# ---- SQLite state helpers ----
def init_db(path=DB_PATH):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS files (
            url TEXT PRIMARY KEY,
            filename TEXT,
            local_path TEXT,
            status TEXT,              -- 'downloaded', 'failed', 'skipped', 'pending'
            etag TEXT,
            last_modified TEXT,
            last_checked TEXT,
            last_downloaded TEXT
        );
        """
    )
    conn.commit()
    return conn

def get_record(conn, url):
    cur = conn.cursor()
    cur.execute("SELECT url, filename, local_path, status, etag, last_modified, last_checked, last_downloaded FROM files WHERE url = ?", (url,))
    row = cur.fetchone()
    if not row:
        return None
    keys = ["url", "filename", "local_path", "status", "etag", "last_modified", "last_checked", "last_downloaded"]
    return dict(zip(keys, row))

def upsert_record(conn, url, filename=None, local_path=None, status=None, etag=None, last_modified=None, last_checked=None, last_downloaded=None):
    cur = conn.cursor()
    # SELECT existing values to allow partial updates
    cur.execute("SELECT url, filename, local_path, status, etag, last_modified, last_checked, last_downloaded FROM files WHERE url = ?", (url,))
    row = cur.fetchone()
    if row:
        cur.execute(
            """
            UPDATE files SET
              filename=COALESCE(?, filename),
              local_path=COALESCE(?, local_path),
              status=COALESCE(?, status),
              etag=COALESCE(?, etag),
              last_modified=COALESCE(?, last_modified),
              last_checked=COALESCE(?, last_checked),
              last_downloaded=COALESCE(?, last_downloaded)
            WHERE url=?
            """,
            (filename, local_path, status, etag, last_modified, last_checked, last_downloaded, url)
        )
    else:
        cur.execute(
            "INSERT INTO files (url, filename, local_path, status, etag, last_modified, last_checked, last_downloaded) VALUES (?,?,?,?,?,?,?,?)",
            (url, filename, local_path, status, etag, last_modified, last_checked, last_downloaded)
        )
    conn.commit()

def should_download(conn, url, force=False):
    """
    Return (bool, reason) -> whether we should fetch the file.
    Logic:
      - If force True -> download
      - If no record -> download
      - If record.status != 'downloaded' -> download
      - If record.status == 'downloaded' -> attempt HEAD to compare ETag or Last-Modified
         - If remote ETag/Last-Modified differ -> download
         - If remote has no metadata -> skip (conservative)
    """
    if force:
        return True, "force"
    rec = get_record(conn, url)
    if rec is None:
        return True, "no-db-record"
    if rec.get("status") != "downloaded":
        return True, f"status={rec.get('status')}"
    # already downloaded -> verify remote metadata
    try:
        head = SESSION.head(url, timeout=10, allow_redirects=True)
        head.raise_for_status()
        remote_etag = head.headers.get("ETag")
        remote_lm = head.headers.get("Last-Modified")
        # Update last_checked
        upsert_record(conn, url, last_checked=datetime.utcnow().isoformat())
        if remote_etag and (rec.get("etag") != remote_etag):
            return True, "etag-changed"
        if remote_lm and (rec.get("last_modified") != remote_lm):
            return True, "last-modified-changed"
        # no differences found or no metadata available -> skip
        # If both remote_etag and remote_lm are None -> conservative skip
        if not remote_etag and not remote_lm:
            return False, "no-remote-metadata"
        return False, "no-change"
    except requests.RequestException:
        # If HEAD failed, skip to avoid accidental re-download; user may use --force
        return False, "head-failed"

# ---- Download / crawling logic (with DB updates) ----
def download_file(conn, url, target_dir, dry_run=False):
    try:
        parsed = urlparse(url)
        filename = os.path.basename(parsed.path) or ""
        if not filename:
            ext = os.path.splitext(parsed.path)[1] or ""
            filename = f"downloaded_file_{int(time.time())}{ext}"

        os.makedirs(target_dir, exist_ok=True)
        filepath = os.path.join(target_dir, filename)

        # If file already exists locally, update DB and skip
        if os.path.exists(filepath):
            if not dry_run:
                upsert_record(conn, url, filename=filename, local_path=filepath, status="downloaded", last_downloaded=datetime.utcnow().isoformat())
            print(f"  [SKIP] Already exists locally: {filename}")
            return True

        if dry_run:
            # show HEAD status for dry-run and do not write DB
            try:
                head = SESSION.head(url, timeout=10, allow_redirects=True)
                status = head.status_code
                print(f"  [DRY-RUN] Would download: {url}  (HEAD status: {status})")
            except requests.RequestException:
                print(f"  [DRY-RUN] Would download: {url}  (HEAD failed)")
            return True

        # mark pending in DB
        upsert_record(conn, url, filename=filename, local_path=filepath, status="pending", last_checked=datetime.utcnow().isoformat())

        resp = SESSION.get(url, stream=True, timeout=30)
        resp.raise_for_status()

        # Save metadata if present
        remote_etag = resp.headers.get("ETag")
        remote_lm = resp.headers.get("Last-Modified")

        with open(filepath, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    fh.write(chunk)

        # mark downloaded
        upsert_record(
            conn,
            url,
            filename=filename,
            local_path=filepath,
            status="downloaded",
            etag=remote_etag,
            last_modified=remote_lm,
            last_checked=datetime.utcnow().isoformat(),
            last_downloaded=datetime.utcnow().isoformat(),
        )

        print(f"  [SUCCESS] {filename} -> {filepath}")
        return True
    except requests.RequestException as e:
        if not dry_run:
            upsert_record(conn, url, status="failed", last_checked=datetime.utcnow().isoformat())
        print(f"  [ERROR] Failed to download {url}: {e}")
        return False
    except Exception as e:
        if not dry_run:
            upsert_record(conn, url, status="failed", last_checked=datetime.utcnow().isoformat())
        print(f"  [ERROR] An unexpected error occurred during download: {e}")
        return False

def crawl_and_download(current_url, base_domain, conn, dry_run=False, force=False):
    if current_url in VISITED_URLS:
        return
    VISITED_URLS.add(current_url)

    print("-" * 50)
    print(f"Crawling: {current_url}")

    if is_downloadable(current_url):
        print("[INFO] Current URL is a direct download link.")
        should, reason = should_download(conn, current_url, force=force)
        print(f"  [DECISION] should_download={should} ({reason})")
        if should:
            download_file(conn, current_url, DOWNLOAD_DIR, dry_run=dry_run)
        else:
            # mark as skipped if not already present
            upsert_record(conn, current_url, status="skipped", last_checked=datetime.utcnow().isoformat())
        return  # nothing further to crawl for a direct file link

    try:
        resp = SESSION.get(current_url, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        print(f"[WARNING] Could not fetch page {current_url}: {e}")
        return

    # Collect downloadable links on this page
    download_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        absolute = urljoin(current_url, href)
        parsed = urlparse(absolute)
        clean = parsed._replace(fragment="").geturl()
        if is_downloadable(clean):
            download_links.append(clean)

    # If this page has downloadable files, download them but continue crawling
    if download_links:
        unique_links = sorted(set(download_links))
        if dry_run:
            print(f"[FOUND {len(unique_links)} downloadable link(s) on this page] (dry-run) - will NOT download content")
        else:
            print(f"[FOUND {len(unique_links)} downloadable link(s) on this page. Processing...]")
        for dl in unique_links:
            if dl in VISITED_URLS:
                continue
            print(f"[FILE] {dl}")
            should, reason = should_download(conn, dl, force=force)
            print(f"  [DECISION] should_download={should} ({reason})")
            if should:
                download_file(conn, dl, DOWNLOAD_DIR, dry_run=dry_run)
            else:
                upsert_record(conn, dl, status="skipped", last_checked=datetime.utcnow().isoformat())
            VISITED_URLS.add(dl)

    # Continue crawling same-domain links
    for a in soup.find_all("a", href=True):
        href = a["href"]
        absolute = urljoin(current_url, href)
        parsed = urlparse(absolute)
        clean = parsed._replace(fragment="").geturl()

        if parsed.netloc == base_domain and clean not in VISITED_URLS:
            time.sleep(0.5)
            crawl_and_download(clean, base_domain, conn, dry_run=dry_run, force=force)

def print_status(conn, limit=20):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM files")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM files WHERE status='downloaded'")
    downloaded = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM files WHERE status='failed'")
    failed = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM files WHERE status='skipped'")
    skipped = cur.fetchone()[0]
    print("--- OMD DB STATUS ---")
    print(f"Total tracked: {total}")
    print(f"Downloaded: {downloaded}")
    print(f"Failed: {failed}")
    print(f"Skipped: {skipped}")
    print()
    print(f"Last {limit} records (most recent checks):")
    cur.execute("SELECT url, filename, status, last_checked, last_downloaded FROM files ORDER BY last_checked DESC LIMIT ?", (limit,))
    for row in cur.fetchall():
        print(f"- {row[1] or '<no-filename>'} -- {row[2]} -- checked={row[3]} last_downloaded={row[4]} -- {row[0]}")

def main():
    parser = argparse.ArgumentParser(description="OMD - crawler with persistent state")
    parser.add_argument("--dry-run", action="store_true", help="Find and report downloadable links but do not save files")
    parser.add_argument("--force", action="store_true", help="Force re-download of files even if previously downloaded")
    parser.add_argument("--status", action="store_true", help="Print DB status and exit")
    args = parser.parse_args()

    if START_URL == "http://example.com":
        print("!!! WARNING: set START_URL in .env or environment before running.")
        return

    base = urlparse(START_URL).netloc
    if not base:
        print(f"[ERROR] Could not parse domain from START_URL: {START_URL}")
        return

    conn = init_db(DB_PATH)

    if args.status:
        print_status(conn)
        return

    print("--- Starting Web Crawler ---")
    print(f"Target Domain: {base}")
    print(f"Downloadable Extensions: {', '.join(DOWNLOAD_EXTENSIONS)}")
    crawl_and_download(START_URL, base, conn, dry_run=args.dry_run, force=args.force)
    print("-" * 50)
    print("--- Finished ---")
    print(f"Total Unique URLs processed: {len(VISITED_URLS)}")
    print(f"Downloaded files are in: {DOWNLOAD_DIR}" if not args.dry_run else "No files were downloaded (dry-run).")

if __name__ == "__main__":
    main()
