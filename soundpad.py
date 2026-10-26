#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import urllib.error
import uuid

CONFIG_PATH = os.path.expanduser("~/soundpad/config.json")
CACHE_DIR = os.path.expanduser("~/.cache/soundpad")
os.makedirs(CACHE_DIR, exist_ok=True)
RENDER_FILE = os.path.join(CACHE_DIR, "_render.wav")

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

# ========== STORAGE ==========
class WorkerStorage:
    def __init__(self, cfg):
        self.base = cfg["worker_url"].rstrip("/")

    def list_sounds(self):
        with urllib.request.urlopen(f"{self.base}/sounds", timeout=15) as r:
            return json.loads(r.read())

    def download(self, name, dest):
        url = f"{self.base}/play/{urllib.parse.quote(name)}"
        urllib.request.urlretrieve(url, dest)
        return dest

    def upload(self, path):
        with open(path, "rb") as f:
            data = f.read()
        fname = os.path.basename(path)
        boundary = uuid.uuid4().hex
        body = b""
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="file"; filename="{fname}"\r\n'.encode()
        body += b"Content-Type: application/octet-stream\r\n\r\n"
        body += data + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            f"{self.base}/upload", data=body, method="POST",
        )
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())

# ========== AUDIO ==========
class AudioEngine:
    def __init__(self):
        self.volume = 0.7
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
        self.has_rubberband = False
        if self.has_ffmpeg:
            try:
                out = subprocess.run(
                    ["ffmpeg", "-hide_banner", "-filters"],
                    capture_output=True, text=True, timeout=5,
                )
                self.has_rubberband = "rubberband" in out.stdout
            except Exception:
                pass

    def build_filter(self):
        parts = []
        if self.pitch != 0:
            ratio = 2 ** (self.pitch / 12)
            if self.has_rubberband:
                parts.append(f"rubberband=pitch={ratio:.4f}")
            else:
                parts.append("asetrate=44100*{:.4f}".format(ratio))
                parts.append("aresample=44100")
                parts.append("atempo={:.4f}".format(1 / ratio))
        if self.speed != 1.0:
            parts.append(f"atempo={self.speed:.4f}")
        if self.bass != 0:
            parts.append(f"bass=g={self.bass}")
        if self.treble != 0:
            parts.append(f"treble=g={self.treble}")
        return ",".join(parts) if parts else None

    def play(self, path):
        af = self.build_filter()
        if af and self.has_ffmpeg:
            subprocess.run(
                ["ffmpeg", "-y", "-i", path, "-af", af, "-f", "wav", RENDER_FILE],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20,
            )
            self._raw(RENDER_FILE)
        else:
            self._raw(path)

    def _raw(self, path):
        if self.player == "pw-play":
            subprocess.Popen(["pw-play", f"--volume={self.volume}", path])
        elif self.player == "paplay":
            vol = int(self.volume * 65536)
            subprocess.Popen(["paplay", f"--volume={vol}", path])
        elif self.player == "aplay":
            subprocess.Popen(["aplay", "-q", path])

# ========== PROBE ==========
def probe_duration(path):
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            stderr=subprocess.DEVNULL, timeout=10,
        )
        return float(out.strip())
    except Exception:
        return None

