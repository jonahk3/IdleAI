"""Local bridge for the Idle web app (the Lovable UI).

    python -m peer.api

A web page can't start programs on your device, so this small server runs on YOUR
device and the web app talks to it at http://127.0.0.1:8800. It doesn't do any AI
itself: like the desktop GUI (peer.gui), it runs the same commands you could type
(peer.server, peer.split, peer.chat, peer.check) and passes their output back.

Safety:
  - it only listens on 127.0.0.1, so other devices can't reach it;
  - it only answers the Idle web app (by web address) and pages on this device;
  - every request needs the secret key printed when it starts (the link it prints
    includes it), so other websites can't control it.
"""
import argparse
import base64
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from peer import attachments, carbon, ollama
from peer.config import (API_ALLOWED_ORIGINS, API_PORT, COORDINATOR_MODEL, MODELS, RESULTS_DIR,
                         STARTING_CREDITS, UI_URL)
from peer.room import new_room_name
from peer.settings import PROJECT_DIR, load_settings, save_settings

UPLOAD_DIR = PROJECT_DIR / "uploads"   # files the web app attaches (deleted when you clear them)
MAX_UPLOAD_BYTES = 30_000_000


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- running commands
class Job:
    """One command running in the background (same as the desktop GUI's runner)."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.proc: subprocess.Popen | None = None
        self.output = ""
        self.command = ""
        self.exit_code: int | None = None
        self.lock = threading.Lock()
        self.on_line = None  # called with each complete output line

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, cmd: list[str]) -> None:
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        shown = ["python"] + cmd[1:]
        self.command = subprocess.list2cmdline(shown) if os.name == "nt" else shlex.join(shown)
        with self.lock:
            self.output, self.exit_code = f"$ {self.command}\n", None
        log(f"running: {self.command}")
        self.proc = subprocess.Popen(cmd, cwd=PROJECT_DIR, env=env, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0,
                                     creationflags=flags)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc: subprocess.Popen) -> None:
        import codecs
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        while True:
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                break
            # Windows ends lines with \r\n: drop the \r so the output reads the same everywhere
            text = decoder.decode(chunk).replace("\r", "")
            with self.lock:
                self.output += text
            pending += text
            *lines, pending = pending.split("\n")
            for line in lines:
                if self.on_line:
                    self.on_line(line)
        proc.wait()
        with self.lock:
            self.exit_code = proc.returncode

    def wait(self, timeout: float) -> None:
        if self.proc:
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                pass
        time.sleep(0.2)  # let the reader thread catch up

    def stop(self) -> None:
        if self.running:
            self.proc.terminate()

    def snapshot(self, since: int = 0) -> dict:
        with self.lock:
            return {"text": self.output[since:], "next": len(self.output), "running": self.running,
                    "exit_code": self.exit_code, "command": self.command}


# ---------------------------------------------------------------- reading the commands' output
def models_used(output: str) -> list[dict]:
    """Which model ran each step, from the [model] lines (same as the GUI's debug bar)."""
    steps: dict[str, dict] = {}
    for line in output.splitlines():
        if "[model]" not in line:
            continue
        if m := re.search(r"plan on this device: (\S+)", line):
            steps["plan"] = {"step": "plan", "device": "this device", "model": m.group(1), "done": True}
        elif m := re.search(r"part (\d+) -> (.+?) \[carbon\] .*?\[model\] (\S+?):", line):
            steps[f"part {m.group(1)}"] = {"step": f"part {m.group(1)}", "device": m.group(2), "model": m.group(3), "done": False}
        elif m := re.search(r"part (\d+) done by (.+?) in [\d.]+s \[model\] (\S+)", line):
            steps[f"part {m.group(1)}"] = {"step": f"part {m.group(1)}", "device": m.group(2), "model": m.group(3), "done": True}
        elif m := re.search(r"combine on this device: (\S+)", line):
            steps["combine"] = {"step": "combine", "device": "this device", "model": m.group(1), "done": True}
        elif m := re.search(r"Using (\S+) on '(.+?)'", line):
            steps["answer"] = {"step": "answer", "device": m.group(2), "model": m.group(1), "done": False}
        elif m := re.search(r"s on (.+?), \[model\] (\S+?)\)", line):
            steps["answer"] = {"step": "answer", "device": m.group(1), "model": m.group(2), "done": True}
    return list(steps.values())


def routing(output: str) -> list[dict]:
    """Where each part went and how clean that device's grid is, from the [carbon] lines."""
    out = []
    for m in re.finditer(r"part (\d+) -> (.+?) \[carbon\] (\S+) \(([\d.]+) g/kWh\)", output):
        out.append({"part": int(m.group(1)), "device": m.group(2), "location": m.group(3),
                    "g_co2_per_kwh": float(m.group(4))})
    return out


def final_answer(output: str) -> str | None:
    """The final answer printed by peer.split, or the reply printed by peer.chat --prompt."""
    m = re.search(r"## Final answer\n\n?(.*?)\n\n?={20,}", output, re.S)
    if m:
        return m.group(1).strip()
    m = re.search(r"\nyou> .*?\n(.+?)> (.*?)\n   \(\d", output, re.S)
    return m.group(2).strip() if m else None


def co2_summary(output: str) -> dict | None:
    m = re.search(r"\[carbon\] about ([\d.]+) g CO2 .*? about ([\d.]+) g", output)
    return {"grams": float(m.group(1)), "dirtiest_grams": float(m.group(2))} if m else None


# ---------------------------------------------------------------- the bridge
class Bridge:
    def __init__(self, token: str) -> None:
        self.token = token
        self.host = Job("host")
        self.ask = Job("ask")
        self.earned = 0.0   # credits for this session only (reset every time the bridge starts)
        self.spent = 0.0
        self.peers: list[dict] = []
        self.files: dict[str, dict] = {}   # id -> {"id", "name", "path", "summary", "error"}
        self.host.on_line = self.ask.on_line = self.count_credits
        # Slow checks (is Ollama running? is PDF support installed?) run in the background and
        # are only READ by state(), so a slow answer can never freeze the app.
        self.status = {"ollama_running": False, "pdf_supported": attachments.pdf_supported()}
        threading.Thread(target=self._watch_status, daemon=True).start()

    def _watch_status(self) -> None:
        while True:
            self.status["ollama_running"] = ollama.ollama_running()
            self.status["pdf_supported"] = attachments.pdf_supported()
            time.sleep(5)

    def count_credits(self, line: str) -> None:
        if m := re.search(r"\[credits\] (earned|spent) ([\d.]+) credits", line):
            if m.group(1) == "earned":
                self.earned += float(m.group(2))
            else:
                self.spent += float(m.group(2))

    # ----- commands (the same ones the desktop GUI builds) -----
    @staticmethod
    def base(module: str) -> list[str]:
        return [sys.executable, "-m", module]

    @staticmethod
    def common(s: dict) -> list[str]:
        args = ["--room", s["room"]] if s.get("room") else []
        return args + (["--location", s["location"]] if s.get("location") else [])

    @staticmethod
    def peer_args(s: dict) -> list[str]:
        extra = (s.get("extra_peers") or "").replace(",", " ").split()
        return (["--peers", *extra] if extra else []) + ([] if s.get("search_wifi", True) else ["--no-discover"])

    def file_paths(self) -> list[str]:
        return [f["path"] for f in self.files.values() if not f.get("error")]

    def ask_command(self, s: dict, prompt: str) -> list[str]:
        # Always split: the planner decides how many devices (1 or more), and carbon-aware
        # routing picks which ones.
        files = self.file_paths()
        cmd = self.base("peer.split") + [prompt, "--model", s["model"]] + self.common(s) + self.peer_args(s)
        return cmd + (["--file", *files] if files else [])

    def host_command(self, s: dict) -> list[str]:
        cmd = self.base("peer.server") + ["--name", s.get("host_name") or "device"] + self.common(s)
        models = [m for m in s.get("host_models") or [] if m in MODELS]
        return cmd + (["--models", *models] if models else [])

    # ----- what the web app shows -----
    def state(self) -> dict:
        s = load_settings()
        has_images = any(Path(f["name"]).suffix.lower() in attachments.IMAGE_EXTENSIONS
                         for f in self.files.values())
        warning = None
        if has_images and not MODELS[s["model"]]["images"]:
            warning = f"{s['model']} can't see images: pick " + " or ".join(m for m in MODELS if MODELS[m]["images"])
        return {
            "settings": s,
            "models": [{"name": m, **info} for m, info in MODELS.items()],
            "coordinator_model": COORDINATOR_MODEL,
            "locations": [{"code": c, **info} for c, info in carbon.provinces().items()],
            "credits": {"start": STARTING_CREDITS, "earned": round(self.earned, 2), "spent": round(self.spent, 2),
                        "balance": round(STARTING_CREDITS + self.earned - self.spent, 2)},
            "hosting": {"running": self.host.running, "command": self.host.command},
            "asking": {"running": self.ask.running, "command": self.ask.command},
            "peers": self.peers,
            "attachments": list(self.files.values()),
            "attachment_warning": warning,
            "pdf_supported": self.status["pdf_supported"],
            "ollama_running": self.status["ollama_running"],
        }

    def ask_result(self, since: int) -> dict:
        snap = self.ask.snapshot(since)
        full = self.ask.output
        snap.update({"models_used": models_used(full), "routing": routing(full),
                     "co2": co2_summary(full), "final_answer": None if self.ask.running else final_answer(full)})
        m = re.search(r"Saved to (\S+\.md)", full)
        snap["results_file"] = Path(m.group(1)).name if m else None
        return snap

    # ----- actions -----
    def find_peers(self, s: dict) -> dict:
        job = Job("find")
        job.start(self.base("peer.split") + ["--list", "--model", s["model"]] + self.common(s) + self.peer_args(s))
        job.wait(60)
        m = re.search(r"PEERS_JSON (.*)", job.output)
        self.peers = [{**p, "label": f"{p['name']} ({carbon.label(p.get('location'))}) - {p['address']}"}
                      for p in (json.loads(m.group(1)) if m else [])]
        return {"peers": self.peers, "log": job.output}

    def check(self, address: str) -> dict:
        job = Job("check")
        job.start(self.base("peer.check") + [address])
        job.wait(30)
        return {"log": job.output}

    def add_file(self, name: str, data_b64: str) -> dict:
        return self.add_file_bytes(name, base64.b64decode(data_b64))

    def add_file_bytes(self, name: str, data: bytes) -> dict:
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError(f"{name} is too big (max {MAX_UPLOAD_BYTES // 1_000_000} MB)")
        file_id = uuid.uuid4().hex[:12]
        safe = re.sub(r"[^\w.\- ()]", "_", Path(name).name) or "file"
        folder = UPLOAD_DIR / file_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / safe
        path.write_bytes(data)
        entry = {"id": file_id, "name": safe, "path": str(path), "summary": safe, "error": None}
        try:
            entry["summary"] = attachments.load([str(path)]).summary[0]
        except ValueError as e:
            entry["error"] = str(e)
        self.files[file_id] = entry
        return entry

    def start_ask(self, prompt: str) -> None:
        """Start a prompt with the saved settings. Raises ValueError with a friendly message."""
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("Type a prompt first.")
        if self.ask.running:
            raise ValueError("Still working on the last prompt. Stop it first.")
        problems = [f["error"] for f in self.files.values() if f.get("error")]
        if problems:
            raise ValueError(problems[0])
        warning = self.state()["attachment_warning"]
        if warning:
            raise ValueError(warning[0].upper() + warning[1:])
        self.ask.start(self.ask_command(load_settings(), prompt))

    def install_pdf(self) -> dict:
        job = Job("pip")
        job.start([sys.executable, "-m", "pip", "install", "pypdf"])
        job.wait(300)
        self.recheck_files()
        self.status["pdf_supported"] = attachments.pdf_supported()
        return {"ok": job.exit_code == 0, "log": job.output, "pdf_supported": self.status["pdf_supported"]}

    def remove_file(self, file_id: str) -> None:
        entry = self.files.pop(file_id, None)
        if entry:
            try:
                Path(entry["path"]).unlink()
                Path(entry["path"]).parent.rmdir()
            except OSError:
                pass

    def recheck_files(self) -> None:
        for entry in self.files.values():
            try:
                entry["summary"], entry["error"] = attachments.load([entry["path"]]).summary[0], None
            except ValueError as e:
                entry["error"] = str(e)


class Handler(BaseHTTPRequestHandler):
    bridge: Bridge

    def log_message(self, format, *args) -> None:
        pass

    # ----- CORS: only the Idle web app (and pages on this device) may talk to the bridge -----
    def allowed_origin(self) -> str | None:
        origin = self.headers.get("Origin")
        if origin and re.match(API_ALLOWED_ORIGINS, origin):
            return origin
        return None

    def cors(self) -> None:
        origin = self.allowed_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Idle-Token")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            # Chrome asks before a public website may talk to something on this device:
            self.send_header("Access-Control-Allow-Private-Network", "true")

    def send_json(self, status: int, obj) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204 if self.allowed_origin() else 403)
        self.cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def authorised(self) -> bool:
        if self.headers.get("Origin") and not self.allowed_origin():
            self.send_json(403, {"error": "this website isn't allowed to use the Idle bridge"})
            return False
        if not secrets.compare_digest(self.headers.get("X-Idle-Token", ""), self.bridge.token):
            self.send_json(401, {"error": "wrong or missing key: open the link the bridge printed"})
            return False
        return True

    def body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_UPLOAD_BYTES * 2:
            raise ValueError("request too big")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        self.route("GET")

    def do_POST(self) -> None:
        self.route("POST")

    def do_DELETE(self) -> None:
        self.route("DELETE")

    def route(self, method: str) -> None:
        path, _, query = self.path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        if method == "GET" and path == "/api/hello":  # no key needed: lets the app see the bridge is running
            self.send_json(200, {"app": "idle", "version": 1})
            return
        if not self.authorised():
            return
        b = self.bridge
        try:
            if method == "GET" and path == "/api/state":
                self.send_json(200, b.state())
            elif method == "POST" and path == "/api/settings":
                s = load_settings()
                s.update({k: v for k, v in self.body().items() if k in s})
                save_settings(s)
                self.send_json(200, load_settings())
            elif method == "POST" and path == "/api/room/new":
                s = load_settings()
                s["room"] = new_room_name()
                save_settings(s)
                self.send_json(200, {"room": s["room"]})
            elif method == "POST" and path == "/api/host/start":
                if not b.host.running:
                    b.host.start(b.host_command(load_settings()))
                self.send_json(200, b.host.snapshot())
            elif method == "POST" and path == "/api/host/stop":
                b.host.stop()
                self.send_json(200, {"stopped": True})
            elif method == "GET" and path == "/api/host/log":
                self.send_json(200, b.host.snapshot(int(params.get("since", 0))))
            elif method == "POST" and path == "/api/ask":
                b.start_ask(str(self.body().get("prompt", "")))
                self.send_json(200, b.ask_result(0))
            elif method == "GET" and path == "/api/ask/log":
                self.send_json(200, b.ask_result(int(params.get("since", 0))))
            elif method == "POST" and path == "/api/ask/stop":
                b.ask.stop()
                self.send_json(200, {"stopped": True})
            elif method == "POST" and path == "/api/peers/find":
                self.send_json(200, b.find_peers(load_settings()))
            elif method == "POST" and path == "/api/check":
                address = str(self.body().get("address", "")).strip()
                if not address:
                    raise ValueError("Give the device's IP address.")
                self.send_json(200, b.check(address))
            elif method == "POST" and path == "/api/files":
                data = self.body()
                self.send_json(200, b.add_file(str(data.get("name", "file")), str(data.get("data_base64", ""))))
            elif method == "DELETE" and path.startswith("/api/files/"):
                b.remove_file(path.rsplit("/", 1)[-1])
                self.send_json(200, {"attachments": list(b.files.values())})
            elif method == "POST" and path == "/api/pdf/install":
                self.send_json(200, b.install_pdf())
            elif method == "GET" and path == "/api/results":
                folder = PROJECT_DIR / RESULTS_DIR
                files = sorted(folder.glob("*.md"), reverse=True)[:30] if folder.is_dir() else []
                self.send_json(200, {"results": [f.name for f in files]})
            elif method == "GET" and path.startswith("/api/results/"):
                name = Path(path.rsplit("/", 1)[-1]).name
                file = PROJECT_DIR / RESULTS_DIR / name
                if not name.endswith(".md") or not file.is_file():
                    self.send_json(404, {"error": "no such result"})
                else:
                    self.send_json(200, {"name": name, "markdown": file.read_text(encoding="utf-8")})
            else:
                self.send_json(404, {"error": f"unknown endpoint {method} {path}"})
        except (ValueError, KeyError, TypeError) as e:
            self.send_json(400, {"error": str(e)})
        except Exception as e:  # never just hang up on the web app
            log(f"ERROR handling {method} {path}: {e!r}")
            self.send_json(500, {"error": f"bridge error: {e!r}"})


def main() -> None:
    parser = argparse.ArgumentParser(description="Local bridge for the Idle web app.")
    parser.add_argument("--port", type=int, default=API_PORT)
    parser.add_argument("--ui", default=UI_URL, help="web address of the Idle web app")
    args = parser.parse_args()
    os.chdir(PROJECT_DIR)  # settings, rooms and results live next to the code
    token = secrets.token_urlsafe(24)
    Handler.bridge = bridge = Bridge(token)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as e:
        raise SystemExit(f"Can't start the bridge on port {args.port} ({e}). Is it already running?")
    server.daemon_threads = True
    link = f"{args.ui}#bridge=http://127.0.0.1:{args.port}&key={token}"
    log(f"Idle bridge running on http://127.0.0.1:{args.port} (only this device can reach it).")
    log("Open the Idle app with this link (it includes your secret key):")
    print(f"\n    {link}\n", flush=True)
    log("Keep this window open while you use the app. Press Ctrl+C to stop (also stops hosting).")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        bridge.host.stop()
        bridge.ask.stop()
        log("Bridge stopped.")


if __name__ == "__main__":
    main()
