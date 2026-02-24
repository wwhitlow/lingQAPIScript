# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium   # one-time, required for lingq_interactive.py
```

## Web UI (recommended for non-technical users)

| Platform | Launcher | Notes |
|---|---|---|
| macOS | Double-click `start.command` in Finder | First run: right-click → Open if Gatekeeper blocks it |
| Windows | Double-click `start.bat` in File Explorer | Requires Python installed with "Add to PATH" checked |

```bash
# Or from the terminal (any platform):
python lingq_app.py
```

**First time on a new machine:** both launchers auto-create the venv and install all dependencies (including Playwright's Chromium). After that, double-clicking is all that's needed.

**Architecture** (`lingq_app.py`):
- Single-file Flask app with embedded HTML/CSS/JS (no separate template files)
- Serves on `127.0.0.1:5050` (auto-increments if busy)
- Background jobs run as subprocesses of `lingq_interactive.py`; output is streamed to the browser via polling (`GET /api/job/<id>?offset=N`)
- Config files (`lingq_*.json`) are read/written directly from the project directory

**API endpoints:** `GET /api/sites`, `GET|POST /api/site/<file>`, `DELETE /api/site/<file>`, `POST /api/launch`, `POST /api/run`, `GET /api/job/<id>`

---

## Running the script

Prepare output files only (no upload):
```bash
python lingq_daily_import.py --url "https://example.com/article"
```

Upload to LingQ (requires env vars `LINGQ_API_KEY`, `LINGQ_LANGUAGE`, optionally `LINGQ_COLLECTION_ID`):
```bash
python lingq_daily_import.py --url "https://example.com/article" --upload
```

## Architecture

This is a single-file script (`lingq_daily_import.py`) with no tests framework. The flow is:

1. **Fetch** — `fetch_html()` GETs the URL with an optional `Accept-Language` header. If `--source-lang` is given, `with_query_param()` injects `lang=<code>` into the URL before fetching.
2. **Extract** — `extract_content()` uses BeautifulSoup to strip boilerplate tags. If `--selector` is provided, it collects text from all matching nodes (concatenated with `\n\n`) and skips the heuristic. Otherwise, it scores candidate `<article>`, `<main>`, and `<section>` nodes by word count + paragraph count (`score_node_text()`), picking the highest-scoring block. Falls back to `<body>` if nothing qualifies. If selectors are given but match nothing, a warning is printed to stderr and the heuristic runs instead.
3. **Validate** — word count must meet `--min-words` (default 120) or the script exits with code 1.
4. **Write artifacts** — saves `./imports/YYYYMMDD-<slug>.txt` and `./imports/YYYYMMDD-<slug>.payload.json`.
5. **Upload** (optional) — `upload_to_lingq()` POSTs the JSON payload to `https://www.lingq.com/api/v3/<language>/lessons/` using a `Token` auth header.

## Environment variables

| Variable | CLI flag override | Purpose |
|---|---|---|
| `LINGQ_API_KEY` | — | LingQ API token (required for `--upload`) |
| `LINGQ_LANGUAGE` | `--language` | Target language code (default: `en`) |
| `LINGQ_COLLECTION_ID` | `--collection` | Course/collection ID |
| `SOURCE_LANG` | `--source-lang` | Forces a `lang=` query param on the fetch URL |
| `SOURCE_ACCEPT_LANGUAGE` | `--accept-language` | HTTP Accept-Language header value |

## Output

Artifacts are written to `./imports/` (overridable with `--out-dir`):
- `YYYYMMDD-<title-slug>.txt` — extracted plain text
- `YYYYMMDD-<title-slug>.payload.json` — LingQ API-ready JSON

## Targeting specific page content with selectors

Use `--selector` to pin extraction to exact parts of the page, bypassing the heuristic scorer:

```bash
# Single selector
python lingq_daily_import.py --url "https://example.com/article" --selector ".article-body"

# Multiple selectors — text is concatenated in the order selectors are given
python lingq_daily_import.py --url "https://example.com/article" \
  --selector ".reading-intro" \
  --selector ".reading-body"
```

Any valid CSS selector supported by BeautifulSoup/soupsieve works (class, id, attribute, pseudo-class, etc.). If none of the selectors match, the script warns to stderr and falls back to the heuristic.

## Interactive selector tool (`lingq_interactive.py`)

A visual alternative to writing selectors by hand. Opens a real Chromium browser, overlays a sidebar, and lets you point-and-click to build selectors. Saves a `lingq_config.json` that can then be used for headless/cron imports.

**First time on a new site:**
```bash
python lingq_interactive.py --url "https://example.com/article"
# Browser opens → hover to preview, click to select (green highlight)
# Fill API Key + Language in the sidebar
# Click "Save Config & Exit"
# Prompted: "Upload to LingQ now? [y/N]"
```

**Subsequent headless runs (cron-friendly):**
```bash
python lingq_interactive.py --headless
python lingq_interactive.py --headless --upload
python lingq_interactive.py --headless --config my_site.json --upload
```

**Config file schema** (`lingq_config.json`):
```json
{
  "url": "https://...",
  "selectors": [".article-body"],
  "api_key": "your-lingq-token",
  "language": "en",
  "title": null,
  "collection_id": null,
  "source_lang": null,
  "accept_language": null
}
```

`api_key` in the config takes precedence; falls back to `LINGQ_API_KEY` env var if blank.

**Key implementation details** (`lingq_interactive.py`):
- `_INJECTED_JS` — self-contained JS IIFE injected via `page.evaluate()` after DOM load; guarded by `window.__lingqSelectorActive` to prevent double-init
- `interactive_mode()` — launches Playwright Chromium headed, re-injects sidebar on every navigation, blocks on `page.wait_for_function("window.__lingqDone")` (no timeout)
- `headless_mode()` — imports functions directly from `lingq_daily_import` (same directory), runs the same fetch → extract → validate → write → upload pipeline
- Selector generator walks the DOM upward using `#id`, `.class`, or `:nth-child` disambiguation