# ========== UI GTK ==========
def run_gtk(cfg):
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, GLib

    AppIndicator = None
    try:
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3 as AppIndicator
    except Exception:
        try:
            gi.require_version("AppIndicator3", "0.1")
            from gi.repository import AppIndicator3 as AppIndicator
        except Exception:
            AppIndicator = None

    storage = WorkerStorage(cfg)
    audio = AudioEngine()
    audio.volume = cfg.get("volume", 0.7)

    state = {"sounds": [], "cache": {}, "indicator": None}

    class Win(Gtk.Window):
        def __init__(self):
            super().__init__(title="Phantom Soundpad")
            self.set_default_size(780, 580)
            self.connect("delete-event", self._hide)

            hb = Gtk.HeaderBar()
            hb.set_show_close_button(True)
            hb.props.title = "Phantom Soundpad"
            self.set_titlebar(hb)

            nb = Gtk.Notebook()
            self.add(nb)
            nb.append_page(self._tab_sons(), Gtk.Label(label="Sons"))
            nb.append_page(self._tab_som(), Gtk.Label(label="Som"))
            self.show_all()

        def _hide(self, *a):
            self.hide()
            return True

        def _tab_sons(self):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.set_border_width(8)

            bar = Gtk.Box(spacing=6)
            self.search = Gtk.SearchEntry()
            self.search.set_placeholder_text("Pesquisar som...")
            self.search.connect("search-changed", lambda e: self._render())
            bar.pack_start(self.search, True, True, 0)

            rb = Gtk.Button(label="Atualizar")
            rb.connect("clicked", lambda b: self._refresh())
            bar.pack_start(rb, False, False, 0)

            ub = Gtk.Button(label="+ Adicionar")
            ub.connect("clicked", self._upload)
            bar.pack_start(ub, False, False, 0)
            box.pack_start(bar, False, False, 0)

            sw = Gtk.ScrolledWindow()
            sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            self.listbox = Gtk.ListBox()
            self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
            sw.add(self.listbox)
            box.pack_start(sw, True, True, 0)
            return box

        def _tab_som(self):
            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            outer.set_border_width(20)

            self.sliders = {}
            specs = [
                ("volume", "Volume", 0, 100, 1, cfg.get("volume", 0.7) * 100, "{}%"),
                ("pitch", "Pitch", -12, 12, 1, 0, "{:+.0f} semitons"),
                ("speed", "Velocidade", 50, 200, 1, 100, "{:.0f}%"),
                ("bass", "Graves", -20, 20, 1, 0, "{:+.0f} dB"),
                ("treble", "Agudos", -20, 20, 1, 0, "{:+.0f} dB"),
            ]
            for key, label, mn, mx, step, default, fmt in specs:
                row = Gtk.Box(spacing=8)
                lbl = Gtk.Label(label=label)
                lbl.set_width_chars(16)
                lbl.set_xalign(0)
                row.pack_start(lbl, False, False, 0)

                sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, mn, mx, step)
                sc.set_value(default)
                sc.set_hexpand(True)
                sc.set_draw_value(False)
                row.pack_start(sc, True, True, 0)

                vl = Gtk.Label(label=fmt.format(default))
                vl.set_width_chars(14)
                vl.set_xalign(1)
                row.pack_start(vl, False, False, 0)

                sc.connect("value-changed", self._on_slider, key, vl, fmt)
                self.sliders[key] = (sc, vl, fmt, default)
                outer.pack_start(row, False, False, 0)

            reset = Gtk.Button(label="Reset — restaurar padrões")
            reset.connect("clicked", self._reset)
            outer.pack_start(reset, False, False, 14)
            return outer

        def _on_slider(self, sc, key, vl, fmt):
            v = sc.get_value()
            vl.set_text(fmt.format(v))
            if key == "volume":
                audio.volume = v / 100
                cfg["volume"] = audio.volume
                save_config(cfg)
            elif key == "pitch":
                audio.pitch = v
            elif key == "speed":
                audio.speed = v / 100
            elif key == "bass":
                audio.bass = v
            elif key == "treble":
                audio.treble = v

        def _reset(self, b):
            for key, (sc, vl, fmt, default) in self.sliders.items():
                sc.set_value(default)

        def _refresh(self):
            def work():
                try:
                    state["sounds"] = storage.list_sounds()
                except Exception as e:
                    GLib.idle_add(self._err, f"Erro ao listar: {e}")
                    return
                GLib.idle_add(self._render)
            threading.Thread(target=work, daemon=True).start()

        def _render(self):
            for c in self.listbox.get_children():
                self.listbox.remove(c)
            q = self.search.get_text().lower()
            flt = [s for s in state["sounds"] if q in s["name"].lower()] if q else state["sounds"]
            if not flt:
                lbl = Gtk.Label(label="Nenhum som encontrado.")
                lbl.set_margin_top(20)
                self.listbox.add(lbl)
                self.listbox.show_all()
                return
            for s in flt:
                row = Gtk.ListBoxRow()
                hb = Gtk.Box(spacing=8)
                hb.set_border_width(6)
                pb = Gtk.Button(label="▶ Tocar")
                pb.connect("clicked", lambda b, snd=s: self._play(snd))
                hb.pack_start(pb, False, False, 0)
                nm = Gtk.Label(label=s["name"].rsplit(".", 1)[0])
                nm.set_xalign(0)
                hb.pack_start(nm, True, True, 0)
                row.add(hb)
                self.listbox.add(row)
            self.listbox.show_all()

        def _play(self, sound):
            name = sound["name"]
            try:
                if name in state["cache"] and os.path.exists(state["cache"][name]):
                    path = state["cache"][name]
                else:
                    path = os.path.join(CACHE_DIR, name)
                    storage.download(name, path)
                    state["cache"][name] = path
                audio.play(path)
            except Exception as e:
                self._err(f"Erro: {e}")

        def _upload(self, b):
            d = Gtk.FileChooserDialog(
                title="Escolher som (máx 10s)", transient_for=self,
                action=Gtk.FileChooserAction.OPEN,
            )
            d.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                          Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
            f = Gtk.FileFilter()
            f.set_name("Áudio")
            for p in ["*.mp3","*.wav","*.ogg","*.flac","*.m4a","*.opus"]:
                f.add_pattern(p)
            d.add_filter(f)
            resp = d.run()
            path = d.get_filename()
            d.destroy()
            if resp != Gtk.ResponseType.OK or not path:
                return
            dur = probe_duration(path)
            if dur is not None and dur > 10.5:
                self._err(f"Muito longo: {dur:.1f}s (máx 10s).")
                return
            try:
                storage.upload(path)
                self._refresh()
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors="ignore")[:200]
                self._err(f"Upload falhou {e.code}: {body}")
            except Exception as e:
                self._err(f"Upload falhou: {e}")

        def _err(self, msg):
            d = Gtk.MessageDialog(
                transient_for=self, flags=0,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK, text=msg,
            )
            d.run(); d.destroy()

    win = Win()

    if AppIndicator is not None:
        ind = AppIndicator.Indicator.new(
            "phantom-soundpad", "audio-x-generic",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        menu = Gtk.Menu()
        show = Gtk.MenuItem(label="Mostrar painel")
        show.connect("activate", lambda w: (win.present(), win.deiconify()))
        menu.append(show)
        quit_i = Gtk.MenuItem(label="Sair")
        quit_i.connect("activate", lambda w: Gtk.main_quit())
        menu.append(quit_i)
        menu.show_all()
        ind.set_menu(menu)
        state["indicator"] = ind
        GLib.timeout_add(1500, win.hide)

    win._refresh()
    Gtk.main()

# ========== FALLBACK WEB ==========
def run_web(cfg):
    from http.server import HTTPServer, BaseHTTPRequestHandler
    storage = WorkerStorage(cfg)
    audio = AudioEngine()
    audio.volume = cfg.get("volume", 0.7)
    state = {"sounds": [], "cache": {}}

    HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Soundpad</title>
<style>body{font-family:sans-serif;background:#1e1e1e;color:#ddd;padding:20px;margin:0}
.tabs{display:flex;gap:4px}.tab{padding:8px 18px;background:#333;cursor:pointer;border-radius:6px 6px 0 0}
.tab.active{background:#555}.panel{display:none;background:#2a2a2a;padding:16px;border-radius:0 6px 6px 6px}
.panel.active{display:block}input[type=range]{width:100%}button{padding:6px 14px;margin:2px;background:#444;color:#eee;border:none;border-radius:4px;cursor:pointer}
#search{width:100%;padding:8px;margin-bottom:12px;background:#333;border:1px solid #555;color:#eee}
.sound{padding:8px;background:#3a3a3a;margin:4px 0;cursor:pointer}.sound:hover{background:#4a4a4a}
</style></head><body>
<div class="tabs">
<div class="tab active" onclick="tab('sons',this)">Sons</div>
<div class="tab" onclick="tab('som',this)">Som</div></div>
<div id="sons" class="panel active">
<input id="search" placeholder="Pesquisar..." oninput="render()">
<div id="list"></div></div>
<div id="som" class="panel">
<p>Volume <span id="v0">70%</span></p><input type="range" min="0" max="100" value="70" oninput="setV('volume',this.value)">
<p>Pitch <span id="v1">0</span></p><input type="range" min="-12" max="12" value="0" oninput="setV('pitch',this.value)">
<p>Velocidade <span id="v2">100%</span></p><input type="range" min="50" max="200" value="100" oninput="setV('speed',this.value)">
<p>Graves <span id="v3">0</span></p><input type="range" min="-20" max="20" value="0" oninput="setV('bass',this.value)">
<p>Agudos <span id="v4">0</span></p><input type="range" min="-20" max="20" value="0" oninput="setV('treble',this.value)">
<button onclick="reset()">Reset</button></div>
<script>
let sounds=[];
function tab(id,el){document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
document.getElementById(id).classList.add('active');el.classList.add('active');}
function render(){const q=document.getElementById('search').value.toLowerCase();
const l=document.getElementById('list');l.innerHTML='';
sounds.filter(s=>s.name.toLowerCase().includes(q)).forEach(s=>{
const d=document.createElement('div');d.className='sound';
d.textContent='▶ '+s.name.replace(/\\.[^.]+$/,'');
d.onclick=()=>fetch('/play',{method:'POST',body:s.name});l.appendChild(d);});}
function setV(k,v){document.getElementById('v'+['volume','pitch','speed','bass','treble'].indexOf(k)).textContent=v;
fetch('/set',{method:'POST',body:k+'='+v});}
function reset(){['70','0','100','0','0'].forEach((v,i)=>setV(['volume','pitch','speed','bass','treble'][i],v));
document.querySelectorAll('#som input').forEach((e,i)=>e.value=['70','0','100','0','0'][i]);}
fetch('/sounds').then(r=>r.json()).then(d=>{sounds=d;render();});
</script></body></html>"""

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            if self.path == "/":
                self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.end_headers()
                self.wfile.write(HTML.encode())
            elif self.path == "/sounds":
                try: state["sounds"] = storage.list_sounds()
                except Exception: state["sounds"] = []
                self.send_response(200); self.send_header("Content-Type","application/json"); self.end_headers()
                self.wfile.write(json.dumps(state["sounds"]).encode())
            else: self.send_response(404); self.end_headers()
        def do_POST(self):
            n = int(self.headers.get("Content-Length",0))
            body = self.rfile.read(n).decode()
            if self.path == "/play":
                for s in state["sounds"]:
                    if s["name"] == body:
                        nm = s["name"]
                        p = state["cache"].get(nm) or os.path.join(CACHE_DIR, nm)
                        if not os.path.exists(p): storage.download(nm, p)
                        state["cache"][nm] = p
                        audio.play(p); break
            elif self.path == "/set":
                k, v = body.split("=")
                v = float(v)
                if k == "volume": audio.volume = v/100; cfg["volume"]=audio.volume; save_config(cfg)
                elif k == "pitch": audio.pitch = v
                elif k == "speed": audio.speed = v/100
                elif k == "bass": audio.bass = v
                elif k == "treble": audio.treble = v
            self.send_response(200); self.end_headers()

    port = 8765
    print(f"http://127.0.0.1:{port}")
    import webbrowser
    webbrowser.open(f"http://127.0.0.1:{port}")
    HTTPServer(("127.0.0.1", port), H).serve_forever()

# ========== BOOT ==========
if __name__ == "__main__":
    cfg = load_config()
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        run_gtk(cfg)
    except Exception as e:
        print(f"GTK indisponível ({e}). Caindo para web...")
        run_web(cfg)
