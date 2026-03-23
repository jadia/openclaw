---
name: ai_brief
description: Daily or weekly AI briefing focused on frontier models, developer tools, open-source repos, and practical research updates. Now powered by a robust Python orchestrator.
---

# AI Brief

Use this skill when the user wants:
- a daily AI news briefing
- a weekly AI roundup
- to bookmark briefing items
- to list, research, or clear bookmarks

## Execution / Orchestration
This skill relies on a unified Python orchestrator located at `bin/main.py`.
Dependencies must be installed via `bin/install.sh` first, which creates a local `.venv/`.

**CLI Routes to invoke the script via openclaw/shell:**
- Idempotent Initial Setup: `.venv/bin/python bin/main.py --setup`
- Daily Fetch & Generation: `.venv/bin/python bin/main.py --daily`
- Weekly Fetch & Generation: `.venv/bin/python bin/main.py --weekly`

## Scope
Prioritize:
- LLMs and frontier models
- developer tools, SDKs, APIs, runtimes, evals, inference, RAG, agents
- open-source AI repos and frameworks
- practical papers with likely developer impact
- major launches, releases, rollouts, benchmarks, integrations, pricing changes

Deprioritize:
- generic business news with no developer angle
- opinion-only posts
- repeated unchanged stories

## State
All persistent files live under `state/`.
If they do not exist, run `.venv/bin/python bin/main.py --setup`.
- `ai_settings.json`
- `ai_news_memory.json`
- `ai_bookmarks.json`
- `latest_candidates.json`
- `latest_brief.md`

## Deduplication and updates
Use a 14-day memory window unless overridden in settings.

Classify every candidate item as one of:
- `new`
- `update`
- `duplicate`

Mark as `duplicate` when:
- same canonical URL already exists in memory
- same repo/model/company event with no material new fact
- repost, mirror, or discussion echo of an already-covered item

Mark as `update` when an existing story gets a material new fact, such as:
- new release or version
- API access
- bench mark result
- integration

## Output rules
Return markdown only. Extremely clean and optimized for Telegram reading.

Daily output shape:

# AI Brief — YYYY-MM-DD

## Fresh developments
1. **Headline**
Two short lines.
Why it matters: one short developer-focused line.
Source: <link>
Read more: <optional second link>

## Ongoing updates
1. **Update: Headline**
Two short lines.
Why it matters: one short line.
Source: <link>

## Tools and repos
1. **Headline**
Two short lines.

## Frontier models and papers
1. **Headline**
Two short lines.

## Bookmarks status
- N items saved.

## Bookmark commands
Support these actions natively in OpenClaw chats:
- bookmark item <n>
- list bookmarks
- clear bookmarks
- research all bookmarks

Bookmark object shape:
{
  "title": "string",
  "url": "string",
  "section": "string",
  "date_bookmarked": "ISO-8601"
}
