"""A simple window for Idle, so you don't have to remember command-line flags.

    python -m peer.gui

The window doesn't do any networking itself. It builds the same terminal
commands you could type yourself (shown at the top of the output), runs them,
and shows what they print. It remembers your settings in gui_settings.json.

  Ask mode:  type a prompt at the bottom and press Send (or Ctrl+Enter).
  Host mode: press "Start hosting" to share this laptop's model.
"""
import codecs
import json
import os
import queue
import shlex
import socket
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from peer.config import DEFAULT_MODEL, IMAGE_EXTENSIONS, MODEL_ALLOWLIST, MODELS, RESULTS_DIR, ROOM_FILE
from peer.room import new_room_name

SETTINGS_FILE = "gui_settings.json"
PROJECT_DIR = Path(__file__).resolve().parent.parent  # the folder that contains peer/

DEFAULTS = {
    "mode": "ask",              # "ask" or "host"
    "room": "",
    "model": DEFAULT_MODEL,
    "host_name": socket.gethostname(),
    "host_fake": False,
    "send_mode": "split",       # "split" or "one"
    "one_peer": "",
    "search_wifi": True,
    "ask_fake": False,
    "extra_peers": "",
    "host_models": [DEFAULT_MODEL],  # models to download and share while hosting
}


def model_label(name: str) -> str:
    info = MODELS[name]
    kind = "text + images" if info["images"] else "text"
    return f"{info['about']} · {kind} · ~{info['size']} download"


