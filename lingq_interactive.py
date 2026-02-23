#!/usr/bin/env python3
"""Interactive LingQ content selector tool.

Opens a real browser so you can click on page regions to build CSS selectors,
enter your API key, and save a reusable config file.  The same config can
then drive a fully headless import (great for cron jobs).

Usage:
  # Visual selector — opens browser, saves lingq_config.json
  python lingq_interactive.py --url "https://example.com/article"

  # Re-open browser to edit an existing config
  python lingq_interactive.py --url "https://example.com/article" --config my_site.json

  # Headless import using a saved config
  python lingq_interactive.py --headless
  python lingq_interactive.py --headless --config my_site.json --upload
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# JavaScript injected into the page (self-contained IIFE)                      #
# --------------------------------------------------------------------------- #

_INJECTED_JS = r"""
(function () {
  // Guard: don't initialise twice on the same page
  if (window.__lingqSelectorActive) return;
  window.__lingqSelectorActive = true;
  window.__lingqDone  = false;
  window.__lingqConfig = null;

  /* ── Styles ──────────────────────────────────────────────────────────── */
  const css = document.createElement('style');
  css.textContent = `
    .__lh {
      outline: 2px dashed #f59e0b !important;
      background: rgba(251,191,36,.18) !important;
      cursor: crosshair !important;
    }
    .__ls {
      outline: 2px solid #10b981 !important;
      background: rgba(16,185,129,.14) !important;
    }
    #__lsb {
      all: initial;
      position: fixed !important;
      top: 0 !important; right: 0 !important;
      width: 300px !important; height: 100vh !important;
      background: #1e293b !important;
      color: #f1f5f9 !important;
      font: 13px/1.45 system-ui,sans-serif !important;
      z-index: 2147483647 !important;
      display: flex !important; flex-direction: column !important;
      box-shadow: -4px 0 24px rgba(0,0,0,.55) !important;
      overflow: hidden !important;
    }
    #__lsb * { box-sizing: border-box !important; margin: 0 !important; }
    #__lsb h2 {
      padding: 12px 14px !important; font-size: 14px !important;
      font-weight: 700 !important; background: #0f172a !important;
      border-bottom: 1px solid #334155 !important; letter-spacing: .02em !important;
    }
    #__lsb h3 {
      margin-bottom: 8px !important; font-size: 10px !important;
      font-weight: 600 !important; text-transform: uppercase !important;
      letter-spacing: .08em !important; color: #94a3b8 !important;
    }
    #__lsb .sec { padding: 12px 14px !important; border-bottom: 1px solid #334155 !important; }
    #__lsb .scroll { flex: 1 !important; overflow-y: auto !important; }
    #__lsb ul {
      list-style: none !important; padding: 0 !important;
      display: flex !important; flex-direction: column !important; gap: 5px !important;
    }
    #__lsb li {
      display: flex !important; align-items: center !important; gap: 6px !important;
      background: #0f172a !important; border-radius: 4px !important;
      padding: 5px 8px !important;
    }
    #__lsb li span {
      flex: 1 !important; font: 11px/1.3 monospace !important;
      overflow: hidden !important; text-overflow: ellipsis !important;
      white-space: nowrap !important; color: #7dd3fc !important;
    }
    #__lsb .rm {
      background: none !important; border: none !important;
      color: #f87171 !important; cursor: pointer !important;
      font-size: 14px !important; padding: 0 2px !important; flex-shrink: 0 !important;
    }
    #__lsb .empty { color: #64748b !important; font-style: italic !important; font-size: 12px !important; }
    #__lsb .field { margin-bottom: 8px !important; }
    #__lsb .field label {
      display: block !important; margin-bottom: 3px !important;
      font-size: 11px !important; color: #94a3b8 !important;
    }
    #__lsb .field input {
      width: 100% !important; padding: 5px 8px !important;
      background: #0f172a !important; border: 1px solid #334155 !important;
      border-radius: 4px !important; color: #f1f5f9 !important;
      font: 12px/1.4 monospace !important;
    }
    #__lsb .field input:focus { outline: none !important; border-color: #3b82f6 !important; }
    #__lsb .actions {
      padding: 12px 14px !important; display: flex !important;
      flex-direction: column !important; gap: 7px !important;
      border-top: 1px solid #334155 !important;
    }
    #__lsb .btn {
      padding: 8px 12px !important; border-radius: 5px !important;
      border: none !important; font: 600 13px system-ui !important;
      cursor: pointer !important; width: 100% !important;
    }
    #__lsb .btn-p { background: #10b981 !important; color: #fff !important; }
    #__lsb .btn-p:hover { background: #059669 !important; }
    #__lsb .btn-s { background: #334155 !important; color: #f1f5f9 !important; }
    #__lsb .btn-s:hover { background: #475569 !important; }
    #__lsb .btn-d {
      background: transparent !important; color: #f87171 !important;
      border: 1px solid #f87171 !important;
    }
    #__lsb .btn-d:hover { background: rgba(248,113,113,.12) !important; }
    #__lsb .status { font-size: 11px !important; color: #94a3b8 !important; text-align: center !important; }
  `;
  document.head.appendChild(css);

  /* ── Sidebar HTML ────────────────────────────────────────────────────── */
  const sb = document.createElement('div');
  sb.id = '__lsb';
  sb.innerHTML = `
    <h2>&#128269; LingQ Selector</h2>
    <div class="scroll">
      <div class="sec">
        <h3>Selected Regions</h3>
        <ul id="__lsl"></ul>
      </div>
      <div class="sec">
        <h3>Settings</h3>
        <div class="field">
          <label>API Key</label>
          <input id="__lak" type="password" placeholder="LingQ token" autocomplete="off" />
        </div>
        <div class="field">
          <label>Language code</label>
          <input id="__llg" type="text" placeholder="en" value="en" />
        </div>
        <div class="field">
          <label>Lesson title <span style="color:#64748b">(optional)</span></label>
          <input id="__lti" type="text" placeholder="Auto-detected from page" />
        </div>
        <div class="field">
          <label>Collection / Course ID <span style="color:#64748b">(optional)</span></label>
          <input id="__lco" type="text" placeholder="123456" />
        </div>
      </div>
    </div>
    <div class="actions">
      <div class="status" id="__lst">Hover elements to preview &bull; Click to select</div>
      <button class="btn btn-p" id="__lbx">&#10003; Save Config &amp; Exit</button>
      <button class="btn btn-s" id="__lbs">&#128190; Save Config (keep open)</button>
      <button class="btn btn-d" id="__lbc">&#10007; Clear All Selections</button>
    </div>
  `;
  document.body.appendChild(sb);

  /* ── Pre-populate from existing config ───────────────────────────────── */
  const ic = window.__lingqInitialConfig || {};
  if (ic.api_key)      document.getElementById('__lak').value = ic.api_key;
  if (ic.language)     document.getElementById('__llg').value = ic.language;
  if (ic.title)        document.getElementById('__lti').value = ic.title;
  if (ic.collection_id) document.getElementById('__lco').value = String(ic.collection_id);

  /* ── State ───────────────────────────────────────────────────────────── */
  let selectors = (ic.selectors && ic.selectors.length) ? [...ic.selectors] : [];

  // Re-apply green highlight for pre-loaded selectors
  function rehighlight() {
    selectors.forEach(function(sel) {
      try { document.querySelectorAll(sel).forEach(function(el) { el.classList.add('__ls'); }); }
      catch (_) {}
    });
  }
  rehighlight();

  /* ── CSS selector generator ──────────────────────────────────────────── */
  function genSel(el) {
    if (el.id) return '#' + CSS.escape(el.id);
    var parts = [];
    var cur = el;
    while (cur && cur !== document.documentElement && cur.nodeType === 1) {
      if (cur === document.body) { parts.unshift('body'); break; }
      var tag = cur.nodeName.toLowerCase();
      if (cur.id) { parts.unshift('#' + CSS.escape(cur.id)); break; }
      var cls = Array.from(cur.classList)
        .filter(function(c) { return !c.startsWith('__l'); })
        .slice(0, 3);
      if (cls.length) {
        tag += '.' + cls.map(function(c) { return CSS.escape(c); }).join('.');
      } else if (cur.parentElement) {
        var sibs = Array.from(cur.parentElement.children);
        var idx   = sibs.indexOf(cur);
        if (idx > 0) tag += ':nth-child(' + (idx + 1) + ')';
      }
      parts.unshift(tag);
      cur = cur.parentElement;
    }
    return parts.join(' > ');
  }

  /* ── List renderer ───────────────────────────────────────────────────── */
  function render() {
    var ul = document.getElementById('__lsl');
    if (!ul) return;
    if (selectors.length === 0) {
      ul.innerHTML = '<li class="empty">Hover &amp; click to select regions</li>';
      return;
    }
    ul.innerHTML = '';
    selectors.forEach(function(s, i) {
      var li = document.createElement('li');
      li.innerHTML = '<span title="' + s + '">' + s + '</span>' +
                     '<button class="rm" data-i="' + i + '" title="Remove">&#10005;</button>';
      ul.appendChild(li);
    });
  }
  render();

  function setStatus(msg) {
    var el = document.getElementById('__lst');
    if (el) el.textContent = msg;
  }

  /* ── Remove / clear ──────────────────────────────────────────────────── */
  document.getElementById('__lsl').addEventListener('click', function(e) {
    var btn = e.target.closest('.rm');
    if (!btn) return;
    var i   = parseInt(btn.dataset.i, 10);
    var sel = selectors[i];
    try { document.querySelectorAll(sel).forEach(function(el) { el.classList.remove('__ls'); }); }
    catch (_) {}
    selectors.splice(i, 1);
    render();
    setStatus('Removed: ' + sel);
  });

  document.getElementById('__lbc').addEventListener('click', function() {
    document.querySelectorAll('.__ls').forEach(function(el) { el.classList.remove('__ls'); });
    selectors = [];
    render();
    setStatus('Cleared all selections.');
  });

  /* ── Hover highlight ─────────────────────────────────────────────────── */
  var hovered = null;

  document.addEventListener('mouseover', function(e) {
    if (sb.contains(e.target)) {
      if (hovered) { hovered.classList.remove('__lh'); hovered = null; }
      return;
    }
    if (hovered && hovered !== e.target) hovered.classList.remove('__lh');
    hovered = e.target;
    if (!hovered.classList.contains('__ls')) hovered.classList.add('__lh');
  }, true);

  document.addEventListener('mouseout', function(e) {
    if (e.target && !e.target.classList.contains('__ls')) e.target.classList.remove('__lh');
    if (hovered === e.target) hovered = null;
  }, true);

  /* ── Click to select / deselect ──────────────────────────────────────── */
  document.addEventListener('click', function(e) {
    if (sb.contains(e.target)) return;
    e.preventDefault();
    e.stopPropagation();
    var el  = e.target;
    var sel = genSel(el);
    if (el.classList.contains('__ls')) {
      // Deselect
      try { document.querySelectorAll(sel).forEach(function(n) { n.classList.remove('__ls'); }); }
      catch (_) { el.classList.remove('__ls'); }
      var idx = selectors.indexOf(sel);
      if (idx !== -1) selectors.splice(idx, 1);
      el.classList.add('__lh');
      render();
      setStatus('Deselected: ' + sel);
    } else {
      // Select
      el.classList.remove('__lh');
      el.classList.add('__ls');
      if (!selectors.includes(sel)) {
        selectors.push(sel);
        render();
      }
      setStatus('Selected: ' + sel);
    }
  }, true);

  /* ── Build config object ─────────────────────────────────────────────── */
  function buildConfig() {
    var colRaw = document.getElementById('__lco').value.trim();
    return {
      url:           location.href,
      selectors:     selectors.slice(),
      api_key:       document.getElementById('__lak').value.trim(),
      language:      document.getElementById('__llg').value.trim() || 'en',
      title:         document.getElementById('__lti').value.trim() || null,
      collection_id: colRaw ? (parseInt(colRaw, 10) || null) : null,
    };
  }

  /* ── Save buttons ────────────────────────────────────────────────────── */
  document.getElementById('__lbx').addEventListener('click', function() {
    window.__lingqConfig = buildConfig();
    window.__lingqDone   = true;
    setStatus('Saving and closing\u2026');
  });

  document.getElementById('__lbs').addEventListener('click', function() {
    window.__lingqConfig = buildConfig();
    setStatus('Config saved \u2014 continue selecting or exit.');
  });

})();
"""


# --------------------------------------------------------------------------- #
# Python helpers                                                                #
# --------------------------------------------------------------------------- #

_DEFAULT_CONFIG = "lingq_config.json"


def _inject_script(initial_config: dict | None) -> str:
    """Prefix the JS with the initial config so the sidebar can pre-populate."""
    payload = json.dumps(initial_config or {})
    return f"window.__lingqInitialConfig = {payload};\n" + _INJECTED_JS


def _load_lingq_import():
    """Import sibling lingq_daily_import as a module."""
    sys.path.insert(0, str(Path(__file__).parent))
    import lingq_daily_import as ldi  # noqa: PLC0415
    return ldi


# --------------------------------------------------------------------------- #
# Interactive mode                                                              #
# --------------------------------------------------------------------------- #

def interactive_mode(url: str, config_path: Path) -> dict:
    """Open a headed browser, inject the selector sidebar, and block until the
    user clicks 'Save Config & Exit'.  Returns the collected config dict."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError:
        print(
            "ERROR: playwright is not installed.\n"
            "  pip install playwright\n"
            "  playwright install chromium",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load existing config for pre-population (nice for editing a saved config)
    initial_config: dict | None = None
    if config_path.exists():
        try:
            initial_config = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    inject_js = _inject_script(initial_config)

    print(f"\nOpening browser: {url}")
    print("  • Hover over elements — they highlight in amber")
    print("  • Click to select (green) — click again to deselect")
    print("  • Fill in Settings on the right panel")
    print("  • Click \u2018Save Config & Exit\u2019 when done\n")

    config: dict = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        def reinject() -> None:
            try:
                page.evaluate(inject_js)
            except Exception:
                pass

        # Re-inject after every navigation so the sidebar survives link clicks
        page.on("domcontentloaded", lambda _: reinject())
        page.goto(url, wait_until="domcontentloaded")
        reinject()  # also inject immediately in case the event already fired

        try:
            # Wait indefinitely — timeout=0 means no timeout in Playwright
            page.wait_for_function("window.__lingqDone === true", timeout=0)
            config = page.evaluate("window.__lingqConfig") or {}
        except Exception:
            # Browser was closed by the user before clicking Save & Exit
            try:
                config = page.evaluate("window.__lingqConfig") or {}
            except Exception:
                config = {}
        finally:
            try:
                browser.close()
            except Exception:
                pass

    if not config:
        print("WARNING: No config was saved (browser closed without saving).", file=sys.stderr)
        return {}

    config_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return config


# --------------------------------------------------------------------------- #
# Headless import mode                                                          #
# --------------------------------------------------------------------------- #

def headless_mode(config: dict, upload: bool, out_dir: str, min_words: int) -> int:
    """Run the scrape-and-import pipeline using a saved config dict."""
    import requests as req  # noqa: PLC0415

    ldi = _load_lingq_import()

    url = config.get("url", "").strip()
    if not url:
        print("ERROR: config has no 'url'.", file=sys.stderr)
        return 1

    selectors   = config.get("selectors") or None
    api_key     = config.get("api_key") or os.getenv("LINGQ_API_KEY")
    language    = config.get("language") or "en"
    title_ovr   = config.get("title") or None
    collection  = config.get("collection_id") or None
    source_lang = config.get("source_lang") or None
    accept_lang = config.get("accept_language") or None

    source_url = url
    if source_lang:
        source_url = ldi.with_query_param(source_url, "lang", source_lang)

    title_seed = title_ovr or ldi.derive_default_title(source_url)

    try:
        html      = ldi.fetch_html(source_url, accept_language=accept_lang)
        extracted = ldi.extract_content(html, fallback_title=title_seed, selectors=selectors)
    except req.RequestException as exc:
        print(f"ERROR: fetch failed: {exc}", file=sys.stderr)
        return 1

    title      = title_ovr or extracted.title or title_seed
    text       = extracted.text
    word_count = len(re.findall(r"\w+", text))

    if word_count < min_words:
        print(
            f"ERROR: extracted only {word_count} words (minimum required: {min_words}).",
            file=sys.stderr,
        )
        return 1

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    stamp   = dt.datetime.now().strftime("%Y%m%d")
    base    = f"{stamp}-{ldi.slugify(title)[:80]}"
    txt_p   = out / f"{base}.txt"
    json_p  = out / f"{base}.payload.json"
    payload = ldi.build_lingq_payload(title=title, text=text, collection=collection)

    txt_p.write_text(text + "\n", encoding="utf-8")
    json_p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Text file : {txt_p}")
    print(f"Payload   : {json_p}")
    print(f"Words     : {word_count}")

    if not upload:
        print("Upload skipped.  Add --upload to send to LingQ.")
        return 0

    if not api_key:
        print(
            "ERROR: upload requested but no API key found.\n"
            "  Set it in the config file or export LINGQ_API_KEY=<token>",
            file=sys.stderr,
        )
        return 1

    try:
        result = ldi.upload_to_lingq(payload=payload, language=language, api_key=api_key)
    except req.HTTPError as exc:
        body = exc.response.text if exc.response is not None else ""
        print(f"ERROR: LingQ upload failed: {exc}\n{body}", file=sys.stderr)
        return 1
    except req.RequestException as exc:
        print(f"ERROR: LingQ upload failed: {exc}", file=sys.stderr)
        return 1

    lesson_id = result.get("id") if isinstance(result, dict) else None
    if lesson_id is not None:
        print(f"LingQ upload complete.  Lesson ID: {lesson_id}")
    else:
        print(f"LingQ upload complete.  Response: {json.dumps(result)}")
    return 0


# --------------------------------------------------------------------------- #
# CLI                                                                           #
# --------------------------------------------------------------------------- #

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Interactive LingQ selector tool (opens browser) or headless importer.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python lingq_interactive.py --url 'https://example.com/article'\n"
            "  python lingq_interactive.py --headless\n"
            "  python lingq_interactive.py --headless --upload\n"
        ),
    )
    p.add_argument("--url", help="Webpage URL (required for interactive mode)")
    p.add_argument(
        "--config",
        default=_DEFAULT_CONFIG,
        help=f"Config file path (default: {_DEFAULT_CONFIG})",
    )
    p.add_argument(
        "--headless",
        action="store_true",
        help="Skip the browser; run import using the saved config file",
    )
    p.add_argument(
        "--upload",
        action="store_true",
        help="Upload to LingQ after extraction (skips the y/N prompt)",
    )
    p.add_argument("--out-dir", default="./imports", help="Output folder (default: ./imports)")
    p.add_argument("--min-words", type=int, default=120, help="Minimum word count (default: 120)")
    return p.parse_args()


