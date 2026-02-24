#!/usr/bin/env python3
"""LingQ Import Manager — local web application.

Serves a browser-based UI for managing site configs and running imports.
The browser opens automatically when you start the app.

Start:
  python lingq_app.py
  # or double-click start.command on macOS
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import uuid
import webbrowser
from pathlib import Path

from flask import Flask, Response, jsonify, request

# Keep Flask's own logger quiet so our startup message stays clean
logging.getLogger("werkzeug").setLevel(logging.ERROR)

app = Flask(__name__)
_DIR = Path(__file__).parent

# --------------------------------------------------------------------------- #
# Background job registry                                                       #
# --------------------------------------------------------------------------- #
# {job_id: {"lines": [str, ...], "done": bool, "rc": int|None}}
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _start_job(cmd: list[str]) -> str:
    """Run *cmd* in a background thread, capturing all output. Returns job_id."""
    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {"lines": [], "done": False, "rc": None}

    def _run() -> None:
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                cwd=str(_DIR),
            )
            for line in proc.stdout:
                with _jobs_lock:
                    _jobs[job_id]["lines"].append(line.rstrip())
            proc.wait()
            with _jobs_lock:
                _jobs[job_id]["done"] = True
                _jobs[job_id]["rc"] = proc.returncode
        except Exception as exc:
            with _jobs_lock:
                _jobs[job_id]["lines"].append(f"ERROR: {exc}")
                _jobs[job_id]["done"] = True
                _jobs[job_id]["rc"] = 1

    threading.Thread(target=_run, daemon=True).start()
    return job_id


# --------------------------------------------------------------------------- #
# Filename validation helper                                                    #
# --------------------------------------------------------------------------- #
_SAFE_FILENAME = re.compile(r"^lingq_[a-z0-9][a-z0-9\-]*\.json$")


def _safe(filename: str) -> bool:
    return bool(_SAFE_FILENAME.match(filename))


# --------------------------------------------------------------------------- #
# Flask routes — API                                                            #
# --------------------------------------------------------------------------- #

@app.route("/api/sites")
def api_sites():
    sites = []
    for p in sorted(_DIR.glob("lingq_*.json")):
        try:
            config = json.loads(p.read_text(encoding="utf-8"))
            url = config.get("url", "")
            try:
                from urllib.parse import urlparse
                name = urlparse(url).netloc.replace("www.", "") or p.stem
            except Exception:
                name = p.stem
            sites.append({"filename": p.name, "url": url, "name": name})
        except Exception:
            pass
    return jsonify(sites)


@app.route("/api/site/<filename>")
def api_get_site(filename: str):
    if not _safe(filename):
        return jsonify({"error": "Invalid filename"}), 400
    p = _DIR / filename
    if not p.exists():
        return jsonify({}), 200
    return jsonify(json.loads(p.read_text(encoding="utf-8")))


@app.route("/api/site", methods=["POST"])
def api_save_site():
    body = request.get_json(force=True)
    filename = (body.get("filename") or "").strip()
    config = body.get("config") or {}
    if not _safe(filename):
        return jsonify({"error": "Invalid filename"}), 400
    p = _DIR / filename
    p.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return jsonify({"filename": filename})


@app.route("/api/site/<filename>", methods=["DELETE"])
def api_delete_site(filename: str):
    if not _safe(filename):
        return jsonify({"error": "Invalid filename"}), 400
    p = _DIR / filename
    if p.exists():
        p.unlink()
    return jsonify({"ok": True})


@app.route("/api/launch", methods=["POST"])
def api_launch():
    """Start an interactive Playwright browser session."""
    body = request.get_json(force=True)
    filename = (body.get("filename") or "").strip()
    url = (body.get("url") or "").strip()
    if not _safe(filename) or not url:
        return jsonify({"error": "filename and url required"}), 400
    cmd = [sys.executable, "lingq_interactive.py", "--url", url, "--config", filename]
    return jsonify({"job_id": _start_job(cmd)})


@app.route("/api/run", methods=["POST"])
def api_run():
    """Start a headless import job."""
    body = request.get_json(force=True)
    filename = (body.get("filename") or "").strip()
    if not _safe(filename):
        return jsonify({"error": "Invalid filename"}), 400
    cmd = [sys.executable, "lingq_interactive.py", "--headless", "--config", filename]
    if body.get("upload"):
        cmd.append("--upload")
    return jsonify({"job_id": _start_job(cmd)})


@app.route("/api/job/<job_id>")
def api_job(job_id: str):
    offset = max(0, int(request.args.get("offset", 0)))
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({"lines": job["lines"][offset:], "done": job["done"], "rc": job["rc"]})


# --------------------------------------------------------------------------- #
# Main HTML page                                                                #
# --------------------------------------------------------------------------- #

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>LingQ Import Manager</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: #f1f5f9; color: #1e293b;
      height: 100vh; display: flex; flex-direction: column; overflow: hidden;
    }

    /* ── Top bar ─────────────────────────────────────────────────────────── */
    #topbar {
      background: #0f172a; color: #f1f5f9;
      padding: 0 20px; height: 50px;
      display: flex; align-items: center; gap: 10px; flex-shrink: 0;
    }
    #topbar h1 { font-size: 15px; font-weight: 700; }
    #topbar .tag {
      font-size: 10px; background: #1e3a5f; color: #7dd3fc;
      padding: 2px 8px; border-radius: 20px; font-weight: 600;
      letter-spacing: .04em;
    }

    /* ── Layout ──────────────────────────────────────────────────────────── */
    #main { display: flex; flex: 1; overflow: hidden; }

    /* ── Sidebar ─────────────────────────────────────────────────────────── */
    #sidebar {
      width: 230px; background: #1e293b;
      display: flex; flex-direction: column; flex-shrink: 0;
    }
    #sidebar-head {
      padding: 14px 14px 8px;
      font-size: 10px; font-weight: 700; text-transform: uppercase;
      letter-spacing: .1em; color: #475569;
    }
    #new-btn {
      margin: 0 10px 10px; padding: 9px 12px;
      background: #10b981; color: white; border: none; border-radius: 6px;
      cursor: pointer; font-size: 13px; font-weight: 600; width: calc(100% - 20px);
      transition: background .15s;
    }
    #new-btn:hover { background: #059669; }
    #site-list { flex: 1; overflow-y: auto; }
    .site-item {
      padding: 10px 14px; cursor: pointer;
      border-left: 3px solid transparent; color: #94a3b8;
      transition: background .1s;
    }
    .site-item:hover { background: rgba(255,255,255,.05); color: #cbd5e1; }
    .site-item.active { background: #0f172a; color: #f1f5f9; border-left-color: #10b981; }
    .site-name { font-size: 13px; font-weight: 600; margin-bottom: 2px; }
    .site-url {
      font-size: 11px; color: #475569; white-space: nowrap;
      overflow: hidden; text-overflow: ellipsis; max-width: 190px;
    }
    .site-item.active .site-url { color: #64748b; }
    #no-sites { padding: 20px 14px; color: #475569; font-size: 12px; line-height: 1.6; }

    /* ── Right panel ─────────────────────────────────────────────────────── */
    #panel { flex: 1; display: flex; flex-direction: column; overflow: hidden; }

    /* Empty state */
    #empty {
      flex: 1; display: flex; flex-direction: column;
      align-items: center; justify-content: center; gap: 10px;
      color: #94a3b8;
    }
    #empty .icon { font-size: 52px; }
    #empty p { font-size: 14px; }

    /* Config form */
    #form-wrap { flex: 1; overflow-y: auto; padding: 18px; display: none; }
    .card {
      background: white; border-radius: 8px; padding: 18px;
      margin-bottom: 14px; box-shadow: 0 1px 3px rgba(0,0,0,.07);
    }
    .card-title {
      font-size: 10px; font-weight: 700; text-transform: uppercase;
      letter-spacing: .09em; color: #94a3b8; margin-bottom: 14px;
    }
    .row { display: flex; gap: 12px; }
    .field { flex: 1; margin-bottom: 12px; }
    .field:last-child { margin-bottom: 0; }
    .field label {
      display: block; font-size: 11px; font-weight: 600;
      color: #64748b; margin-bottom: 4px; letter-spacing: .03em;
    }
    .field label .opt { font-weight: 400; color: #94a3b8; }
    .field input {
      width: 100%; padding: 8px 10px; border: 1px solid #e2e8f0;
      border-radius: 6px; font-size: 13px; color: #1e293b;
      background: #f8fafc; transition: border-color .15s, background .15s;
    }
    .field input:focus { outline: none; border-color: #10b981; background: white; }

    /* Selectors */
    #sel-list { display: flex; flex-direction: column; gap: 6px; margin-bottom: 8px; }
    .sel-row { display: flex; gap: 6px; }
    .sel-row input {
      flex: 1; padding: 7px 10px; border: 1px solid #e2e8f0; border-radius: 6px;
      font-size: 12px; font-family: ui-monospace, monospace; background: #f8fafc;
    }
    .sel-row input:focus { outline: none; border-color: #10b981; background: white; }
    .rm-sel {
      padding: 7px 11px; border: 1px solid #fca5a5; background: white;
      color: #ef4444; border-radius: 6px; cursor: pointer; font-size: 14px;
      transition: background .1s;
    }
    .rm-sel:hover { background: #fef2f2; }
    #add-sel {
      padding: 7px 14px; border: 1px dashed #94a3b8; background: transparent;
      color: #64748b; border-radius: 6px; cursor: pointer; font-size: 12px;
      width: 100%; transition: all .15s;
    }
    #add-sel:hover { border-color: #10b981; color: #10b981; }
    .hint { font-size: 11px; color: #94a3b8; margin-top: 6px; line-height: 1.5; }

    /* ── Action bar ──────────────────────────────────────────────────────── */
    #actions {
      background: white; border-top: 1px solid #e2e8f0;
      padding: 12px 18px; display: none; flex-shrink: 0;
    }
    .action-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .btn {
      padding: 9px 15px; border-radius: 6px; border: none;
      font-size: 13px; font-weight: 600; cursor: pointer;
      display: inline-flex; align-items: center; gap: 5px;
      transition: background .15s, opacity .15s;
    }
    .btn:disabled { opacity: .45; cursor: not-allowed; }
    .g  { background: #10b981; color: white; }
    .g:hover:not(:disabled)  { background: #059669; }
    .b  { background: #3b82f6; color: white; }
    .b:hover:not(:disabled)  { background: #2563eb; }
    .sl { background: #f1f5f9; color: #475569; border: 1px solid #e2e8f0; }
    .sl:hover:not(:disabled) { background: #e2e8f0; }
    .r  { background: white; color: #ef4444; border: 1px solid #fca5a5; }
    .r:hover:not(:disabled)  { background: #fef2f2; }
    .upload-wrap {
      margin-left: auto; display: flex; align-items: center; gap: 6px;
      font-size: 13px; color: #475569; cursor: pointer;
    }
    .upload-wrap input { width: 15px; height: 15px; cursor: pointer; accent-color: #10b981; }

    /* ── Log panel ───────────────────────────────────────────────────────── */
    #log {
      background: #0f172a; color: #94a3b8;
      font-family: ui-monospace, monospace; font-size: 12px; line-height: 1.65;
      padding: 12px 18px; height: 170px; overflow-y: auto;
      flex-shrink: 0; display: none;
    }
    .ll { padding: 1px 0; white-space: pre-wrap; word-break: break-all; }
    .lok { color: #34d399; }
    .lerr { color: #f87171; }
    .linfo { color: #93c5fd; }

    /* ── Status bar ──────────────────────────────────────────────────────── */
    #statusbar {
      background: #f8fafc; border-top: 1px solid #e2e8f0;
      padding: 5px 18px; font-size: 11px; color: #64748b;
      display: flex; align-items: center; gap: 8px; flex-shrink: 0;
    }
    .spin {
      width: 11px; height: 11px; border: 2px solid #e2e8f0;
      border-top-color: #10b981; border-radius: 50%;
      animation: spin .7s linear infinite; display: none; flex-shrink: 0;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body>

<div id="topbar">
  <h1>&#128218; LingQ Import Manager</h1>
  <span class="tag">local</span>
</div>

<div id="main">

  <!-- Sidebar -->
  <div id="sidebar">
    <div id="sidebar-head">Your Sites</div>
    <button id="new-btn" onclick="newSite()">&#43; New Site</button>
    <div id="site-list">
      <div id="no-sites">No sites yet.<br>Click <strong>+ New Site</strong> to begin.</div>
    </div>
  </div>

  <!-- Right panel -->
  <div id="panel">

    <div id="empty">
      <div class="icon">&#128196;</div>
      <p>Select a site or add a new one</p>
    </div>

    <!-- Config form -->
    <div id="form-wrap">

      <div class="card">
        <div class="card-title">Site URL</div>
        <div class="field">
          <label>Page address</label>
          <input id="f-url" type="url" placeholder="https://example.com/daily-reading" />
        </div>
      </div>

      <div class="card">
        <div class="card-title">Content Selectors</div>
        <div id="sel-list"></div>
        <button id="add-sel" onclick="addSel()">&#43; Add selector</button>
        <p class="hint">
          CSS selectors that target the exact text you want to import.
          Not sure what to use? Click <strong>Select Content</strong> to pick visually in the browser.
          Leave empty to let the importer choose automatically.
        </p>
      </div>

      <div class="card">
        <div class="card-title">LingQ Settings</div>
        <div class="row">
          <div class="field">
            <label>API Key</label>
            <input id="f-key" type="password" placeholder="Your LingQ token" autocomplete="off" />
          </div>
          <div class="field" style="max-width:110px">
            <label>Language</label>
            <input id="f-lang" type="text" placeholder="en" />
          </div>
        </div>
        <div class="row">
          <div class="field">
            <label>Lesson Title <span class="opt">(optional)</span></label>
            <input id="f-title" type="text" placeholder="Auto-detected from page" />
          </div>
          <div class="field" style="max-width:160px">
            <label>Collection ID <span class="opt">(optional)</span></label>
            <input id="f-coll" type="text" placeholder="123456" />
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">Browser Language <span class="opt" style="text-transform:none;letter-spacing:0">(optional)</span></div>
        <div class="field">
          <label>Page locale — use when the site serves content based on your browser&#8217;s language</label>
          <input id="f-bloc" type="text" placeholder="e.g. es-ES" />
        </div>
      </div>

    </div><!-- /form-wrap -->

    <!-- Action bar -->
    <div id="actions">
      <div class="action-row">
        <button class="btn g" id="btn-sel" onclick="launchBrowser()">&#127760; Select Content</button>
        <button class="btn b" id="btn-run" onclick="runImport()">&#9654; Run Import</button>
        <button class="btn sl" onclick="saveSite()">&#128190; Save</button>
        <button class="btn r"  onclick="deleteSite()">&#128465; Delete</button>
        <label class="upload-wrap">
          <input type="checkbox" id="chk-upload" />
          Upload to LingQ
        </label>
      </div>
    </div>

    <!-- Log output -->
    <div id="log"></div>

    <!-- Status bar -->
    <div id="statusbar">
      <div class="spin" id="spin"></div>
      <span id="stxt">Ready</span>
    </div>

  </div><!-- /panel -->

</div><!-- /main -->

<script>
  /* ── State ────────────────────────────────────────────────────────────── */
  let currentFile = null;  // active lingq_*.json filename
  let pollTimer   = null;

  /* ── Boot ─────────────────────────────────────────────────────────────── */
  document.addEventListener('DOMContentLoaded', loadSiteList);

  /* ── API thin wrapper ─────────────────────────────────────────────────── */
  async function call(method, path, body) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const r = await fetch(path, opts);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }

  /* ── Site list ────────────────────────────────────────────────────────── */
  async function loadSiteList() {
    const sites = await call('GET', '/api/sites');
    const list  = document.getElementById('site-list');
    list.innerHTML = '';
    if (!sites.length) {
      list.innerHTML = '<div id="no-sites">No sites yet.<br>Click <strong>+ New Site</strong> to begin.</div>';
      return;
    }
    sites.forEach(s => {
      const el = document.createElement('div');
      el.className = 'site-item' + (s.filename === currentFile ? ' active' : '');
      el.dataset.file = s.filename;
      el.onclick = () => openSite(s.filename);
      el.innerHTML = `<div class="site-name">${esc(s.name)}</div>
                      <div class="site-url" title="${esc(s.url)}">${esc(s.url)}</div>`;
      list.appendChild(el);
    });
  }

  /* ── Open / new site ──────────────────────────────────────────────────── */
  async function openSite(filename) {
    const config = await call('GET', `/api/site/${filename}`);
    currentFile = filename;
    populateForm(config);
    highlightActive();
  }

  function newSite() {
    currentFile = null;
    populateForm({});
    highlightActive();
    document.getElementById('f-url').focus();
  }

  function highlightActive() {
    document.querySelectorAll('.site-item')
      .forEach(el => el.classList.toggle('active', el.dataset.file === currentFile));
  }

  /* ── Form population ──────────────────────────────────────────────────── */
  function populateForm(c) {
    v('f-url',  c.url              || '');
    v('f-key',  c.api_key          || '');
    v('f-lang', c.language         || 'en');
    v('f-bloc', c.browser_language || '');
    v('f-title',c.title            || '');
    v('f-coll', c.collection_id != null ? String(c.collection_id) : '');

    const sl = document.getElementById('sel-list');
    sl.innerHTML = '';
    (c.selectors || []).forEach(s => addSel(s));

    show('form-wrap'); show('actions'); show('log');
    document.getElementById('empty').style.display = 'none';
  }

  /* ── Selectors ────────────────────────────────────────────────────────── */
  function addSel(val = '') {
    const row = document.createElement('div');
    row.className = 'sel-row';
    row.innerHTML = `<input type="text" value="${esc(val)}" placeholder=".article-body" />
                     <button class="rm-sel" onclick="this.parentElement.remove()" title="Remove">&#10005;</button>`;
    document.getElementById('sel-list').appendChild(row);
    if (!val) row.querySelector('input').focus();
  }

  function getSelectors() {
    return [...document.querySelectorAll('.sel-row input')]
      .map(i => i.value.trim()).filter(Boolean);
  }

  /* ── Build config from form ───────────────────────────────────────────── */
  function buildConfig() {
    const colRaw = g('f-coll').trim();
    return {
      url:              g('f-url').trim(),
      selectors:        getSelectors(),
      api_key:          g('f-key').trim(),
      language:         g('f-lang').trim() || 'en',
      browser_language: g('f-bloc').trim() || null,
      title:            g('f-title').trim() || null,
      collection_id:    colRaw ? (parseInt(colRaw, 10) || null) : null,
    };
  }

  function fileForUrl(url) {
    try {
      const host = new URL(url).hostname.toLowerCase().replace(/^www\./, '');
      const slug = host.replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'site';
      return `lingq_${slug}.json`;
    } catch { return 'lingq_site.json'; }
  }

  /* ── Save ─────────────────────────────────────────────────────────────── */
  async function saveSite() {
    const config = buildConfig();
    if (!config.url) { status('Please enter a URL first.', 'err'); return null; }
    const filename = currentFile || fileForUrl(config.url);
    await call('POST', '/api/site', { filename, config });
    currentFile = filename;
    status(`Saved: ${filename}`, 'ok');
    await loadSiteList();
    highlightActive();
    return filename;
  }

  /* ── Delete ───────────────────────────────────────────────────────────── */
  async function deleteSite() {
    if (!currentFile) return;
    if (!confirm(`Delete the config for ${currentFile}?\nThis cannot be undone.`)) return;
    await call('DELETE', `/api/site/${currentFile}`);
    currentFile = null;
    document.getElementById('empty').style.display  = '';
    hide('form-wrap'); hide('actions'); hide('log');
    clearLog();
    await loadSiteList();
  }

  /* ── Launch browser ───────────────────────────────────────────────────── */
  async function launchBrowser() {
    const config = buildConfig();
    if (!config.url) { status('Please enter a URL first.', 'err'); return; }
    const filename = await saveSite();
    if (!filename) return;
    clearLog();
    log('Opening browser window — hover to preview, click to select content.', 'linfo');
    log('When done, click \u201cSave Config & Exit\u201d in the browser sidebar.', 'linfo');
    busy(true);
    const { job_id } = await call('POST', '/api/launch', { filename, url: config.url });
    poll(job_id, 'launch');
  }

  /* ── Run import ───────────────────────────────────────────────────────── */
  async function runImport() {
    const config = buildConfig();
    if (!config.url) { status('Please enter a URL first.', 'err'); return; }
    const filename = await saveSite();
    if (!filename) return;
    clearLog();
    status('Running import\u2026', 'info');
    busy(true);
    const upload = document.getElementById('chk-upload').checked;
    const { job_id } = await call('POST', '/api/run', { filename, upload });
    poll(job_id, 'run');
  }

  /* ── Job polling ──────────────────────────────────────────────────────── */
  function poll(jobId, type) {
    let offset = 0;
    clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      try {
        const data = await call('GET', `/api/job/${jobId}?offset=${offset}`);
        data.lines.forEach(line => log(line));
        offset += data.lines.length;
        if (data.done) {
          clearInterval(pollTimer);
          busy(false);
          if (data.rc === 0) {
            status('Done.', 'ok');
            if (type === 'launch' && currentFile) {
              // Re-read config — the browser session may have updated selectors
              const updated = await call('GET', `/api/site/${currentFile}`);
              populateForm(updated);
              log('\u2713 Form updated with your selections.', 'lok');
            }
          } else {
            status('Finished with errors \u2014 see output above.', 'err');
          }
        }
      } catch (e) {
        clearInterval(pollTimer);
        busy(false);
        status('Connection error: ' + e.message, 'err');
      }
    }, 600);
  }

  /* ── Tiny UI helpers ──────────────────────────────────────────────────── */
  function g(id)  { return document.getElementById(id).value; }
  function v(id, val) { document.getElementById(id).value = val; }
  function show(id) { document.getElementById(id).style.display = ''; }
  function hide(id) { document.getElementById(id).style.display = 'none'; }
  function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;'); }

  function clearLog() { document.getElementById('log').innerHTML = ''; }

  function log(text, cls = '') {
    const panel = document.getElementById('log');
    if (!cls) cls = text.startsWith('ERROR') ? 'lerr' : text.startsWith('\u2713') ? 'lok' : '';
    const div = document.createElement('div');
    div.className = 'll ' + cls;
    div.textContent = text;
    panel.appendChild(div);
    panel.scrollTop = panel.scrollHeight;
  }

  function status(msg, type = '') {
    const el = document.getElementById('stxt');
    el.textContent = msg;
    el.style.color = { err:'#ef4444', ok:'#10b981', info:'#3b82f6' }[type] || '#64748b';
  }

  function busy(on) {
    document.getElementById('spin').style.display = on ? 'block' : 'none';
    ['btn-sel','btn-run'].forEach(id => { document.getElementById(id).disabled = on; });
    if (on) status('Working\u2026', 'info');
  }
</script>
</body>
</html>"""


@app.route("/")
def index():
    return Response(_HTML, content_type="text/html")


# --------------------------------------------------------------------------- #
# Entry point                                                                   #
# --------------------------------------------------------------------------- #

def _free_port(start: int = 5050) -> int:
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


if __name__ == "__main__":
    port = _free_port()
    url  = f"http://127.0.0.1:{port}"
    print(f"LingQ Import Manager")
    print(f"  {url}")
    print(f"  Press Ctrl+C to stop.\n")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
