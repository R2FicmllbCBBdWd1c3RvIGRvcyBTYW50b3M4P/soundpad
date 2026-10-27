#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import urllib.error
import uuid
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
CACHE_DIR = os.path.expanduser("~/.cache/soundpad")
os.makedirs(CACHE_DIR, exist_ok=True)

DEFAULT_CONFIG = {
    "provider": "worker",
    "worker_url": "https://soundpad.SEU_SUBDOMINIO.workers.dev",
    "volume": 0.7,
}

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

def load_config():
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        print(f"Config criado em {CONFIG_PATH}. Edita worker_url.")
        sys.exit(0)
    with open(CONFIG_PATH) as f:
        return json.load(f)

cfg = load_config()

# ========== STORAGE ==========
class WorkerStorage:
    def __init__(self, config):
        self.base = config["worker_url"].rstrip("/")
        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

    def list_sounds(self):
        req = urllib.request.Request(f"{self.base}/sounds", headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except Exception as e:
            print(f"Erro na API: {e}")
            return []

    def download(self, name, dest):
        url = f"{self.base}/play/{urllib.parse.quote(name)}"
        req = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=15) as r, open(dest, "wb") as f:
            f.write(r.read())
        return dest

    def upload(self, path, fname):
        with open(path, "rb") as f:
            data = f.read()
        
        boundary = uuid.uuid4().hex
        body = b""
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="file"; filename="{fname}"\r\n'.encode()
        body += b"Content-Type: application/octet-stream\r\n\r\n"
        body += data + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        
        req = urllib.request.Request(f"{self.base}/upload", data=body, method="POST", headers=self.headers)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())

storage = WorkerStorage(cfg)

