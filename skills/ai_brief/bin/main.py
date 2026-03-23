#!/usr/bin/env python3
import json
import re
import time
import hashlib
import sys
import subprocess
import argparse
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

try:
    from curl_cffi import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Dependencies not found. Please run bin/install.sh first.", file=sys.stderr)
    sys.exit(1)

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = BASE_DIR / "state"
SETTINGS_FILE = STATE_DIR / "ai_settings.json"

DEFAULT_SETTINGS = {
  "version": 1,
  "timezone": "Asia/Kolkata",
  "daily_schedule": "0 22 * * *",
  "weekly_schedule": "0 22 * * 0",
  "daily_item_limit": 7,
  "memory_days": 14,
  "weekly_repo_limit": 10,
  "sources": {
    "hackernews": True,
    "arxiv": True,
    "github_trending": True,
    "youtube_watchlist": True,
    "x_twitter": False
  },
  "arxiv_categories": ["cs.AI", "cs.CL", "cs.LG"],
  "github_keywords": ["llm", "agent", "rag", "eval", "inference", "serving", "open-source-ai"],
  "youtube_channels": ["LLMs for Devs", "Full Stack AI LAB"]
}

OUT = {
    "version": 1,
    "generated_at": None,
    "items": []
}

def setup_state():
    """Idempotent setup for the state directory and default files."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    
    def create_if_missing(file_path: Path, content: str):
        if not file_path.exists():
            file_path.write_text(content)

    create_if_missing(STATE_DIR / "ai_news_memory.json", json.dumps({"version": 1, "items": []}, indent=2))
    create_if_missing(STATE_DIR / "ai_bookmarks.json", json.dumps({"version": 1, "items": []}, indent=2))
    create_if_missing(STATE_DIR / "latest_candidates.json", json.dumps({"version": 1, "generated_at": None, "items": []}, indent=2))
    create_if_missing(STATE_DIR / "latest_brief.md", "# AI Brief\n\nNo brief generated yet.\n")

    if not SETTINGS_FILE.exists():
        SETTINGS_FILE.write_text(json.dumps(DEFAULT_SETTINGS, indent=2))
    print("ai_brief setup complete. State directory is ready.")

def get_settings():
    if not SETTINGS_FILE.exists():
        return DEFAULT_SETTINGS
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except Exception as e:
        print(f"Error reading settings: {e}", file=sys.stderr)
        return DEFAULT_SETTINGS

def add_item(source, title, url, summary="", extra=None):
    title = (title or "").strip()
    url = (url or "").strip()
    if not title or not url:
        return
    item_id = hashlib.sha256(f"{source}|{title}|{url}".encode()).hexdigest()[:16]
    OUT["items"].append({
        "id": item_id,
        "source": source,
        "title": title,
        "url": url,
        "summary": (summary or "").strip(),
        "extra": extra or {}
    })

def fetch_hn(settings):
    if not settings["sources"].get("hackernews", True):
        return
    try:
        r = requests.get("https://hacker-news.firebaseio.com/v0/topstories.json", impersonate="chrome110", timeout=20)
        ids = r.json()[:40]
        for story_id in ids:
            try:
                ir = requests.get(f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json", impersonate="chrome110", timeout=10)
                item = ir.json()
                if not item: continue
                title = item.get("title", "")
                url = item.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
                score = item.get("score", 0)
                comments = item.get("descendants", 0)
                add_item("hackernews", title, url, extra={"score": score, "comments": comments})
                time.sleep(0.05)
            except Exception as e:
                print(f"HN Item Error {story_id}: {e}", file=sys.stderr)
                continue
    except Exception as e:
         print(f"HN Fetch Error: {e}", file=sys.stderr)
         raise

def fetch_arxiv(settings):
    if not settings["sources"].get("arxiv", True):
        return
    categories = settings.get("arxiv_categories", ["cs.AI", "cs.CL", "cs.LG"])
    for cat in categories:
        try:
            feed_url = f"https://export.arxiv.org/api/query?search_query=cat:{urllib.parse.quote(cat)}&start=0&max_results=15&sortBy=submittedDate&sortOrder=descending"
            r = requests.get(feed_url, impersonate="chrome110", timeout=20)
            root = ET.fromstring(r.content)
            ns = {"a": "http://www.w3.org/2005/Atom"}
            for entry in root.findall("a:entry", ns):
                title = entry.findtext("a:title", default="", namespaces=ns)
                url = entry.findtext("a:id", default="", namespaces=ns)
                summary = entry.findtext("a:summary", default="", namespaces=ns)
                add_item("arxiv", re.sub(r"\s+", " ", title), url, re.sub(r"\s+", " ", summary))
        except Exception as e:
            print(f"Arxiv Fetch Error for {cat}: {e}", file=sys.stderr)
            continue

def fetch_github_trending(settings):
    if not settings["sources"].get("github_trending", True):
        return
    try:
        r = requests.get("https://github.com/trending", impersonate="chrome110", timeout=20)
        soup = BeautifulSoup(r.text, 'html.parser')
        articles = soup.find_all('article', class_='Box-row')
        for article in articles[:25]:
            h2 = article.find('h2', class_='h3 lh-condensed')
            if not h2: continue
            a_tag = h2.find('a')
            if not a_tag: continue
            
            repo = a_tag.get('href', '').strip("/")
            url = f"https://github.com/{repo}"
            
            p_tag = article.find('p', class_='col-9 color-fg-muted my-1 pr-4')
            desc = p_tag.text.strip() if p_tag else ""
            
            add_item("github", repo, url, re.sub(r"\s+", " ", desc))
    except Exception as e:
        print(f"GitHub Trending Fetch Error: {e}", file=sys.stderr)
        raise

def run_fetch(settings):
    OUT["generated_at"] = datetime.now(timezone.utc).isoformat()
    fetch_hn(settings)
    fetch_arxiv(settings)
    fetch_github_trending(settings)
    (STATE_DIR / "latest_candidates.json").write_text(json.dumps(OUT, indent=2))
    print(f"Scraped {len(OUT['items'])} items.")

def trigger_openclaw(is_weekly=False):
    prompt_type = "weekly AI digest" if is_weekly else "daily AI brief"
    
    prompt = f"""Use the ai_brief skill.

