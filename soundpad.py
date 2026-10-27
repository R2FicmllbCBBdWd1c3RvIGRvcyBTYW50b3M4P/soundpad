#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import uuid
import shutil
from http.server import HTTPServer, BaseHTTPRequestHandler

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
CACHE_DIR  = os.path.expanduser("~/.cache/soundpad")
os.makedirs(CACHE_DIR, exist_ok=True)

DEFAULT_CONFIG = {
    "provider":   "worker",
    "worker_url": "https://soundpad.SEU_SUBDOMINIO.workers.dev",
    "volume":     0.7,
}

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

def load_config():
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        print(f"Config criado em {CONFIG_PATH}. Edita worker_url e reinicia.")
        sys.exit(0)
    with open(CONFIG_PATH) as f:
        return json.load(f)

cfg = load_config()

# ── Worker Storage ────────────────────────────────────────────────────────────
class WorkerStorage:
    def __init__(self, config):
        self.base = config["worker_url"].rstrip("/")
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }

    def list_sounds(self):
        req = urllib.request.Request(f"{self.base}/sounds", headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except Exception as e:
            print(f"[worker] list_sounds error: {e}")
            return []

    def download(self, name, dest):
        url = f"{self.base}/play/{urllib.parse.quote(name)}"
        req = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=20) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
        return dest

    def upload(self, path, fname):
        with open(path, "rb") as f:
            data = f.read()
        boundary = uuid.uuid4().hex
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{fname}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()

        req = urllib.request.Request(
            f"{self.base}/upload", data=body, method="POST", headers=self.headers
        )
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())

storage = WorkerStorage(cfg)

# ── Audio Engine ──────────────────────────────────────────────────────────────
class AudioEngine:
    def __init__(self):
        self.volume = cfg.get("volume", 0.7)
        self.pitch  = 0
        self.speed  = 1.0
        self.bass   = 0
        self.treble = 0
        self.player = None
        for cmd in ("pw-play", "paplay", "aplay"):
            if shutil.which(cmd):
                self.player = cmd
                break
        self.has_ffmpeg = shutil.which("ffmpeg") is not None

    def _filter(self):
        parts = []
        if self.pitch != 0:
            ratio = 2 ** (self.pitch / 12)
            parts += [
                f"asetrate=44100*{ratio:.4f}",
                "aresample=44100",
                f"atempo={1/ratio:.4f}",
            ]
        if self.speed != 1.0:
            parts.append(f"atempo={self.speed:.4f}")
        if self.bass != 0:
            parts.append(f"bass=g={self.bass}")
        if self.treble != 0:
            parts.append(f"treble=g={self.treble}")
        return ",".join(parts) if parts else None

    def play(self, path):
        af = self._filter()
        if af and self.has_ffmpeg:
            tmp = os.path.join(CACHE_DIR, "_render.wav")
            subprocess.run(
                ["ffmpeg", "-y", "-i", path, "-af", af, "-f", "wav", tmp],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self._raw(tmp)
        else:
            self._raw(path)

    def _raw(self, path):
        if self.player == "pw-play":
            subprocess.Popen(["pw-play", f"--volume={self.volume}", path])
        elif self.player == "paplay":
            subprocess.Popen(["paplay", f"--volume={int(self.volume * 65536)}", path])
        elif self.player == "aplay":
            subprocess.Popen(["aplay", "-q", path])

audio   = AudioEngine()
_gtk_win = None   # set by main(), used by /quit

# ── HTML ──────────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<title>Phantom Soundpad</title>
<style>
/* ─── Tokens ─────────────────────────────────────────────────────────────── */
:root {
  --red:        #ff1a1a;
  --red-dim:    rgba(255,26,26,.18);
  --red-glow:   rgba(255,26,26,.45);
  --red-border: rgba(255,26,26,.22);
  --bg:         #060000;
  --glass:      rgba(12,0,0,.60);
  --glass2:     rgba(255,255,255,.030);
  --glass3:     rgba(255,255,255,.018);
  --border:     rgba(255,80,80,.14);
  --border-hi:  rgba(255,80,80,.35);
  --text:       #f5e8e8;
  --text-2:     #9e7070;
  --text-3:     #6b4444;
  --radius:     16px;
  --radius-sm:  10px;
  --blur:       blur(32px) saturate(160%);
}

/* ─── Reset ──────────────────────────────────────────────────────────────── */
*, *::before, *::after {
  box-sizing: border-box;
  margin: 0; padding: 0;
  user-select: none;
  -webkit-user-drag: none;
  outline: none;
}
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(255,26,26,.3); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: rgba(255,26,26,.6); }

