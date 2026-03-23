# ai_brief

Daily and weekly AI briefing skill for OpenClaw.

`ai_brief` creates a concise markdown summary focused on:
- Frontier models and LLMs
- Developer-facing AI tools
- Open-source AI repositories
- Practical papers and model releases
- Meaningful follow-up developments without repeating unchanged news

It is designed for people who want a clean AI briefing every day without noisy duplicates, delivered right to your OpenClaw interface or Telegram.

## Features

- **Consolidated Python Orchestrator:** Simple to run, robust fetching.
- **Advanced Scraping:** Uses `curl_cffi` to bypass scraping blocks and `beautifulsoup4` for HTML reliability.
- **Smart Error Notification:** If a source fails to fetch due to layout changes, the skill natively dispatches an OpenClaw alert to your Telegram so you can address it immediately.
- **Duplicate Suppression:** Update-aware story tracking prevents noise.
- **Config-driven:** Customize sources and logic through a single `ai_settings.json` file.

## Why this project exists

Most AI news feeds repeat headlines. `ai_brief` is opinionated:
- exact duplicates are suppressed
- meaningful story evolution is kept
- output is short and skimmable
- weekly reports highlight hot repos and rising topics

## Requirements

- OpenClaw installed and working
- Internet access for API fetching

## Installation

OpenClaw loads skills from `~/.openclaw/skills/` or `<workspace>/skills/`.
Assuming you place this folder at `~/.openclaw/skills/ai_brief`:

1. **Set up environment & dependencies:**
Run the install script, which installs the required Python dependencies in a local, isolated virtual environment (`.venv`):

```bash
 ~/.openclaw/skills/ai_brief/bin/install.sh
```

*(This automatically runs the initial state bootstrapping as well).*

## File structure

```text
ai_brief/
├── SKILL.md
├── README.md
├── requirements.txt          # lists curl_cffi and beautifulsoup4
├── bin/
│   ├── install.sh            # creates .venv and installs requirements
│   └── main.py              # the unified Orchestrator (scraping + OpenClaw trigger)
└── state/                    # auto-created during setup
    ├── ai_settings.json      
    ├── ai_news_memory.json   
    ├── ai_bookmarks.json     
    ├── latest_candidates.json
    └── latest_brief.md
```

## Configuration

Edit `state/ai_settings.json` to configure topics, sources, and schedules.

```json
{
  "version": 1,
  "sources": {
    "hackernews": true,
    "arxiv": true,
    "github_trending": true
  },
  "arxiv_categories": ["cs.AI", "cs.CL", "cs.LG"]
}
```

## Usage & Scheduling with OpenClaw

The Python file is the main driver. Use the isolated virtual environment Python to run it:

### Manual Testing

**Daily Run:**
```bash
 ~/.openclaw/skills/ai_brief/.venv/bin/python ~/.openclaw/skills/ai_brief/bin/main.py --daily
```

**Weekly Digest:**
```bash
 ~/.openclaw/skills/ai_brief/.venv/bin/python ~/.openclaw/skills/ai_brief/bin/main.py --weekly
```

### Scheduling with OpenClaw Cron

Use OpenClaw cron to automate exactly as above. (Make sure you use the `.venv/bin/python` binary.)

**Daily job:**
```bash
 openclaw cron add \
  --name "AI Brief Daily" \
  --cron "0 22 * * *" \
  --session isolated \
  --light-context \
  --no-deliver \
  --message "~/.openclaw/skills/ai_brief/.venv/bin/python ~/.openclaw/skills/ai_brief/bin/main.py --daily"
```

**Weekly job:**
```bash
 openclaw cron add \
  --name "AI Brief Weekly" \
  --cron "0 22 * * 0" \
  --session isolated \
  --light-context \
  --no-deliver \
  --message "~/.openclaw/skills/ai_brief/.venv/bin/python ~/.openclaw/skills/ai_brief/bin/main.py --weekly"
```

## 📱 Telegram Integration


Because `ai_brief` communicates natively through OpenClaw, you can orchestrate it seamlessly via Telegram!

**Example Telegram Commands:**

- **On-demand Briefs:**
  > *"Hey OpenClaw, generate my daily AI brief right now."* 
  > *(OpenClaw will run the `main.py --daily` script and report back).*
  > 
  > *"Can you fetch my weekly AI digest?"*
  
- **Deep Dives & Follow-ups:**
  > *"Tell me more about the first item in today's brief. Search the web if you need to."*
  > *"Summarize the github repo mentioned in the latest news."*

- **Bookmarks Management:**
  > *"Bookmark the second item from today's brief."*
  > *"Show me all my bookmarked AI news."*
  > *"Research all my bookmarks and write a comprehensive summary."*
  > *"Clear all my bookmarks."*

- **Smart Error Recovery:**
  If GitHub or HackerNews blocks the scraper, the python script catches the error and alerts you on Telegram:
  > **OpenClaw (via Telegram):** The ai_brief skill encountered a critical error during execution...
  
  You can simply reply:
  > *"Go ahead and fix the scraper for me."*

## Output format

Daily output is markdown and typically looks like this:

```md
# AI Brief — YYYY-MM-DD

## Fresh developments
1. **Headline**
Two short lines.
Why it matters: short developer-focused reason.
Source: <link>

## Ongoing updates
1. **Update: Headline**

## Tools and repos
...
```