Read these files from the ai_brief state directory:
- {STATE_DIR}/ai_settings.json
- {STATE_DIR}/ai_news_memory.json
- {STATE_DIR}/ai_bookmarks.json
- {STATE_DIR}/latest_candidates.json

Tasks for the {prompt_type}:
1. Load settings.
2. Review candidates and suppress unchanged duplicates.
3. Keep meaningful follow-up developments as updates.
4. Produce markdown in the ai_brief format.
5. Write the final markdown to {STATE_DIR}/latest_brief.md
6. Update ai_news_memory.json with accepted items only.
7. Report only the final markdown brief to me.
"""
    subprocess.run(["openclaw", "chat", "--message", prompt], check=True)

def notify_error(error_msg):
    """Fallback handler to explicitly notify the user via OpenClaw if the scraper breaks."""
    prompt = f"""The ai_brief skill encountered a critical error during execution.

Error details:
{error_msg}

Please notify me on Telegram about this failure, summarize what broke, and tell me I can ask you to "Go ahead and fix it"."""
    try:
        subprocess.run(["openclaw", "chat", "--message", prompt])
    except Exception as e:
        print(f"Failed to even send error notification via OpenClaw: {e}", file=sys.stderr)

def main():
    parser = argparse.ArgumentParser(description="AI Brief Orchestrator")
    parser.add_argument("--setup", action="store_true", help="Initialize state directory securely")
    parser.add_argument("--daily", action="store_true", help="Run the daily briefing")
    parser.add_argument("--weekly", action="store_true", help="Run the weekly briefing")
    args = parser.parse_args()

    if args.setup:
        setup_state()
        return

    setup_state()
    settings = get_settings()

    try:
        if args.daily or args.weekly:
            run_fetch(settings)
            trigger_openclaw(is_weekly=args.weekly)
        else:
            parser.print_help()
    except Exception as e:
        import traceback
        err_msg = traceback.format_exc()
        print(f"Critical error occurred:\n{err_msg}", file=sys.stderr)
        notify_error(err_msg)
        sys.exit(1)

if __name__ == "__main__":
    main()
