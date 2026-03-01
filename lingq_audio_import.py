#!/usr/bin/env python3
"""Batch upload MP3 audiobook files to LingQ, with optional Whisper transcription.

Each MP3 becomes a private lesson in the specified course.  With --transcribe,
OpenAI Whisper runs locally on each file first, producing a real text transcript
that is uploaded alongside the audio — giving LingQ full word-level content.

Without --transcribe the lesson title is used as placeholder text, and you can
trigger LingQ's built-in transcript generation manually from the web interface.

Usage examples:
  # Preview files and titles (no upload, no transcription)
  python lingq_audio_import.py --dir ./audio --prefix "My Book" --dry-run

  # Upload with Whisper transcription (recommended)
  python lingq_audio_import.py --dir ./audio --prefix "My Book" \\
      --language es --collection 2612735 --transcribe

  # Upload without transcription (audio + title placeholder)
  python lingq_audio_import.py --dir ./audio --prefix "My Book" \\
      --language es --collection 2612735

  # Resume a partial upload starting at track 6
  python lingq_audio_import.py --dir ./audio --prefix "My Book" \\
      --language es --collection 2612735 --transcribe --start-track 6

Whisper model sizes (--whisper-model):
  tiny   — fastest, lowest accuracy  (~75 MB)
  base   — good balance for quick tests (~150 MB)
  small  — solid quality (~500 MB)
  medium — recommended for Spanish audiobooks (~1.5 GB)  ← default
  large  — highest accuracy, slowest (~3 GB)

First-time setup:
  pip install openai-whisper
  brew install ffmpeg          # macOS
  # or: sudo apt install ffmpeg  (Debian/Ubuntu)

API reference:   https://www.lingq.com/apidocs/
Developer forum: https://forum.lingq.com/t/python-uploading-audio-via-api/64977

Environment variable fallbacks (used when CLI flags are omitted):
  LINGQ_API_KEY        API token
  LINGQ_LANGUAGE       Language code (default: es)
  LINGQ_COLLECTION_ID  Collection / course ID
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import requests

_LINGQ_LESSONS_URL = "https://www.lingq.com/api/v3/{language}/lessons/"


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #

def natural_sort_key(p: Path) -> list:
    """Split filename on digit runs for human-friendly numeric ordering.

    E.g. "track2.mp3" sorts before "track10.mp3" (unlike plain string sort).
    """
    parts = re.split(r"(\d+)", p.name.lower())
    return [int(x) if x.isdigit() else x for x in parts]


def transcribe_with_whisper(mp3_path: Path, language: str, model_name: str) -> str:
    """Transcribe *mp3_path* using a local Whisper model and return the text.

    The model is loaded once per process (cached by whisper internally).
    *language* should be the ISO 639-1 code (e.g. ``"es"`` for Spanish);
    passing it explicitly skips Whisper's auto-detection step and is faster.

    Whisper language codes use the full name or the two-letter ISO code —
    both are accepted by the library.
    """
    try:
        import whisper  # noqa: PLC0415
    except ImportError:
        print(
            "ERROR: openai-whisper is not installed.\n"
            "  pip install openai-whisper\n"
            "  brew install ffmpeg   # also required",
            file=sys.stderr,
        )
        sys.exit(1)

    model = whisper.load_model(model_name)
    result = model.transcribe(str(mp3_path), language=language, verbose=False)
    return result["text"].strip()


def upload_audio_lesson(
    mp3_path: Path,
    title: str,
    text: str,
    language: str,
    api_key: str,
    collection: int | None,
    timeout: int,
) -> dict:
    """Upload a single MP3 as a new private LingQ lesson.

    API reference: https://www.lingq.com/apidocs/
    Developer forum (audio upload): https://forum.lingq.com/t/python-uploading-audio-via-api/64977
    """
    url = _LINGQ_LESSONS_URL.format(language=language)
    headers = {"Authorization": f"Token {api_key}"}
    data: dict[str, str] = {
        "title": title,
        "text": text,
        "share_status": "private",
    }
    if collection is not None:
        data["collection"] = str(collection)

    with mp3_path.open("rb") as fh:
        files = {"audio": (mp3_path.name, fh, "audio/mpeg")}
        response = requests.post(
            url, headers=headers, data=data, files=files, timeout=timeout
        )
    response.raise_for_status()
    return response.json() if response.text.strip() else {"ok": True}


# --------------------------------------------------------------------------- #
# CLI                                                                           #
# --------------------------------------------------------------------------- #

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Batch upload MP3 audiobook files to LingQ.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python lingq_audio_import.py --dir ./audio --prefix 'My Book' --dry-run\n"
            "  python lingq_audio_import.py --dir ./audio --prefix 'My Book' "
            "--language es --transcribe\n"
            "  python lingq_audio_import.py --dir ./audio --prefix 'My Book' "
            "--language es --collection 12345 --transcribe\n"
        ),
    )
    p.add_argument(
        "--dir",
        required=True,
        metavar="PATH",
        help="Directory containing MP3 files",
    )
    p.add_argument(
        "--prefix",
        required=True,
        metavar="TEXT",
        help='Lesson title prefix, e.g. "My Book" → "My Book 01", "My Book 02" …',
    )
    p.add_argument(
        "--language",
        default=os.getenv("LINGQ_LANGUAGE", "es"),
        metavar="CODE",
        help="LingQ language code and Whisper language hint (default: $LINGQ_LANGUAGE or 'es')",
    )
    p.add_argument(
        "--collection",
        type=int,
        default=(
            int(os.getenv("LINGQ_COLLECTION_ID"))
            if os.getenv("LINGQ_COLLECTION_ID")
            else None
        ),
        metavar="ID",
        help="LingQ course/collection ID (default: $LINGQ_COLLECTION_ID)",
    )
    p.add_argument(
        "--api-key",
        default=os.getenv("LINGQ_API_KEY"),
        metavar="TOKEN",
        help="LingQ API token (default: $LINGQ_API_KEY)",
    )
    p.add_argument(
        "--start-track",
        type=int,
        default=1,
        metavar="N",
        help="Track number for the first file (default: 1). "
             "Useful when resuming a partial upload.",
    )
    p.add_argument(
        "--transcribe",
        action="store_true",
        help=(
            "Transcribe each MP3 with local Whisper before uploading. "
            "Requires: pip install openai-whisper && brew install ffmpeg"
        ),
    )
    p.add_argument(
        "--whisper-model",
        default="medium",
        metavar="SIZE",
        choices=["tiny", "base", "small", "medium", "large"],
        help="Whisper model size: tiny/base/small/medium/large (default: medium). "
             "Larger = more accurate but slower and uses more RAM.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="List files and generated titles without transcribing or uploading",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=120,
        metavar="SECS",
        help="Upload timeout per file in seconds (default: 120)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    mp3_dir = Path(args.dir)
    if not mp3_dir.is_dir():
        print(f"ERROR: directory not found: {mp3_dir}", file=sys.stderr)
        return 1

    mp3_files = sorted(mp3_dir.glob("*.mp3"), key=natural_sort_key)
    if not mp3_files:
        print(f"ERROR: no MP3 files found in {mp3_dir}", file=sys.stderr)
        return 1

    total = len(mp3_files)
    # Zero-pad to 2 digits; upgrade to 3 if the batch exceeds 99 tracks
    pad = 3 if (args.start_track + total - 1) > 99 else 2

    if args.dry_run:
        print(f"Dry run — {total} file(s) found in {mp3_dir}\n")
    else:
        if not args.api_key:
            print(
                "ERROR: no API key found.\n"
                "  Pass --api-key or set the LINGQ_API_KEY environment variable.",
                file=sys.stderr,
            )
            return 1
        mode = "with Whisper transcription" if args.transcribe else "audio-only (title as placeholder text)"
        print(f"Uploading {total} MP3 file(s) to LingQ [{args.language}] — {mode}\n")
        if args.transcribe:
            print(f"  Whisper model: {args.whisper_model}  (downloading on first run if needed)\n")

    failures = 0
    for i, mp3_path in enumerate(mp3_files):
        track_num = args.start_track + i
        title = f"{args.prefix} {track_num:0{pad}d}"
        prefix_str = f"[{i + 1}/{total}]"

        if args.dry_run:
            size_mb = mp3_path.stat().st_size / (1024 * 1024)
            trans_note = "  (will transcribe with Whisper)" if args.transcribe else ""
            print(f"  {prefix_str} {mp3_path.name}  ({size_mb:.1f} MB)  →  '{title}'{trans_note}")
            continue

        print(f"  {prefix_str} {mp3_path.name}  →  '{title}'")

        # ── Transcription ──────────────────────────────────────────────────── #
        if args.transcribe:
            print(f"    Transcribing with Whisper ({args.whisper_model})… ", end="", flush=True)
            try:
                text = transcribe_with_whisper(mp3_path, args.language, args.whisper_model)
                word_count = len(text.split())
                print(f"{word_count} words")
            except Exception as exc:
                print(f"FAILED\n    {exc}")
                failures += 1
                continue
        else:
            # Placeholder — LingQ rejects a blank text field
            text = title

        # ── Upload ─────────────────────────────────────────────────────────── #
        print(f"    Uploading… ", end="", flush=True)
        try:
            result = upload_audio_lesson(
                mp3_path=mp3_path,
                title=title,
                text=text,
                language=args.language,
                api_key=args.api_key,
                collection=args.collection,
                timeout=args.timeout,
            )
            lesson_id = result.get("id") if isinstance(result, dict) else None
            if lesson_id is not None:
                print(f"OK  (lesson ID: {lesson_id})")
            else:
                print("OK")
        except requests.HTTPError as exc:
            body = exc.response.text if exc.response is not None else ""
            print(f"FAILED\n    HTTP {exc.response.status_code}: {body[:200]}")
            failures += 1
        except requests.RequestException as exc:
            print(f"FAILED\n    {exc}")
            failures += 1

    if not args.dry_run:
        uploaded = total - failures
        print(f"\nDone: {uploaded}/{total} uploaded", end="")
        if failures:
            print(f", {failures} failed")
        else:
            print()

    return failures


if __name__ == "__main__":
    raise SystemExit(main())