# ========== AUDIO ==========
class AudioEngine:
    def __init__(self):
        self.volume = cfg.get("volume", 0.7)
        self.pitch = 0
        self.speed = 1.0
        self.bass = 0
        self.treble = 0
        self.player = None
        for cmd in ("pw-play", "paplay", "aplay"):
            if shutil.which(cmd):
                self.player = cmd
                break
        self.has_ffmpeg = shutil.which("ffmpeg") is not None

    def build_filter(self):
        parts = []
        if self.pitch != 0:
            ratio = 2 ** (self.pitch / 12)
            parts.append("asetrate=44100*{:.4f}".format(ratio))
            parts.append("aresample=44100")
            parts.append("atempo={:.4f}".format(1 / ratio))
        if self.speed != 1.0: parts.append(f"atempo={self.speed:.4f}")
        if self.bass != 0: parts.append(f"bass=g={self.bass}")
        if self.treble != 0: parts.append(f"treble=g={self.treble}")
        return ",".join(parts) if parts else None

    def play(self, path):
        af = self.build_filter()
        if af and self.has_ffmpeg:
            temp = os.path.join(CACHE_DIR, "_render.wav")
            subprocess.run(["ffmpeg", "-y", "-i", path, "-af", af, "-f", "wav", temp], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._raw(temp)
        else:
            self._raw(path)

    def _raw(self, path):
        if self.player == "pw-play": subprocess.Popen(["pw-play", f"--volume={self.volume}", path])
        elif self.player == "paplay": subprocess.Popen(["paplay", f"--volume={int(self.volume * 65536)}", path])
        elif self.player == "aplay": subprocess.Popen(["aplay", "-q", path])

audio = AudioEngine()

# ========== WEB SERVER & API ==========
HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>Phantom Soundpad</title>
<style>
    :root {
        --bg: #0f0c29;
        --grad1: #302b63;
        --grad2: #24243e;
        --accent: #66ccff;
    }
    * { box-sizing: border-box; user-select: none; }
    body {
        font-family: 'Segoe UI', system-ui, sans-serif;
        margin: 0; height: 100vh; overflow: hidden;
        background: linear-gradient(135deg, var(--bg), var(--grad1), var(--grad2));
        color: #fff; display: flex; align-items: center; justify-content: center;
    }
    
    /* Glassmorphism Panel */
    .app-container {
        width: 90vw; height: 90vh; max-width: 1000px; max-height: 800px;
        background: rgba(255, 255, 255, 0.03);
        backdrop-filter: blur(24px) saturate(150%);
        -webkit-backdrop-filter: blur(24px) saturate(150%);
        border-radius: 24px;
        border: 1px solid rgba(255, 255, 255, 0.08);
        box-shadow: 0 24px 64px rgba(0, 0, 0, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.1);
        display: flex; flex-direction: column;
        overflow: hidden;
    }

    /* Custom Title Bar */
    .titlebar {
        height: 48px; background: rgba(0,0,0,0.2);
        -webkit-app-region: drag;
        display: flex; align-items: center; padding: 0 20px;
        font-weight: 600; font-size: 0.9em; letter-spacing: 0.5px;
        border-bottom: 1px solid rgba(255,255,255,0.05);
    }
    .titlebar span { flex: 1; text-align: center; margin-left: -48px; }
    .close-btn {
        width: 14px; height: 14px; border-radius: 50%; background: #ff5f56;
        cursor: pointer; box-shadow: 0 0 8px rgba(255,95,86,0.4); transition: 0.2s;
    }
    .close-btn:hover { transform: scale(1.1); }

    /* Tabs */
    .tabs { display: flex; gap: 8px; padding: 20px 24px 0; }
    .tab {
        padding: 10px 24px; background: transparent; border-radius: 12px; cursor: pointer;
        font-weight: 600; color: rgba(255,255,255,0.5); transition: all 0.3s ease;
    }
    .tab:hover { background: rgba(255,255,255,0.05); color: #fff; }
    .tab.active {
        background: rgba(255,255,255,0.1); color: #fff;
        box-shadow: 0 4px 12px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.1);
        border: 1px solid rgba(255,255,255,0.05);
    }

    /* Content */
    .content { flex: 1; padding: 24px; display: flex; flex-direction: column; overflow: hidden; }
    .panel { display: none; flex-direction: column; height: 100%; animation: fadeIn 0.3s ease; }
    .panel.active { display: flex; }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }

    /* Search Bar */
    .search-container { position: relative; margin-bottom: 16px; display: flex; gap: 12px; }
    #search {
        flex: 1; padding: 14px 20px; background: rgba(0,0,0,0.3);
        border: 1px solid rgba(255,255,255,0.05); border-radius: 14px;
        color: #fff; outline: none; font-size: 0.95em; transition: 0.3s;
    }
    #search:focus { border-color: var(--accent); background: rgba(0,0,0,0.4); box-shadow: 0 0 0 3px rgba(102,204,255,0.15); }

    /* Buttons */
    button {
        padding: 14px 24px; background: rgba(255,255,255,0.08); color: #fff;
        border: 1px solid rgba(255,255,255,0.1); border-radius: 14px;
        cursor: pointer; font-weight: 600; transition: all 0.2s; display: flex; align-items: center; gap: 8px;
    }
    button:hover { background: rgba(255,255,255,0.15); transform: translateY(-1px); }
    button:active { transform: translateY(1px); }

    /* Sound List */
    .sound-list { flex: 1; overflow-y: auto; padding-right: 8px; }
    .sound-list::-webkit-scrollbar { width: 6px; }
    .sound-list::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.2); border-radius: 10px; }
    .sound-list::-webkit-scrollbar-track { background: transparent; }
    
    .sound {
        padding: 16px 20px; background: rgba(255,255,255,0.02); margin-bottom: 8px;
        border-radius: 14px; cursor: pointer; display: flex; align-items: center;
        border: 1px solid transparent; transition: all 0.2s;
    }
    .sound:hover { background: rgba(255,255,255,0.06); border-color: rgba(255,255,255,0.1); transform: scale(1.01); }
    .sound::before { content: '▶'; margin-right: 16px; font-size: 0.8em; color: var(--accent); opacity: 0.8; }

    /* Sliders */
    .slider-group { margin-bottom: 24px; }
    .slider-header { display: flex; justify-content: space-between; margin-bottom: 8px; font-size: 0.9em; font-weight: 500; color: rgba(255,255,255,0.8); }
    input[type=range] {
        -webkit-appearance: none; width: 100%; background: transparent;
    }
    input[type=range]::-webkit-slider-runnable-track {
        width: 100%; height: 6px; background: rgba(0,0,0,0.4); border-radius: 4px;
        border: 1px solid rgba(255,255,255,0.05);
    }
    input[type=range]::-webkit-slider-thumb {
        -webkit-appearance: none; height: 18px; width: 18px; border-radius: 50%;
        background: #fff; margin-top: -7px; box-shadow: 0 0 10px rgba(0,0,0,0.5); cursor: pointer;
        transition: transform 0.1s;
    }
    input[type=range]::-webkit-slider-thumb:hover { transform: scale(1.2); }

    .reset-btn { width: 100%; justify-content: center; background: rgba(255, 95, 86, 0.1); border-color: rgba(255, 95, 86, 0.3); margin-top: 10px; }
    .reset-btn:hover { background: rgba(255, 95, 86, 0.2); }
    
    #fileInput { display: none; }
