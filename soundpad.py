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
import shutil
import socket
from http.server import HTTPServer, BaseHTTPRequestHandler

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
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<title>Phantom Soundpad</title>
<style>
    :root {
        --accent: #ff2222;
        --accent-glow: rgba(255, 34, 34, 0.4);
        --glass-bg: rgba(15, 0, 0, 0.6);
        --glass-border: rgba(255, 34, 34, 0.2);
        --text-main: #ffffff;
        --text-muted: #cc9999;
    }
    * { box-sizing: border-box; user-select: none; -webkit-user-drag: none; }
    
    /* Fundo Cyberpunk Vermelho com Grid */
    body {
        font-family: 'Segoe UI', system-ui, sans-serif;
        margin: 0; height: 100vh; overflow: hidden;
        background-color: #050000;
        background-image: 
            linear-gradient(rgba(255, 0, 0, 0.07) 1px, transparent 1px),
            linear-gradient(90deg, rgba(255, 0, 0, 0.07) 1px, transparent 1px),
            radial-gradient(circle at 50% 50%, rgba(80, 0, 0, 0.4) 0%, #050000 80%);
        background-size: 30px 30px, 30px 30px, 100% 100%;
        background-position: center;
        color: var(--text-main); display: flex; align-items: center; justify-content: center;
    }

    /* Painel de Vidro (Glassmorphism) */
    .app-container {
        width: 92vw; height: 92vh; max-width: 1100px; max-height: 850px;
        background: var(--glass-bg);
        backdrop-filter: blur(20px) saturate(150%);
        -webkit-backdrop-filter: blur(20px) saturate(150%);
        border-radius: 16px;
        border: 1px solid var(--glass-border);
        box-shadow: 
            0 30px 60px rgba(0, 0, 0, 0.9),
            inset 0 1px 0 rgba(255, 80, 80, 0.1),
            inset 0 0 50px rgba(255, 0, 0, 0.05);
        display: flex; flex-direction: column;
        overflow: hidden; position: relative;
    }

    /* Barra de Título (Arrastável) */
    .titlebar {
        height: 50px; background: rgba(0,0,0,0.4);
        display: flex; align-items: center; padding: 0 24px;
        border-bottom: 1px solid rgba(255, 50, 50, 0.15);
    }
    .titlebar span { 
        flex: 1; text-align: center; font-weight: 700; 
        font-size: 0.85em; letter-spacing: 2px; text-transform: uppercase;
        color: #ff8888; text-shadow: 0 0 10px var(--accent-glow); margin-left: -24px;
    }
    .close-btn {
        width: 16px; height: 16px; border-radius: 50%; background: var(--accent);
        cursor: pointer; box-shadow: 0 0 12px var(--accent); transition: all 0.2s;
        border: 1px solid rgba(255,255,255,0.2);
    }
    .close-btn:hover { transform: scale(1.15); box-shadow: 0 0 20px var(--accent); }

    /* Abas */
    .tabs { display: flex; gap: 12px; padding: 24px 32px 0; }
    .tab {
        padding: 12px 28px; background: transparent; border-radius: 10px; cursor: pointer;
        font-weight: 600; color: var(--text-muted); transition: all 0.3s ease;
        font-size: 0.95em; border: 1px solid transparent;
    }
    .tab:hover { background: rgba(255, 34, 34, 0.05); color: var(--text-main); }
    .tab.active {
        background: rgba(255, 34, 34, 0.15); color: var(--text-main);
        box-shadow: 0 4px 15px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255, 100, 100, 0.2);
        border: 1px solid var(--glass-border);
    }

    /* Conteúdo */
    .content { flex: 1; padding: 24px 32px; display: flex; flex-direction: column; overflow: hidden; }
    .panel { display: none; flex-direction: column; height: 100%; animation: fadeIn 0.3s ease; }
    .panel.active { display: flex; }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }

    /* Busca e Botões */
    .search-container { position: relative; margin-bottom: 20px; display: flex; gap: 12px; }
    #search {
        flex: 1; padding: 16px 24px; background: rgba(0,0,0,0.5);
        border: 1px solid var(--glass-border); border-radius: 12px;
        color: var(--text-main); outline: none; font-size: 1em; transition: 0.3s;
    }
    #search:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-glow); }

    button {
        padding: 16px 28px; background: rgba(255, 34, 34, 0.1); color: #ff7777;
        border: 1px solid rgba(255, 34, 34, 0.3); border-radius: 12px;
        cursor: pointer; font-weight: 600; transition: all 0.2s;
        display: flex; align-items: center; gap: 8px; text-transform: uppercase; font-size: 0.85em; letter-spacing: 1px;
    }
    button:hover { background: rgba(255, 34, 34, 0.2); color: #fff; box-shadow: 0 0 15px var(--accent-glow); }
    button:active { transform: translateY(1px); }

    /* Lista de Sons */
    .sound-list { flex: 1; overflow-y: auto; padding-right: 10px; }
    .sound-list::-webkit-scrollbar { width: 8px; }
    .sound-list::-webkit-scrollbar-thumb { background: rgba(255, 34, 34, 0.4); border-radius: 10px; }
    .sound-list::-webkit-scrollbar-thumb:hover { background: var(--accent); }
    .sound-list::-webkit-scrollbar-track { background: transparent; }
    
    .sound {
        padding: 18px 24px; background: rgba(255,255,255,0.02); margin-bottom: 10px;
        border-radius: 12px; cursor: pointer; display: flex; align-items: center;
        border: 1px solid transparent; transition: all 0.2s;
    }
    .sound:hover { 
        background: rgba(255, 34, 34, 0.08); border-color: rgba(255, 34, 34, 0.3); 
        transform: translateX(5px); 
    }
    .sound::before { 
        content: '▶'; margin-right: 20px; font-size: 1em; color: var(--accent); 
        text-shadow: 0 0 10px var(--accent); 
    }

    /* Sliders */
    .slider-group { margin-bottom: 32px; }
    .slider-header { 
        display: flex; justify-content: space-between; margin-bottom: 12px; 
        font-size: 0.9em; font-weight: 700; color: #ffaaaa; text-transform: uppercase; letter-spacing: 1px;
    }
    input[type=range] { -webkit-appearance: none; width: 100%; background: transparent; }
    input[type=range]::-webkit-slider-runnable-track {
        width: 100%; height: 8px; background: rgba(0,0,0,0.6); border-radius: 4px;
        border: 1px solid rgba(255, 34, 34, 0.2);
    }
    input[type=range]::-webkit-slider-thumb {
        -webkit-appearance: none; height: 22px; width: 22px; border-radius: 50%;
        background: #fff; margin-top: -8px; 
        box-shadow: 0 0 15px var(--accent), inset 0 0 5px rgba(0,0,0,0.5); 
        cursor: pointer; transition: transform 0.1s;
    }
    input[type=range]::-webkit-slider-thumb:hover { transform: scale(1.2); background: var(--accent); }

    .reset-btn { width: 100%; justify-content: center; background: transparent; border-color: rgba(255, 255, 255, 0.1); color: #ccc; margin-top: 20px; }
    .reset-btn:hover { background: rgba(255, 255, 255, 0.05); color: #fff; box-shadow: none; border-color: rgba(255, 255, 255, 0.3); }
    
    #fileInput { display: none; }
</style>
</head>
<body>
<div class="app-container">
    <div class="titlebar" id="titlebar">
        <div class="close-btn" onclick="window.close()"></div>
        <span>Phantom Soundpad</span>
    </div>
    
    <div class="tabs">
        <div class="tab active" onclick="tab('sons',this)">Sons</div>
        <div class="tab" onclick="tab('som',this)">Ajustes</div>
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
    else if(k === 'pitch') label.textContent = v + ' st';
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
                alert("Erro no upload.");
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

# Servidor que permite reutilizar a porta e não trava se já estiver aberta
class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True

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
            body = self.rfile.read(n)
            boundary = self.headers.get("Content-Type").split("boundary=")[1].encode()
            parts = body.split(b"--" + boundary)
            for part in parts:
                if b'filename="' in part:
                    fname = part.split(b'filename="')[1].split(b'"')[0].decode()
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
    server = ReusableHTTPServer(("127.0.0.1", 8765), AppHandler)
    server.serve_forever()

def main():
    threading.Thread(target=start_server, daemon=True).start()
    
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk, Gdk
        
        # Tenta carregar o WebKit em várias versões (garante compatibilidade)
        webkit_loaded = False
        for version in ("4.1", "4.0", "3.0"):
            try:
                gi.require_version("WebKit2", version)
                from gi.repository import WebKit2
                webkit_loaded = True
                break
            except (ValueError, ImportError):
                continue
                
        if not webkit_loaded:
            raise ImportError("WebKitGTK não encontrado no sistema.")
        
        win = Gtk.Window(title="Soundpad")
        win.set_default_size(1000, 750)
        win.set_decorated(False)
        win.set_position(Gtk.WindowPosition.CENTER)
        
        # Deixa a janela transparente pro CSS funcionar
        screen = win.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            win.set_visual(visual)
            win.set_app_paintable(True)
        
        webview = WebKit2.WebView()
        webview.set_background_color(Gdk.RGBA(0, 0, 0, 0))
        webview.load_uri("http://127.0.0.1:8765")
        
        # Permite arrastar a janela clicando na barra de título customizada
        def on_button_press(widget, event):
            if event.button == 1:
                win.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
        webview.connect("button-press-event", on_button_press)
        
        win.add(webview)
        win.connect("destroy", Gtk.main_quit)
        win.show_all()
        Gtk.main()
        
    except Exception as e:
        print(f"ERRO CRÍTICO: {e}")
        print("Para a interface nativa (Glassmorphism) funcionar, você precisa do WebKitGTK.")
        print("Rode: sudo apt install gir1.2-webkit2-4.1")
        sys.exit(1)

if __name__ == "__main__":
    main()
