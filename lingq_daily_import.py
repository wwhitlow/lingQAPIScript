#!/usr/bin/env python3
"""Fetch reading content from a webpage and import it into LingQ.

Usage examples:
  python lingq_daily_import.py --url "https://example.com/article"
  python lingq_daily_import.py --url "https://example.com/article" --title "My Daily Read" --upload
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; LingQDailyImporter/1.0; +https://lingq.com/)"


@dataclass
class ExtractionResult:
    title: str
    text: str


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "lesson"


def fetch_html(url: str, timeout: int = 25) -> str:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    response.raise_for_status()
    return response.text


def clean_text(raw: str) -> str:
    text = re.sub(r"\r", "", raw)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def score_node_text(text: str) -> int:
    paragraphs = text.count("\n") + 1
    word_count = len(re.findall(r"\w+", text))
    return word_count + (paragraphs * 4)


def extract_content(html: str, fallback_title: str) -> ExtractionResult:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
        tag.decompose()

    title = fallback_title
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    candidates = []
    for selector in ["article", "main", "section"]:
        for node in soup.select(selector):
            text = clean_text(node.get_text("\n"))
            if len(text) > 400:
                candidates.append((score_node_text(text), text))

    if not candidates:
        body = soup.body or soup
        text = clean_text(body.get_text("\n"))
        return ExtractionResult(title=title, text=text)

    best_text = max(candidates, key=lambda item: item[0])[1]
    return ExtractionResult(title=title, text=best_text)


def build_lingq_payload(title: str, text: str, collection: int | None) -> dict:
    payload = {
        "title": title,
        "text": text,
        "share_status": "private",
    }
    if collection is not None:
        payload["collection"] = collection
    return payload


def lingq_import_url(language: str) -> str:
    return f"https://www.lingq.com/api/v3/{language}/lessons/"


def upload_to_lingq(payload: dict, language: str, api_key: str, timeout: int = 35) -> dict:
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json",
    }
    response = requests.post(
        lingq_import_url(language),
        headers=headers,
        data=json.dumps(payload),
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json() if response.text.strip() else {"ok": True}


def derive_default_title(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.replace("www.", "")
    path = parsed.path.strip("/").split("/")[-1]
    path_part = path.replace("-", " ").replace("_", " ").strip()
    date_part = dt.datetime.now().strftime("%Y-%m-%d")
    if path_part:
        return f"{path_part.title()} ({host}) {date_part}"
    return f"Daily Reading ({host}) {date_part}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch webpage content and prepare/import LingQ lesson")
    parser.add_argument("--url", required=True, help="Source article URL")
    parser.add_argument("--title", help="Override lesson title")
    parser.add_argument("--language", default=os.getenv("LINGQ_LANGUAGE", "en"), help="LingQ language code (default: en)")
    parser.add_argument("--collection", type=int, default=os.getenv("LINGQ_COLLECTION_ID"), help="LingQ course/collection id")
    parser.add_argument("--upload", action="store_true", help="Upload directly to LingQ API")
    parser.add_argument("--out-dir", default="./imports", help="Output folder for text + payload artifacts")
    parser.add_argument("--min-words", type=int, default=120, help="Fail if extracted text has fewer words")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_key = os.getenv("LINGQ_API_KEY")

    title_seed = args.title or derive_default_title(args.url)

    try:
        html = fetch_html(args.url)
        extracted = extract_content(html, fallback_title=title_seed)
    except requests.RequestException as exc:
        print(f"ERROR: Unable to fetch page: {exc}", file=sys.stderr)
        return 1

    title = args.title or extracted.title or title_seed
    text = clean_text(extracted.text)
    word_count = len(re.findall(r"\w+", text))

    if word_count < args.min_words:
        print(
            f"ERROR: extracted only {word_count} words (min required: {args.min_words}).",
            file=sys.stderr,
        )
        return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = dt.datetime.now().strftime("%Y%m%d")
    base_name = f"{stamp}-{slugify(title)[:80]}"

    text_path = out_dir / f"{base_name}.txt"
    payload_path = out_dir / f"{base_name}.payload.json"

    payload = build_lingq_payload(title=title, text=text, collection=args.collection)

    text_path.write_text(text + "\n", encoding="utf-8")
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Prepared lesson text: {text_path}")
    print(f"Prepared LingQ payload: {payload_path}")
    print(f"Extracted words: {word_count}")

    if not args.upload:
        print("Upload skipped. Use --upload after setting LINGQ_API_KEY.")
        return 0

    if not api_key:
        print("ERROR: --upload requested but LINGQ_API_KEY is not set.", file=sys.stderr)
        return 1

    try:
        result = upload_to_lingq(payload=payload, language=args.language, api_key=api_key)
    except requests.HTTPError as exc:
        body = exc.response.text if exc.response is not None else ""
        print(f"ERROR: LingQ upload failed: {exc}\n{body}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"ERROR: LingQ upload failed: {exc}", file=sys.stderr)
        return 1

    lesson_id = result.get("id") if isinstance(result, dict) else None
    if lesson_id is not None:
        print(f"LingQ upload complete. Lesson ID: {lesson_id}")
    else:
        print(f"LingQ upload complete. Response: {json.dumps(result)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