/* ─── Body / Background ─────────────────────────────────────────────────── */
html, body {
  width: 100%; height: 100%;
  overflow: hidden;
  background: var(--bg);
  color: var(--text);
  font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
  font-size: 14px;
}

/* Layered background: deep glow + fine grid */
body::before {
  content: '';
  position: fixed; inset: 0; z-index: 0;
  background:
    radial-gradient(ellipse 70% 55% at 50% 45%, rgba(100,0,0,.55) 0%, transparent 70%),
    linear-gradient(rgba(255,26,26,.04) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,26,26,.04) 1px, transparent 1px);
  background-size: 100% 100%, 44px 44px, 44px 44px;
  pointer-events: none;
}

/* ─── Root Shell ─────────────────────────────────────────────────────────── */
.shell {
  position: fixed; inset: 0; z-index: 1;
  display: grid;
  grid-template-rows: 48px 1fr;
  grid-template-columns: 220px 1fr;
  grid-template-areas:
    "titlebar titlebar"
    "sidebar  main";
  overflow: hidden;
}

/* ─── Title Bar ─────────────────────────────────────────────────────────── */
.titlebar {
  grid-area: titlebar;
  -webkit-app-region: drag;
  background: rgba(0,0,0,.45);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  padding: 0 20px;
  gap: 14px;
  backdrop-filter: var(--blur);
  -webkit-backdrop-filter: var(--blur);
}
.win-dot {
  width: 13px; height: 13px;
  border-radius: 50%;
  -webkit-app-region: no-drag;
  cursor: pointer;
  transition: filter .15s, transform .15s;
  border: 1px solid rgba(0,0,0,.3);
}
.win-dot:hover { filter: brightness(1.4); transform: scale(1.15); }
.win-dot.close { background: #ff4444; box-shadow: 0 0 8px rgba(255,68,68,.5); }
.win-dot.min   { background: #ffbb33; box-shadow: 0 0 8px rgba(255,187,51,.4); }
.win-dot.max   { background: #44cc44; box-shadow: 0 0 8px rgba(68,204,68,.4); }
.title-label {
  flex: 1;
  text-align: center;
  font-size: .75em;
  font-weight: 700;
  letter-spacing: .22em;
  color: rgba(255,140,140,.75);
  text-shadow: 0 0 12px var(--red-glow);
  pointer-events: none;
}
.conn-badge {
  font-size: .72em;
  font-weight: 600;
  letter-spacing: .06em;
  padding: 3px 10px;
  border-radius: 20px;
  background: rgba(0,0,0,.4);
  border: 1px solid var(--border);
  color: var(--text-2);
  transition: all .4s;
  -webkit-app-region: no-drag;
}
.conn-badge.ok  { color: #66ee88; border-color: rgba(102,238,136,.3); text-shadow: 0 0 8px rgba(102,238,136,.4); }
.conn-badge.err { color: #ff6655; border-color: rgba(255,102,85,.3); }

/* ─── Sidebar ────────────────────────────────────────────────────────────── */
.sidebar {
  grid-area: sidebar;
  background: rgba(8,0,0,.55);
  border-right: 1px solid var(--border);
  backdrop-filter: var(--blur);
  -webkit-backdrop-filter: var(--blur);
  display: flex;
  flex-direction: column;
  padding: 24px 0 20px;
  gap: 2px;
  overflow-y: auto;
}
.nav-section {
  padding: 0 16px 6px;
  font-size: .65em;
  font-weight: 700;
  letter-spacing: .14em;
  color: var(--text-3);
  margin-top: 14px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 11px 18px;
  margin: 0 8px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  color: var(--text-2);
  font-weight: 600;
  font-size: .88em;
  letter-spacing: .02em;
  transition: all .2s;
  border: 1px solid transparent;
  position: relative;
}
.nav-item svg { flex-shrink: 0; opacity: .7; transition: opacity .2s; }
.nav-item:hover {
  background: var(--red-dim);
  color: var(--text);
  border-color: var(--red-border);
}
.nav-item:hover svg { opacity: 1; }
.nav-item.active {
  background: var(--red-dim);
  color: #ff9999;
  border-color: var(--red-border);
  box-shadow: inset 3px 0 0 var(--red), 0 2px 12px rgba(0,0,0,.4);
}
.nav-item.active svg { opacity: 1; color: var(--red); }
.nav-count {
  margin-left: auto;
  font-size: .72em;
  background: rgba(255,26,26,.15);
  color: var(--red);
  border-radius: 20px;
  padding: 1px 7px;
  font-weight: 700;
}

/* Now Playing strip at bottom of sidebar */
.now-playing {
  margin-top: auto;
  margin: auto 8px 0;
  padding: 14px 14px 12px;
  border-radius: var(--radius-sm);
  background: rgba(255,26,26,.07);
  border: 1px solid var(--red-border);
  display: none;
  flex-direction: column;
  gap: 8px;
}
.now-playing.visible { display: flex; }
.now-playing-label {
  font-size: .63em;
  font-weight: 700;
  letter-spacing: .13em;
  color: var(--text-3);
}
.now-playing-name {
  font-size: .85em;
  font-weight: 600;
  color: #ff9999;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* Waveform animation */
.wave {
  display: flex; gap: 3px; align-items: flex-end; height: 18px;
}
.wave span {
  width: 3px;
  border-radius: 2px;
  background: var(--red);
  animation: wv 0.6s ease-in-out infinite alternate;
  opacity: .8;
}
.wave span:nth-child(1) { animation-duration: .5s; animation-delay:  .0s; }
.wave span:nth-child(2) { animation-duration: .7s; animation-delay: .15s; }
.wave span:nth-child(3) { animation-duration: .4s; animation-delay: .05s; }
.wave span:nth-child(4) { animation-duration: .6s; animation-delay: .25s; }
.wave span:nth-child(5) { animation-duration: .5s; animation-delay: .10s; }
@keyframes wv {
  from { height: 3px; }
  to   { height: 16px; }
}

/* ─── Main Content ───────────────────────────────────────────────────────── */
.main {
  grid-area: main;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 24px 28px;
  gap: 18px;
}
.panel { display: none; flex-direction: column; flex: 1; overflow: hidden; gap: 18px; }
.panel.active { display: flex; }

/* ─── Toolbar ────────────────────────────────────────────────────────────── */
.toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-shrink: 0;
}
.search-wrap {
  flex: 1;
  position: relative;
}
.search-wrap svg {
  position: absolute;
  left: 14px; top: 50%;
  transform: translateY(-50%);
  color: var(--text-3);
  pointer-events: none;
}
#search {
  width: 100%;
  padding: 12px 18px 12px 40px;
  background: rgba(0,0,0,.45);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-size: .9em;
  transition: border-color .2s, box-shadow .2s;
}
#search::placeholder { color: var(--text-3); }
#search:focus {
  border-color: var(--red);
  box-shadow: 0 0 0 3px var(--red-glow);
  background: rgba(8,0,0,.6);
}

.btn {
  display: flex; align-items: center; gap: 8px;
  padding: 11px 22px;
  background: var(--red-dim);
  color: #ff8888;
  border: 1px solid var(--red-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: .85em;
  font-weight: 700;
  letter-spacing: .04em;
  transition: all .2s;
  white-space: nowrap;
  flex-shrink: 0;
}
.btn:hover {
  background: rgba(255,26,26,.3);
  color: #fff;
  box-shadow: 0 0 16px var(--red-glow);
  transform: translateY(-1px);
}
.btn:active { transform: translateY(1px); box-shadow: none; }
.btn svg { flex-shrink: 0; }
#fileInput { display: none; }

/* ─── Drop Zone ──────────────────────────────────────────────────────────── */
.drop-zone {
  border: 2px dashed var(--border);
  border-radius: var(--radius);
  padding: 14px 20px;
  text-align: center;
  color: var(--text-3);
  font-size: .82em;
  flex-shrink: 0;
  transition: all .25s;
  cursor: pointer;
  display: none; /* hidden by default, shown when dragging */
}
.drop-zone.drag-over {
  border-color: var(--red);
  background: var(--red-dim);
  color: #ff9999;
  box-shadow: 0 0 20px var(--red-glow);
}

/* ─── Sound Grid ─────────────────────────────────────────────────────────── */
.sound-grid {
  flex: 1;
  overflow-y: auto;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 10px;
  align-content: start;
  padding-right: 4px;
}

.sound-card {
  background: var(--glass3);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 18px 16px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: all .18s;
  position: relative;
  overflow: hidden;
  min-height: 88px;
}
.sound-card::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, rgba(255,26,26,.05) 0%, transparent 60%);
  opacity: 0;
  transition: opacity .18s;
}
.sound-card:hover {
  border-color: var(--red-border);
  background: var(--red-dim);
  transform: translateY(-2px);
  box-shadow: 0 6px 24px rgba(0,0,0,.5), 0 0 0 1px var(--red-border);
}
.sound-card:hover::before { opacity: 1; }
.sound-card:active { transform: translateY(0); box-shadow: none; }
.sound-card.playing {
  border-color: rgba(255,26,26,.5);
  background: rgba(255,26,26,.12);
  box-shadow: 0 0 20px rgba(255,26,26,.2), inset 0 0 20px rgba(255,26,26,.05);
}
.sound-card.playing::before { opacity: 1; }

.card-icon {
  width: 30px; height: 30px;
  border-radius: 8px;
  background: rgba(255,26,26,.12);
  border: 1px solid var(--red-border);
  display: flex; align-items: center; justify-content: center;
  color: var(--red);
  flex-shrink: 0;
  transition: all .18s;
}
.sound-card:hover .card-icon,
.sound-card.playing .card-icon {
  background: rgba(255,26,26,.22);
  box-shadow: 0 0 10px var(--red-glow);
}
.card-name {
  font-size: .85em;
  font-weight: 600;
  color: var(--text);
  line-height: 1.35;
  word-break: break-word;
}
.sound-card.playing .card-name { color: #ff9999; }

/* Empty / Loading state */
.state-msg {
  grid-column: 1 / -1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 60px 20px;
  color: var(--text-3);
  font-size: .88em;
  text-align: center;
}
.spinner {
  width: 28px; height: 28px;
  border: 3px solid rgba(255,26,26,.2);
  border-top-color: var(--red);
  border-radius: 50%;
  animation: spin .7s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ─── Settings Panel ─────────────────────────────────────────────────────── */
.settings-scroll {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-right: 4px;
}
.slider-block {
  background: var(--glass3);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  transition: border-color .2s;
}
.slider-block:hover { border-color: var(--border-hi); }
.slider-meta {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.slider-label {
  font-size: .82em;
  font-weight: 700;
  color: var(--text-2);
  letter-spacing: .05em;
}
.slider-val {
  font-size: .78em;
  font-weight: 700;
  color: var(--red);
  font-variant-numeric: tabular-nums;
  min-width: 48px;
  text-align: right;
}
input[type=range] {
  -webkit-appearance: none;
  width: 100%;
  background: transparent;
  height: 20px;
  cursor: pointer;
}
input[type=range]::-webkit-slider-runnable-track {
  height: 6px;
  background: rgba(0,0,0,.6);
  border-radius: 3px;
  border: 1px solid rgba(255,26,26,.15);
}
input[type=range]::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 20px; height: 20px;
  border-radius: 50%;
  background: #fff;
  margin-top: -8px;
  box-shadow: 0 0 10px var(--red-glow), 0 2px 6px rgba(0,0,0,.6);
  border: 2px solid rgba(255,200,200,.7);
  cursor: pointer;
  transition: transform .12s, box-shadow .12s;
}
input[type=range]::-webkit-slider-thumb:hover {
  transform: scale(1.25);
  box-shadow: 0 0 18px var(--red), 0 2px 6px rgba(0,0,0,.6);
}
.reset-all {
  background: transparent;
  border: 1px solid rgba(255,255,255,.08);
  color: var(--text-3);
  border-radius: var(--radius-sm);
  padding: 11px 22px;
  cursor: pointer;
  font-size: .82em;
  font-weight: 600;
  transition: all .2s;
  display: flex; align-items: center; justify-content: center; gap: 8px;
  flex-shrink: 0;
}
.reset-all:hover { background: rgba(255,255,255,.04); color: var(--text); border-color: rgba(255,255,255,.2); }

/* ─── Upload status toast ────────────────────────────────────────────────── */
.toast {
  position: fixed;
  bottom: 24px; right: 24px;
  padding: 12px 20px;
  border-radius: var(--radius-sm);
  font-size: .82em;
  font-weight: 600;
  z-index: 999;
  pointer-events: none;
  opacity: 0;
  transform: translateY(8px);
  transition: opacity .25s, transform .25s;
  border: 1px solid transparent;
}
.toast.show { opacity: 1; transform: translateY(0); }
.toast.ok  { background: rgba(20,60,30,.95); border-color: rgba(100,200,120,.3); color: #88ee99; }
.toast.err { background: rgba(60,10,10,.95); border-color: rgba(200,80,80,.3); color: #ff8888; }
</style>
</head>
<body>
<div class="shell">

  <!-- Title bar -->
  <header class="titlebar" id="titlebar">
    <div class="win-dot close" id="closeBtn" title="Fechar"></div>
    <div class="win-dot min"   id="minBtn"   title="Minimizar"></div>
    <div class="win-dot max"   id="maxBtn"   title="Maximizar"></div>
    <span class="title-label">Phantom Soundpad</span>
    <span class="conn-badge" id="connBadge">conectando…</span>
  </header>

  <!-- Sidebar -->
  <nav class="sidebar">
    <span class="nav-section">biblioteca</span>
    <div class="nav-item active" id="nav-sons" onclick="showPanel('sons',this)">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>
      </svg>
      Sons
      <span class="nav-count" id="soundCount">0</span>
    </div>

    <span class="nav-section">sistema</span>
    <div class="nav-item" id="nav-som" onclick="showPanel('som',this)">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="3"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M4.93 4.93a10 10 0 0 0 0 14.14"/>
      </svg>
      Ajustes de Áudio
    </div>

    <!-- Now Playing -->
    <div class="now-playing" id="nowPlaying">
      <span class="now-playing-label">tocando</span>
      <span class="now-playing-name" id="npName">—</span>
      <div class="wave">
        <span></span><span></span><span></span><span></span><span></span>
      </div>
    </div>
  </nav>

  <!-- Main -->
  <main class="main">

    <!-- Sounds panel -->
    <div id="panel-sons" class="panel active">
      <div class="toolbar">
        <div class="search-wrap">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
          </svg>
          <input id="search" placeholder="Pesquisar sons…" oninput="render()" autocomplete="off" spellcheck="false">
        </div>
        <input type="file" id="fileInput" accept="audio/*" onchange="handleFileInput(this.files[0])">
        <button class="btn" onclick="document.getElementById('fileInput').click()">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
          </svg>
          Adicionar Som
        </button>
      </div>

      <div class="drop-zone" id="dropZone">
        Solte o arquivo de áudio aqui (máx. 10 s)
      </div>

      <div id="soundGrid" class="sound-grid">
        <div class="state-msg"><div class="spinner"></div><span>Carregando sons…</span></div>
      </div>
    </div>

    <!-- Audio settings panel -->
    <div id="panel-som" class="panel">
      <div class="settings-scroll">

        <div class="slider-block">
          <div class="slider-meta">
            <span class="slider-label">Volume</span>
            <span class="slider-val" id="lv0">70%</span>
          </div>
          <input type="range" id="sl0" min="0" max="100" value="70" oninput="setAudio('volume',this.value)">
        </div>

        <div class="slider-block">
          <div class="slider-meta">
            <span class="slider-label">Tom (Pitch)</span>
            <span class="slider-val" id="lv1">0 st</span>
          </div>
          <input type="range" id="sl1" min="-12" max="12" value="0" oninput="setAudio('pitch',this.value)">
        </div>

        <div class="slider-block">
          <div class="slider-meta">
            <span class="slider-label">Velocidade</span>
            <span class="slider-val" id="lv2">100%</span>
          </div>
          <input type="range" id="sl2" min="50" max="200" value="100" oninput="setAudio('speed',this.value)">
        </div>

        <div class="slider-block">
          <div class="slider-meta">
            <span class="slider-label">Graves (Bass)</span>
            <span class="slider-val" id="lv3">0 dB</span>
          </div>
          <input type="range" id="sl3" min="-20" max="20" value="0" oninput="setAudio('bass',this.value)">
        </div>

        <div class="slider-block">
          <div class="slider-meta">
            <span class="slider-label">Agudos (Treble)</span>
            <span class="slider-val" id="lv4">0 dB</span>
          </div>
          <input type="range" id="sl4" min="-20" max="20" value="0" oninput="setAudio('treble',this.value)">
        </div>

      </div>
      <button class="reset-all" onclick="resetAll()">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-4.5"/>
        </svg>
        Restaurar padrões
      </button>
    </div>

  </main>
</div>

<div class="toast" id="toast"></div>

<script>
// ── State ──────────────────────────────────────────────────────────────────
let sounds      = [];   // [{name, size?, ...}] from /sounds
let playing     = null; // name of currently-playing sound
let loadError   = false;

// ── Panel nav ──────────────────────────────────────────────────────────────
function showPanel(id, el) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('panel-' + id).classList.add('active');
  el.classList.add('active');
}

// ── Render sound grid ──────────────────────────────────────────────────────
function render() {
  const q   = document.getElementById('search').value.toLowerCase();
  const grid = document.getElementById('soundGrid');
  const filtered = sounds.filter(s => s.name.toLowerCase().includes(q));

  if (sounds.length === 0) {
    grid.innerHTML = loadError
      ? '<div class="state-msg"><span style="font-size:1.4em">⚠</span><span>Não foi possível conectar ao Worker.<br>Verifica o worker_url no config.json.</span></div>'
      : '<div class="state-msg"><div class="spinner"></div><span>Carregando sons…</span></div>';
    return;
  }
  if (filtered.length === 0) {
    grid.innerHTML = '<div class="state-msg"><span>Nenhum som encontrado para "<em>' + escHtml(q) + '</em>"</span></div>';
    return;
  }

  grid.innerHTML = '';
  filtered.forEach(s => {
    const card = document.createElement('div');
    card.className = 'sound-card' + (playing === s.name ? ' playing' : '');
    card.dataset.name = s.name;

    const icon = document.createElement('div');
    icon.className = 'card-icon';
    icon.innerHTML = playing === s.name
      ? '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>'
      : '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>';

    const name = document.createElement('div');
    name.className = 'card-name';
    name.textContent = s.name.replace(/\.[^.]+$/, '');

    card.appendChild(icon);
    card.appendChild(name);
    card.addEventListener('click', () => playSound(s.name));
    grid.appendChild(card);
  });
}

// ── Play a sound ────────────────────────────────────────────────────────────
async function playSound(name) {
  playing = name;
  render();
  updateNowPlaying(name);

  try {
    await fetch('/play', { method: 'POST', body: name });
  } catch (e) {
    showToast('Erro ao reproduzir.', 'err');
  }

  // Clear playing state after ~3 s (approximate; no real progress from server)
  setTimeout(() => {
    if (playing === name) {
      playing = null;
      render();
      hideNowPlaying();
    }
  }, 3000);
}

function updateNowPlaying(name) {
  const np = document.getElementById('nowPlaying');
  document.getElementById('npName').textContent = name.replace(/\.[^.]+$/, '');
  np.classList.add('visible');
}
function hideNowPlaying() {
  document.getElementById('nowPlaying').classList.remove('visible');
}

// ── Audio controls ──────────────────────────────────────────────────────────
const KEYS   = ['volume','pitch','speed','bass','treble'];
const DEFS   = [70, 0, 100, 0, 0];

function setAudio(k, v) {
  const i = KEYS.indexOf(k);
  const fmts = [v => v+'%', v => v+' st', v => v+'%', v => v+' dB', v => v+' dB'];
  document.getElementById('lv'+i).textContent = fmts[i](v);
  fetch('/set', { method: 'POST', body: k + '=' + v }).catch(() => {});
}

function resetAll() {
  DEFS.forEach((v, i) => {
    document.getElementById('sl'+i).value = v;
    setAudio(KEYS[i], v);
  });
}

// ── Load sound list ─────────────────────────────────────────────────────────
async function loadSounds() {
  try {
    const r  = await fetch('/sounds');
    if (!r.ok) throw new Error('status ' + r.status);
    sounds = await r.json();
    loadError = false;
    document.getElementById('soundCount').textContent = sounds.length;
    setBadge('ok', sounds.length + ' sons');
  } catch (e) {
    loadError = true;
    setBadge('err', 'offline');
  }
  render();
}

function setBadge(cls, text) {
  const b = document.getElementById('connBadge');
  b.className = 'conn-badge ' + cls;
  b.textContent = text;
}

// ── Upload ──────────────────────────────────────────────────────────────────
async function handleFileInput(file) {
  if (!file) return;
  await uploadAudio(file);
  document.getElementById('fileInput').value = '';
}

async function uploadAudio(file) {
  // Validate duration via Web Audio API (promise form, no context leak)
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  try {
    const buf = await file.arrayBuffer();
    const decoded = await ctx.decodeAudioData(buf);
    if (decoded.duration > 10.5) {
      showToast('Arquivo excede 10 segundos.', 'err');
      return;
    }
  } catch (e) {
    showToast('Não foi possível decodificar o áudio.', 'err');
    return;
  } finally {
    ctx.close(); // prevent AudioContext leak
  }

  showToast('Enviando…', 'ok');
  try {
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch('/upload', { method: 'POST', body: fd });
    if (!r.ok) throw new Error('upload ' + r.status);
    showToast('Som adicionado!', 'ok');
    await loadSounds();
  } catch (e) {
    showToast('Falha no upload.', 'err');
  }
}

// ── Drag & Drop ─────────────────────────────────────────────────────────────
const dz = document.getElementById('dropZone');
let dragTarget = null;

document.addEventListener('dragenter', e => {
  dragTarget = e.target;
  if (e.dataTransfer.types.includes('Files')) dz.style.display = 'flex';
});
document.addEventListener('dragleave', e => {
  if (e.target === dragTarget || !document.contains(e.relatedTarget)) {
    dz.style.display = 'none';
    dz.classList.remove('drag-over');
  }
});
document.addEventListener('dragover', e => {
  e.preventDefault();
  dz.classList.add('drag-over');
});
document.addEventListener('drop', async e => {
  e.preventDefault();
  dz.style.display = 'none';
  dz.classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file && file.type.startsWith('audio/')) await uploadAudio(file);
  else if (file) showToast('Arquivo não é áudio.', 'err');
});

// ── Toast ───────────────────────────────────────────────────────────────────
let toastTimer;
function showToast(msg, type) {
  const t = document.getElementById('toast');
  t.textContent  = msg;
  t.className    = 'toast ' + type + ' show';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2800);
}

// ── Utils ────────────────────────────────────────────────────────────────────
function escHtml(s) {
  return s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// ── Window controls ──────────────────────────────────────────────────────────
document.getElementById('closeBtn').addEventListener('click', () => {
  // /quit handler calls Gtk.main_quit() server-side
  fetch('/quit', { method: 'POST' }).catch(() => {});
  window.close(); // fallback for non-GTK environments
});
document.getElementById('minBtn').addEventListener('click', () => {
  fetch('/minimize', { method: 'POST' }).catch(() => {});
});

// ── Keyboard shortcuts ───────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && playing) {
    playing = null; render(); hideNowPlaying();
  }
  if ((e.key === 'f' || e.key === 'F') && !e.ctrlKey) {
    document.getElementById('search').focus();
  }
});

// ── Boot ─────────────────────────────────────────────────────────────────────
loadSounds();
// Re-sync every 30 s in case sounds were added from another client
setInterval(loadSounds, 30000);
</script>
</body>
</html>"""

# ── GTK window ref (set in main) ──────────────────────────────────────────────
_gtk_win = None

# ── HTTP Server ───────────────────────────────────────────────────────────────
SET_KEYS = {"volume", "pitch", "speed", "bass", "treble"}

class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True

class AppHandler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass

    # ── helpers ──────────────────────────────────────────────────────────────
    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(n)

    def _ok(self, data=b"", ct="text/plain"):
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.end_headers()
        self.wfile.write(data)

    def _err(self, code, msg="error"):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(msg.encode())

    # ── GET ───────────────────────────────────────────────────────────────────
    def do_GET(self):
        if self.path == "/":
            self._ok(HTML.encode(), "text/html; charset=utf-8")
        elif self.path == "/sounds":
            sounds = storage.list_sounds()
            self._ok(json.dumps(sounds).encode(), "application/json")
        else:
            self._err(404, "not found")

    # ── POST ──────────────────────────────────────────────────────────────────
    def do_POST(self):

        # ── /play ─────────────────────────────────────────────────────────────
        if self.path == "/play":
            body = self._body().decode(errors="replace").strip()
            if not body:
                return self._err(400, "no name")

            # Fetch the authoritative list; only play names that exist on the server.
            # This prevents path traversal via crafted names.
            sounds = storage.list_sounds()
            match  = next((s for s in sounds if s["name"] == body), None)
            if not match:
                return self._err(404, "not found")

            safe_name = os.path.basename(match["name"])
            dest = os.path.join(CACHE_DIR, safe_name)
            # Verify the resolved path stays inside the cache dir
            if not os.path.abspath(dest).startswith(os.path.abspath(CACHE_DIR)):
                return self._err(400, "bad path")

            try:
                if not os.path.exists(dest):
                    storage.download(match["name"], dest)
                audio.play(dest)
            except Exception as e:
                print(f"[play] error: {e}")
                return self._err(500, "play error")
            self._ok()

        # ── /set ──────────────────────────────────────────────────────────────
        elif self.path == "/set":
            body = self._body().decode(errors="replace").strip()
            try:
                k, v = body.split("=", 1)   # split on first '=' only
                k    = k.strip()
                v    = float(v.strip())
            except (ValueError, Exception):
                return self._err(400, "bad params")

            if k not in SET_KEYS:
                return self._err(400, "unknown key")

            if   k == "volume": audio.volume = v / 100; cfg["volume"] = audio.volume; save_config(cfg)
            elif k == "pitch":  audio.pitch  = v
            elif k == "speed":  audio.speed  = v / 100
            elif k == "bass":   audio.bass   = v
            elif k == "treble": audio.treble = v
            self._ok()

        # ── /upload ───────────────────────────────────────────────────────────
        elif self.path == "/upload":
            ct = self.headers.get("Content-Type", "")
            if "boundary=" not in ct:
                return self._err(400, "no boundary")

            boundary = ct.split("boundary=", 1)[1].strip().encode()
            body     = self._body()
            fname    = None
            fdata    = None

            # Robust multipart parse
            for part in body.split(b"--" + boundary):
                if b'filename="' not in part:
                    continue
                try:
                    header, _, payload = part.partition(b"\r\n\r\n")
                    fname_raw = header.split(b'filename="', 1)[1].split(b'"', 1)[0]
                    fname = fname_raw.decode(errors="replace")
                    fdata = payload.rstrip(b"\r\n")
                    break
                except Exception:
                    continue

            if not fname or fdata is None:
                return self._err(400, "no file")

            # Sanitize filename: basename only, no path components
            fname    = os.path.basename(fname)
            tmp_path = os.path.join(CACHE_DIR, "_up_" + fname)
            try:
                with open(tmp_path, "wb") as f:
                    f.write(fdata)
                result = storage.upload(tmp_path, fname)
            except Exception as e:
                print(f"[upload] error: {e}")
                return self._err(500, "upload failed")
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

            self._ok(json.dumps(result).encode(), "application/json")

        # ── /quit ─────────────────────────────────────────────────────────────
        elif self.path == "/quit":
            self._ok()
            def _quit():
                try:
                    from gi.repository import GLib
                    GLib.idle_add(_destroy_win)
                except Exception:
                    os._exit(0)
            def _destroy_win():
                if _gtk_win:
                    _gtk_win.destroy()
                else:
                    os._exit(0)
                return False
            threading.Thread(target=_quit, daemon=True).start()

        # ── /minimize ─────────────────────────────────────────────────────────
        elif self.path == "/minimize":
            self._ok()
            def _min():
                try:
                    from gi.repository import GLib
                    GLib.idle_add(lambda: _gtk_win and _gtk_win.iconify())
                except Exception:
                    pass
            threading.Thread(target=_min, daemon=True).start()

        else:
            self._err(404, "not found")

# ── Server thread ─────────────────────────────────────────────────────────────
def start_server():
    server = ReusableHTTPServer(("127.0.0.1", 8765), AppHandler)
    server.serve_forever()

# ── GTK main ──────────────────────────────────────────────────────────────────
def main():
    global _gtk_win
    threading.Thread(target=start_server, daemon=True).start()

    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        # Try WebKit versions in order of preference
        webkit_mod = None
        for ver in ("4.1", "4.0", "3.0"):
            try:
                gi.require_version("WebKit2", ver)
                from gi.repository import WebKit2 as wk
                webkit_mod = wk
                break
            except (ValueError, ImportError):
                continue

        if webkit_mod is None:
            raise ImportError("WebKitGTK não instalado. Tente: sudo apt install gir1.2-webkit2-4.0")

        win = Gtk.Window(title="Phantom Soundpad")
        win.set_default_size(1040, 720)
        win.set_decorated(False)
        win.set_position(Gtk.WindowPosition.CENTER)
        _gtk_win = win

        webview = webkit_mod.WebView()
        webview.load_uri("http://127.0.0.1:8765")

        # Let the HTML close button work via window.close()
        webview.connect("close", lambda _: win.destroy())

        # Drag window only when clicking inside the 48 px titlebar strip.
        # -webkit-app-region is ignored by WebKit2GTK, so we gate by Y coordinate.
        def on_press(widget, event):
            if event.button == 1 and event.y < 48:
                win.begin_move_drag(
                    event.button, int(event.x_root), int(event.y_root), event.time
                )
        webview.connect("button-press-event", on_press)

        win.add(webview)
        win.connect("destroy", Gtk.main_quit)
        win.show_all()
        Gtk.main()

    except Exception as e:
        print(f"ERRO CRÍTICO: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
