#!/usr/bin/env python3
"""Batch upload MP3 audiobook files to LingQ, with optional Whisper transcription.

Each MP3 becomes a private lesson in the specified course.  With --transcribe,
OpenAI Whisper runs locally on each file first, producing a real text transcript
that is uploaded alongside the audio — giving LingQ full word-level content.

Without --transcribe the lesson title is used as placeholder text, and you can
trigger LingQ's built-in transcript generation manually from the web interface.

Short-track merging (--min-duration, default 10 s):
  LingQ rejects audio lessons shorter than 10 seconds.  Any MP3 below the
  threshold is automatically merged with the following file(s) until the combined
  duration meets the minimum.  The lesson title reflects all merged tracks, e.g.
  "My Book 05, 06, 07".  Trailing short tracks at the end of the directory are
  appended to the last completed group.  Set --min-duration 0 to disable.

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

  # Disable short-track merging
  python lingq_audio_import.py --dir ./audio --prefix "My Book" \\
      --language es --collection 2612735 --min-duration 0

Whisper model sizes (--whisper-model):
  tiny   — fastest, lowest accuracy  (~75 MB)
  base   — good balance for quick tests (~150 MB)
  small  — solid quality (~500 MB)
  medium — recommended for Spanish audiobooks (~1.5 GB)  ← default
  large  — highest accuracy, slowest (~3 GB)

First-time setup:
  pip install openai-whisper mutagen
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
import subprocess
import sys
import tempfile
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


def get_mp3_duration(path: Path) -> float:
    """Return the duration of an MP3 file in seconds using mutagen."""
    try:
        from mutagen.mp3 import MP3  # noqa: PLC0415
        return MP3(str(path)).info.length
    except Exception:
        # If mutagen fails, fall back to 0 (treated as needing merge)
        return 0.0


def group_tracks(
    mp3_files: list[Path],
    start_track: int,
    min_seconds: float,
) -> list[list[tuple[int, Path, float]]]:
    """Group MP3 files so every group meets the minimum duration.

    Each entry in the returned list is a group: a list of
    ``(track_number, path, duration)`` tuples.

    Short files accumulate into ``pending`` until the running total reaches
    ``min_seconds``, at which point the group is committed.  Any remaining
    pending files at the end (trailing short tracks) are appended to the last
    committed group, or kept as a lone group if no committed group exists yet.

    When ``min_seconds`` is 0, every file becomes its own single-element group.
    """
    if min_seconds <= 0:
        return [
            [(start_track + i, path, get_mp3_duration(path))]
            for i, path in enumerate(mp3_files)
        ]

    groups: list[list[tuple[int, Path, float]]] = []
    pending: list[tuple[int, Path, float]] = []
    pending_dur = 0.0

    for i, path in enumerate(mp3_files):
        dur = get_mp3_duration(path)
        pending.append((start_track + i, path, dur))
        pending_dur += dur
        if pending_dur >= min_seconds:
            groups.append(pending)
            pending = []
            pending_dur = 0.0

    # Handle trailing short files
    if pending:
        if groups:
            groups[-1].extend(pending)
        else:
            # All files combined still don't meet the threshold — upload anyway
            groups.append(pending)

    return groups


def make_title(prefix: str, group: list[tuple[int, Path, float]], pad: int) -> str:
    """Build a lesson title from a group of tracks.

    Single track  → "Prefix 05"
    Multiple tracks → "Prefix 05, 06, 07"
    """
    nums = [f"{track_num:0{pad}d}" for track_num, _, _ in group]
    return f"{prefix} {', '.join(nums)}"


def merge_mp3_files_ffmpeg(paths: list[Path], output_path: Path) -> None:
    """Concatenate MP3s using ffmpeg's concat demuxer (clean re-mux)."""
    fd, list_file = tempfile.mkstemp(suffix=".txt")
    try:
        with os.fdopen(fd, "w") as fh:
            for p in paths:
                fh.write(f"file '{p.resolve()}'\n")
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", list_file,
                "-c", "copy",
                str(output_path),
            ],
            check=True,
            capture_output=True,
        )
    finally:
        try:
            os.unlink(list_file)
        except OSError:
            pass


def merge_mp3_files_binary(paths: list[Path], output_path: Path) -> None:
    """Concatenate MP3s by raw byte concatenation (fallback if ffmpeg absent).

    Works reliably for CBR files; VBR join points may show minor glitches.
    """
    with output_path.open("wb") as out:
        for p in paths:
            out.write(p.read_bytes())