</style>
</head>
<body>
<div class="app-container">
    <div class="titlebar">
        <div class="close-btn" onclick="window.close()"></div>
        <span>Phantom Soundpad</span>
    </div>
    
    <div class="tabs">
        <div class="tab active" onclick="tab('sons',this)">Sons</div>
        <div class="tab" onclick="tab('som',this)">Ajustes de Áudio</div>
    </div>

    <div class="content">
        <div id="sons" class="panel active">
            <div class="search-container">
                <input id="search" placeholder="Pesquisar som..." oninput="render()">
                <input type="file" id="fileInput" accept="audio/*" onchange="uploadFile()">
                <button onclick="document.getElementById('fileInput').click()">+ Adicionar</button>
            </div>
            <div id="list" class="sound-list"></div>
        </div>

        <div id="som" class="panel">
            <div class="slider-group">
                <div class="slider-header"><span>Volume</span><span id="v0">70%</span></div>
                <input type="range" min="0" max="100" value="70" oninput="setV('volume',this.value)">
            </div>
            <div class="slider-group">
                <div class="slider-header"><span>Pitch (Tom)</span><span id="v1">0</span></div>
                <input type="range" min="-12" max="12" value="0" oninput="setV('pitch',this.value)">
            </div>
            <div class="slider-group">
                <div class="slider-header"><span>Velocidade</span><span id="v2">100%</span></div>
                <input type="range" min="50" max="200" value="100" oninput="setV('speed',this.value)">
            </div>
            <div class="slider-group">
                <div class="slider-header"><span>Graves (Bass)</span><span id="v3">0 dB</span></div>
                <input type="range" min="-20" max="20" value="0" oninput="setV('bass',this.value)">
            </div>
            <div class="slider-group">
                <div class="slider-header"><span>Agudos (Treble)</span><span id="v4">0 dB</span></div>
                <input type="range" min="-20" max="20" value="0" oninput="setV('treble',this.value)">
            </div>
            <button class="reset-btn" onclick="reset()">Restaurar Padrões</button>
        </div>
    </div>
</div>

<script>
let sounds=[];

function tab(id,el){
    document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
    document.getElementById(id).classList.add('active');
    el.classList.add('active');
}

function render(){
    const q=document.getElementById('search').value.toLowerCase();
    const l=document.getElementById('list');
    l.innerHTML='';
    sounds.filter(s=>s.name.toLowerCase().includes(q)).forEach(s=>{
        const d=document.createElement('div');
        d.className='sound';
        d.textContent=s.name.replace(/\\.[^.]+$/,'');
        d.onclick=()=>fetch('/play',{method:'POST',body:s.name});
        l.appendChild(d);
    });
}

function setV(k,v){
    const idx = ['volume','pitch','speed','bass','treble'].indexOf(k);
    const label = document.getElementById('v'+idx);
    if(k === 'volume' || k === 'speed') label.textContent = v+'%';
    else if(k === 'pitch') label.textContent = v + ' semitons';
    else label.textContent = v + ' dB';
    fetch('/set',{method:'POST',body:k+'='+v});
}

function reset(){
    ['70','0','100','0','0'].forEach((v,i)=>setV(['volume','pitch','speed','bass','treble'][i],v));
    document.querySelectorAll('#som input').forEach((e,i)=>e.value=['70','0','100','0','0'][i]);
}

