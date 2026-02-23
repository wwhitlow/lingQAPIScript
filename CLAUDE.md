# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

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