def merge_mp3_files(paths: list[Path], output_path: Path) -> str:
    """Merge *paths* into *output_path*, returning the method used."""
    try:
        merge_mp3_files_ffmpeg(paths, output_path)
        return "ffmpeg"
    except (FileNotFoundError, subprocess.CalledProcessError):
        merge_mp3_files_binary(paths, output_path)
        return "binary"


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
        "--min-duration",
        type=float,
        default=10.0,
        metavar="SECS",
        help="Minimum lesson duration in seconds (default: 10). "
             "Files shorter than this are merged with the next file(s). "
             "Set to 0 to disable merging.",
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

    total_files = len(mp3_files)
    # Zero-pad based on the highest track number across all source files
    pad = 3 if (args.start_track + total_files - 1) > 99 else 2

    groups = group_tracks(mp3_files, args.start_track, args.min_duration)
    total_groups = len(groups)

    if args.dry_run:
        merge_note = (
            f"  →  {total_groups} lesson(s) after merging"
            if total_groups < total_files
            else ""
        )
        print(f"Dry run — {total_files} file(s) found in {mp3_dir}{merge_note}\n")
        for g_idx, group in enumerate(groups):
            title = make_title(args.prefix, group, pad)
            total_dur = sum(d for _, _, d in group)
            if len(group) == 1:
                _, path, dur = group[0]
                size_mb = path.stat().st_size / (1024 * 1024)
                trans_note = "  (will transcribe)" if args.transcribe else ""
                print(f"  [{g_idx + 1}/{total_groups}] {path.name}  "
                      f"({dur:.1f}s, {size_mb:.1f} MB)  →  '{title}'{trans_note}")
            else:
                file_parts = " + ".join(p.name for _, p, _ in group)
                trans_note = "  (will transcribe each)" if args.transcribe else ""
                print(f"  [{g_idx + 1}/{total_groups}] {file_parts}  "
                      f"[{total_dur:.1f}s merged]  →  '{title}'{trans_note}")
        return 0

    if not args.api_key:
        print(
            "ERROR: no API key found.\n"
            "  Pass --api-key or set the LINGQ_API_KEY environment variable.",
            file=sys.stderr,
        )
        return 1

    mode = "with Whisper transcription" if args.transcribe else "audio-only (title as placeholder text)"
    merge_info = (
        f", merging tracks < {args.min_duration:.0f}s"
        if args.min_duration > 0 and total_groups < total_files
        else ""
    )
    print(f"Uploading {total_files} file(s) as {total_groups} lesson(s) "
          f"to LingQ [{args.language}] — {mode}{merge_info}\n")
    if args.transcribe:
        print(f"  Whisper model: {args.whisper_model}  (downloading on first run if needed)\n")

    failures = 0
    for g_idx, group in enumerate(groups):
        title = make_title(args.prefix, group, pad)
        total_dur = sum(d for _, _, d in group)
        prefix_str = f"[{g_idx + 1}/{total_groups}]"

        if len(group) == 1:
            _, mp3_path, _ = group[0]
            print(f"  {prefix_str} {mp3_path.name}  →  '{title}'")
            merge_method = None
        else:
            file_parts = " + ".join(p.name for _, p, _ in group)
            print(f"  {prefix_str} {file_parts}  [{total_dur:.1f}s]  →  '{title}'")
            tmp_path = Path(tempfile.mktemp(suffix=".mp3"))
            print(f"    Merging… ", end="", flush=True)
            try:
                merge_method = merge_mp3_files([p for _, p, _ in group], tmp_path)
                print(f"OK ({merge_method})")
            except Exception as exc:
                print(f"FAILED\n    {exc}")
                failures += 1
                continue
            mp3_path = tmp_path

        try:
            # ── Transcription ────────────────────────────────────────────── #
            if args.transcribe:
                texts = []
                paths_to_transcribe = [p for _, p, _ in group]
                for t_path in paths_to_transcribe:
                    label = t_path.name if len(group) > 1 else ""
                    suffix = f" ({label})" if label else ""
                    print(f"    Transcribing{suffix} with Whisper ({args.whisper_model})… ",
                          end="", flush=True)
                    try:
                        t = transcribe_with_whisper(t_path, args.language, args.whisper_model)
                        word_count = len(t.split())
                        print(f"{word_count} words")
                        texts.append(t)
                    except Exception as exc:
                        print(f"FAILED\n    {exc}")
                        failures += 1
                        break
                else:
                    text = "\n\n".join(texts)
                    # Fall through to upload
                if len(texts) < len(paths_to_transcribe):
                    continue  # transcription failed for one of the parts
            else:
                # Placeholder — LingQ rejects a blank text field
                text = title

            # ── Upload ───────────────────────────────────────────────────── #
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

        finally:
            # Clean up temp merge file if one was created
            if merge_method is not None and mp3_path.exists():
                try:
                    mp3_path.unlink()
                except OSError:
                    pass

    uploaded = total_groups - failures
    print(f"\nDone: {uploaded}/{total_groups} lesson(s) uploaded", end="")
    if failures:
        print(f", {failures} failed")
    else:
        print()

    return failures


if __name__ == "__main__":
    raise SystemExit(main())