def _print_headless_hint(config_path: Path, upload: bool = False) -> None:
    cmd = f"python lingq_interactive.py --headless --config {config_path}"
    if upload:
        cmd += " --upload"
    print("\n" + "─" * 60)
    print("To run this import without the browser (e.g. for a cron job):")
    print(f"  {cmd}")
    print("─" * 60 + "\n")


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)

    # ── Headless mode ────────────────────────────────────────────────────── #
    if args.headless:
        if not config_path.exists():
            print(
                f"ERROR: config file not found: {config_path}\n"
                "  Run without --headless first to create it.",
                file=sys.stderr,
            )
            return 1
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"ERROR: could not read config: {exc}", file=sys.stderr)
            return 1
        print(f"Running headless import from: {config_path}\n")
        return headless_mode(
            config,
            upload=args.upload,
            out_dir=args.out_dir,
            min_words=args.min_words,
        )

    # ── Interactive mode ─────────────────────────────────────────────────── #
    url = args.url
    if not url:
        # Fall back to the URL stored in an existing config
        if config_path.exists():
            try:
                stored = json.loads(config_path.read_text(encoding="utf-8"))
                url = stored.get("url", "")
            except (json.JSONDecodeError, OSError):
                pass
        if not url:
            print(
                "ERROR: --url is required for interactive mode.\n"
                "  Example: python lingq_interactive.py --url 'https://example.com/article'",
                file=sys.stderr,
            )
            return 1

    config = interactive_mode(url=url, config_path=config_path)

    if not config:
        return 1

    print(f"Config saved: {config_path}")

    # Always show the headless command so non-technical users can copy it
    _print_headless_hint(config_path)

    # If --upload was passed, skip the prompt
    if args.upload:
        return headless_mode(
            config,
            upload=True,
            out_dir=args.out_dir,
            min_words=args.min_words,
        )

    # Prompt the user
    try:
        answer = input("Upload to LingQ now? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""

    if answer in ("y", "yes"):
        return headless_mode(
            config,
            upload=True,
            out_dir=args.out_dir,
            min_words=args.min_words,
        )

    print("Upload skipped.  Use the command above whenever you're ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