async function uploadFile() {
    const file = document.getElementById('fileInput').files[0];
    if(!file) return;
    
    // Validação de duração via Web Audio API
    const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const buffer = await file.arrayBuffer();
    audioCtx.decodeAudioData(buffer, async (decoded) => {
        if (decoded.duration > 10.5) {
            alert("O arquivo é muito longo! Máximo 10 segundos.");
            return;
        }
        
        const formData = new FormData();
        formData.append('file', file);
        
        try {
            const res = await fetch('/upload', { method: 'POST', body: formData });
            if(res.ok) {
                loadSounds();
            } else {
                alert("Erro no upload. Verifique o terminal.");
            }
        } catch(e) {
            alert("Erro de rede no upload.");
        }
    });
}

function loadSounds(){
    fetch('/sounds').then(r=>r.json()).then(d=>{ sounds=d; render(); });
}

loadSounds();
</script>
</body>
</html>"""

class AppHandler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    
    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML.encode())
        elif self.path == "/sounds":
            sounds = storage.list_sounds()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(sounds).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        
        if self.path == "/play":
            body = self.rfile.read(n).decode()
            sounds = storage.list_sounds()
            for s in sounds:
                if s["name"] == body:
                    p = os.path.join(CACHE_DIR, s["name"])
                    if not os.path.exists(p): storage.download(s["name"], p)
                    audio.play(p)
                    break
            self.send_response(200)
            self.end_headers()
            
        elif self.path == "/set":
            body = self.rfile.read(n).decode()
            k, v = body.split("=")
            v = float(v)
            if k == "volume": audio.volume = v/100; cfg["volume"]=audio.volume; save_config(cfg)
            elif k == "pitch": audio.pitch = v
            elif k == "speed": audio.speed = v/100
            elif k == "bass": audio.bass = v
            elif k == "treble": audio.treble = v
            self.send_response(200)
            self.end_headers()
            
        elif self.path == "/upload":
            # Parse multipart data manualmente (simplificado)
            body = self.rfile.read(n)
            boundary = self.headers.get("Content-Type").split("boundary=")[1].encode()
            parts = body.split(b"--" + boundary)
            for part in parts:
                if b'filename="' in part:
                    fname = part.split(b'filename="')[1].split(b'"')[0].decode()
                    # O conteúdo começa depois do \r\n\r\n e termina antes do \r\n final
                    file_data = part.split(b"\r\n\r\n")[1][:-2]
                    
                    tmp_path = os.path.join(CACHE_DIR, fname)
                    with open(tmp_path, "wb") as f:
                        f.write(file_data)
                    try:
                        storage.upload(tmp_path, fname)
                        os.remove(tmp_path)
                    except Exception as e:
                        print("Erro upload:", e)
                    break
            self.send_response(200)
            self.end_headers()

def start_server():
    server = HTTPServer(("127.0.0.1", 8765), AppHandler)
    server.serve_forever()

def main():
    threading.Thread(target=start_server, daemon=True).start()
    
    # Tenta abrir em uma janela nativa usando WebKitGTK
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("WebKit2", "4.0")
        from gi.repository import Gtk, WebKit2, Gdk
        
        win = Gtk.Window(title="Soundpad")
        win.set_default_size(850, 650)
        win.set_decorated(False)
        
        # Tenta deixar o fundo da janela transparente pro Glassmorphism
        screen = win.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            win.set_visual(visual)
            win.set_app_paintable(True)
        
        webview = WebKit2.WebView()
        webview.set_background_color(Gdk.RGBA(0, 0, 0, 0)) # Transparente
        webview.load_uri("http://127.0.0.1:8765")
        win.add(webview)
        
        win.connect("destroy", Gtk.main_quit)
        win.show_all()
        Gtk.main()
        
    except Exception as e:
        # Fallback: Se não tiver WebKit, abre no navegador
        print(f"WebKitGTK indisponível ({e}). Abrindo no navegador padrão...")
        webbrowser.open("http://127.0.0.1:8765")
        try:
            while True: threading.Event().wait(1)
        except KeyboardInterrupt:
            sys.exit(0)

if __name__ == "__main__":
    main()