# ---------------------------------------------------------------- settings
def load_settings() -> dict:
    settings = dict(DEFAULTS)
    try:
        saved = json.loads(Path(SETTINGS_FILE).read_text())
        settings.update({k: v for k, v in saved.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    if settings["model"] not in MODELS:  # e.g. a model that was removed from the list
        settings["model"] = DEFAULT_MODEL
    settings["host_models"] = [m for m in settings.get("host_models") or [] if m in MODELS]
    if not settings["room"]:  # fall back to the room the command-line tools saved
        try:
            settings["room"] = Path(ROOM_FILE).read_text().strip()
        except OSError:
            pass
    return settings


def save_settings(settings: dict) -> None:
    try:
        Path(SETTINGS_FILE).write_text(json.dumps(settings, indent=2))
    except OSError:
        pass


# ---------------------------------------------------------------- running commands
def pretty(cmd: list[str]) -> str:
    """How the command would look if you typed it in a terminal."""
    shown = ["python"] + cmd[1:]
    return subprocess.list2cmdline(shown) if os.name == "nt" else shlex.join(shown)


class Runner:
    """Runs one command in the background and passes its output back to the window."""

    def __init__(self, root: tk.Tk, on_text, on_exit) -> None:
        self.root, self.on_text, self.on_exit = root, on_text, on_exit
        self.proc: subprocess.Popen | None = None
        self.output = ""
        self._queue: queue.Queue = queue.Queue()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, cmd: list[str]) -> None:
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.output = ""
        self.proc = subprocess.Popen(
            cmd, cwd=PROJECT_DIR, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0, creationflags=flags,
        )
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        self.root.after(50, self._drain)

    def _read(self, proc: subprocess.Popen) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            chunk = os.read(proc.stdout.fileno(), 4096)  # whatever is available, up to 4 KB
            if not chunk:
                break
            self._queue.put(decoder.decode(chunk))
        proc.wait()
        self._queue.put(None)  # finished

    def _drain(self) -> None:
        try:
            while True:
                item = self._queue.get_nowait()
                if item is None:
                    self.on_exit(self.proc.returncode if self.proc else 0)
                    return
                self.output += item
                self.on_text(item)
        except queue.Empty:
            self.root.after(50, self._drain)

    def stop(self) -> None:
        if self.running:
            self.proc.terminate()


def open_folder(path: Path) -> None:
    path.mkdir(exist_ok=True)
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


# ---------------------------------------------------------------- the window
class IdleApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.s = load_settings()
        root.title("Idle: share AI between laptops")
        root.geometry("840x760")
        root.minsize(640, 520)

        self.v = {k: (tk.BooleanVar(value=v) if isinstance(v, bool) else tk.StringVar(value=v))
                  for k, v in self.s.items() if not isinstance(v, list)}
        # One checkbox per model in host mode ("download and share this model").
        self.host_model_vars = {m: tk.BooleanVar(value=m in self.s["host_models"]) for m in MODELS}
        for var in list(self.v.values()) + list(self.host_model_vars.values()):
            var.trace_add("write", lambda *_: self.on_settings_changed())
        self.files: list[str] = []  # attachments for the next prompt (not saved)

        self.build_top()
        self.host_frame = self.build_host()
        self.ask_frame = self.build_ask()
        self.ask_runner = Runner(root, lambda t: self.write(self.ask_log, t), self.ask_finished)
        self.find_runner = Runner(root, lambda t: self.write(self.ask_log, t), self.find_finished)
        self.host_runner = Runner(root, lambda t: self.write(self.host_log, t), self.host_finished)

        self.show_mode()
        self.update_preview()
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ----- layout ------------------------------------------------------
    def build_top(self) -> None:
        top = ttk.Frame(self.root, padding=(10, 10, 10, 0))
        top.pack(fill="x")
        ttk.Label(top, text="I want to:").pack(side="left")
        ttk.Radiobutton(top, text="Ask (send prompts)", value="ask", variable=self.v["mode"],
                        command=self.show_mode).pack(side="left", padx=8)
        ttk.Radiobutton(top, text="Host (share my model)", value="host", variable=self.v["mode"],
                        command=self.show_mode).pack(side="left")
        self.host_status = ttk.Label(top, text="", foreground="gray")
        self.host_status.pack(side="right")

        shared = ttk.LabelFrame(self.root, text="Settings (used by both modes)", padding=8)
        shared.pack(fill="x", padx=10, pady=8)
        ttk.Label(shared, text="Room:").grid(row=0, column=0, sticky="w")
        ttk.Entry(shared, textvariable=self.v["room"], width=32).grid(row=0, column=1, sticky="we", padx=4)
        ttk.Button(shared, text="New room", command=self.new_room).grid(row=0, column=2)
        ttk.Label(shared, text="Model:").grid(row=0, column=3, sticky="e", padx=(16, 4))
        ttk.Combobox(shared, textvariable=self.v["model"], values=MODEL_ALLOWLIST,
                     state="readonly", width=14).grid(row=0, column=4)
        self.model_info = ttk.Label(shared, text="", foreground="gray")
        self.model_info.grid(row=1, column=0, columnspan=5, sticky="w", pady=(4, 0))
        ttk.Label(shared, text="Everyone uses the same room name. Leave it empty to use IPs instead. "
                               "Missing models download automatically.",
                  foreground="gray").grid(row=2, column=0, columnspan=5, sticky="w", pady=(2, 0))
        shared.columnconfigure(1, weight=1)

    def build_log(self, parent) -> tk.Text:
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=(6, 0))
        text = tk.Text(frame, wrap="word", font="TkFixedFont", height=12, state="disabled")
        bar = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=bar.set)
        text.tag_configure("cmd", foreground="#1a6fd6")
        bar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        return text

    def build_host(self) -> ttk.Frame:
        f = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="Your name:").pack(side="left")
        ttk.Entry(row, textvariable=self.v["host_name"], width=20).pack(side="left", padx=4)
        ttk.Checkbutton(row, text="Fake model (testing, no Ollama)",
                        variable=self.v["host_fake"]).pack(side="left", padx=12)
        self.host_button = ttk.Button(row, text="Start hosting", command=self.toggle_host)
        self.host_button.pack(side="right")
        models = ttk.LabelFrame(f, text="Models to download (if needed) and share", padding=8)
        models.pack(fill="x", pady=(8, 0))
        for i, m in enumerate(MODELS):
            ttk.Checkbutton(models, text=f"{m}  (~{MODELS[m]['size']}"
                                         + (", images" if MODELS[m]["images"] else "") + ")",
                            variable=self.host_model_vars[m]).grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 16))
        ttk.Label(f, text="While hosting, teammates in the same room can use this laptop's models. "
                          "Keep this window open.", foreground="gray").pack(anchor="w", pady=(6, 0))
        self.host_log = self.build_log(f)
        return f

    def build_ask(self) -> ttk.Frame:
        f = ttk.Frame(self.root, padding=(10, 0, 10, 10))

        how = ttk.LabelFrame(f, text="How to send the prompt", padding=8)
        how.pack(fill="x")
        ttk.Radiobutton(how, text="Split across all laptops", value="split",
                        variable=self.v["send_mode"]).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(how, text="Also search the Wi-Fi",
                        variable=self.v["search_wifi"]).grid(row=0, column=1, sticky="w", padx=12)
        ttk.Radiobutton(how, text="Just one laptop:", value="one",
                        variable=self.v["send_mode"]).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.peer_box = ttk.Combobox(how, textvariable=self.v["one_peer"], width=34)
        self.peer_box.grid(row=1, column=1, columnspan=2, sticky="we", padx=12, pady=(6, 0))
        ttk.Button(how, text="Find laptops", command=self.find_peers).grid(row=1, column=3, pady=(6, 0))

        ttk.Label(how, text="Extra IPs:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(how, textvariable=self.v["extra_peers"]).grid(
            row=2, column=1, columnspan=2, sticky="we", padx=12, pady=(6, 0))
        ttk.Checkbutton(how, text="Fake model (testing)",
                        variable=self.v["ask_fake"]).grid(row=2, column=3, sticky="w", pady=(6, 0))
        how.columnconfigure(2, weight=1)

        files = ttk.Frame(f)
        files.pack(fill="x", pady=(6, 0))
        ttk.Button(files, text="Attach files...", command=self.attach_files).pack(side="left")
        ttk.Button(files, text="Clear", command=self.clear_files).pack(side="left", padx=4)
        self.files_label = ttk.Label(files, text="No attachments", foreground="gray")
        self.files_label.pack(side="left", padx=8)

        self.preview = ttk.Label(f, text="", foreground="gray", wraplength=780, justify="left")
        self.preview.pack(fill="x", pady=(6, 0))
        bottom = ttk.Frame(f)
        bottom.pack(side="bottom", fill="x", pady=(8, 0))  # packed first so it always stays visible
        ttk.Label(bottom, text="Prompt (Ctrl+Enter to send):").pack(anchor="w")
        self.prompt = tk.Text(bottom, height=3, wrap="word")
        self.prompt.pack(side="left", fill="x", expand=True)
        self.prompt.bind("<Control-Return>", lambda e: (self.send(), "break")[1])
        self.prompt.bind("<KeyRelease>", lambda e: self.update_preview())
        buttons = ttk.Frame(bottom)
        buttons.pack(side="right", padx=(8, 0))
        self.send_button = ttk.Button(buttons, text="Send", command=self.send)
        self.send_button.pack(fill="x")
        ttk.Button(buttons, text="Stop", command=self.ask_runner_stop).pack(fill="x", pady=2)
        ttk.Button(buttons, text="Results folder",
                   command=lambda: open_folder(PROJECT_DIR / RESULTS_DIR)).pack(fill="x")
        self.ask_log = self.build_log(f)  # fills the space between settings and prompt
        return f

    # ----- helpers -------------------------------------------------------
    def write(self, log: tk.Text, text: str, tag: str | None = None) -> None:
        log.configure(state="normal")
        log.insert("end", text, tag)
        log.see("end")
        log.configure(state="disabled")

    def write_command(self, log: tk.Text, cmd: list[str]) -> None:
        self.write(log, f"\n$ {pretty(cmd)}\n", "cmd")

    def get(self, key: str):
        value = self.v[key].get()
        return value.strip() if isinstance(value, str) else value

    def on_settings_changed(self) -> None:
        self.s = {k: self.get(k) for k in self.v}
        self.s["host_models"] = [m for m, var in self.host_model_vars.items() if var.get()]
        save_settings(self.s)
        if hasattr(self, "files_label"):
            self.refresh_files()  # also re-checks "this model can't see images"
        else:
            self.update_preview()

    # ----- attachments ---------------------------------------------------
    def attach_files(self) -> None:
        picked = filedialog.askopenfilenames(title="Attach files or images")
        self.files += [p for p in picked if p not in self.files]
        self.refresh_files()

    def clear_files(self) -> None:
        self.files = []
        self.refresh_files()

    def has_images(self) -> bool:
        return any(Path(p).suffix.lower() in IMAGE_EXTENSIONS for p in self.files)

    def refresh_files(self) -> None:
        if not self.files:
            self.files_label.configure(text="No attachments", foreground="gray")
        else:
            names = ", ".join(Path(p).name for p in self.files)
            warn = self.has_images() and not MODELS[self.get("model")]["images"]
            self.files_label.configure(
                text=names + ("   (this model can't see images: pick lfm3-omni or mimo-v2.6)" if warn else ""),
                foreground="#b00020" if warn else "black")
        self.update_preview()

    def show_mode(self) -> None:
        self.host_frame.pack_forget()
        self.ask_frame.pack_forget()
        (self.ask_frame if self.get("mode") == "ask" else self.host_frame).pack(fill="both", expand=True)
        if self.get("mode") == "ask":
            self.prompt.focus_set()

    def new_room(self) -> None:
        if self.get("room") and not messagebox.askyesno(
                "New room", "Replace the current room? Teammates will need the new name."):
            return
        self.v["room"].set(new_room_name())

    def peer_address(self) -> str:
        """'bob - 10.0.0.5:8765' -> '10.0.0.5:8765' (or whatever was typed)."""
        return self.get("one_peer").split(" - ")[-1].strip()

    # ----- commands (exactly what you'd type in a terminal) ---------------
    def base(self, module: str) -> list[str]:
        return [sys.executable, "-m", module]

    def room_args(self) -> list[str]:
        return ["--room", self.get("room")] if self.get("room") else []

    def peer_args(self) -> list[str]:
        extra = self.get("extra_peers").replace(",", " ").split()
        args = ["--peers", *extra] if extra else []
        if not self.get("search_wifi"):
            args.append("--no-discover")
        return args

    def ask_command(self, prompt: str) -> list[str]:
        if self.get("send_mode") == "one":
            cmd = self.base("peer.chat") + ["--peer", self.peer_address() or "<pick a laptop>",
                                            "--model", self.get("model"), "--prompt", prompt]
            return cmd + (["--file", *self.files] if self.files else [])
        cmd = self.base("peer.split") + [prompt, "--model", self.get("model")]
        cmd += self.room_args() + self.peer_args()
        if self.get("ask_fake"):
            cmd.append("--fake")
        return cmd + (["--file", *self.files] if self.files else [])  # --file goes last

    def host_command(self) -> list[str]:
        cmd = self.base("peer.server") + ["--name", self.get("host_name") or socket.gethostname()]
        cmd += self.room_args()
        if self.get("host_fake"):
            cmd.append("--fake")
        models = [m for m, var in self.host_model_vars.items() if var.get()]
        return cmd + (["--models", *models] if models else [])

    def update_preview(self) -> None:
        if hasattr(self, "model_info") and self.get("model") in MODELS:
            self.model_info.configure(text=model_label(self.get("model")))
        if not hasattr(self, "prompt"):
            return
        prompt = self.prompt.get("1.0", "end").strip() or "<your prompt>"
        shown = [Path(a).name if a in self.files else a for a in self.ask_command(prompt)]
        self.preview.configure(text="Will run:  " + pretty(shown))
        if hasattr(self, "host_runner"):
            self.refresh_host_status()

    # ----- ask mode --------------------------------------------------------
    def send(self) -> None:
        prompt = self.prompt.get("1.0", "end").strip()
        if not prompt:
            return
        if self.ask_runner.running:
            messagebox.showinfo("Busy", "Still working on the last prompt. Press Stop to cancel it.")
            return
        if self.get("send_mode") == "one" and not self.peer_address():
            messagebox.showinfo("Pick a laptop", "Press 'Find laptops' or type an IP address first.")
            return
        if self.has_images() and not MODELS[self.get("model")]["images"]:
            messagebox.showinfo("Model can't see images",
                                f"{self.get('model')} only reads text. Pick a model that can see images "
                                "(" + ", ".join(m for m in MODELS if MODELS[m]["images"]) + ") or remove the images.")
            return
        cmd = self.ask_command(prompt)
        self.write_command(self.ask_log, cmd)
        self.send_button.configure(text="Working...", state="disabled")
        self.ask_runner.start(cmd)

    def ask_finished(self, code: int) -> None:
        self.send_button.configure(text="Send", state="normal")
        if code == 0:
            self.prompt.delete("1.0", "end")
            self.update_preview()

    def ask_runner_stop(self) -> None:
        if self.ask_runner.running:
            self.ask_runner.stop()
            self.write(self.ask_log, "\n(stopped)\n")

    def find_peers(self) -> None:
        if self.find_runner.running:
            return
        cmd = self.base("peer.split") + ["--list", "--model", self.get("model")] + self.room_args() + self.peer_args()
        self.write_command(self.ask_log, cmd)
        self.find_runner.start(cmd)

    def find_finished(self, code: int) -> None:
        for line in self.find_runner.output.splitlines():
            if line.startswith("PEERS_JSON "):
                peers = json.loads(line[len("PEERS_JSON "):])
                values = [f"{p['name']} - {p['address']}" for p in peers]
                self.peer_box.configure(values=values)
                if values and not self.get("one_peer"):
                    self.v["one_peer"].set(values[0])

    # ----- host mode ------------------------------------------------------
    def toggle_host(self) -> None:
        if self.host_runner.running:
            self.host_runner.stop()
            return
        cmd = self.host_command()
        self.write_command(self.host_log, cmd)
        self.host_runner.start(cmd)
        self.host_button.configure(text="Stop hosting")
        self.refresh_host_status()

    def host_finished(self, code: int) -> None:
        self.write(self.host_log, "\n(hosting stopped)\n")
        self.host_button.configure(text="Start hosting")
        self.refresh_host_status()

    def refresh_host_status(self) -> None:
        if self.host_runner.running:
            self.host_status.configure(text=f"● Hosting as {self.get('host_name')}", foreground="green")
        else:
            self.host_status.configure(text="○ Not hosting", foreground="gray")

    def on_close(self) -> None:
        for r in (self.ask_runner, self.find_runner, self.host_runner):
            r.stop()
        self.root.destroy()


def main() -> None:
    os.chdir(PROJECT_DIR)  # settings, room.txt and results/ live next to the code
    root = tk.Tk()
    if sys.platform.startswith("linux"):
        ttk.Style().theme_use("clam")  # the default Linux theme looks dated
    IdleApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
