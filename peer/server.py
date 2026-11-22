"""Peer server: lets other laptops on the same network chat with YOUR Ollama.

Run it on the laptop that will lend its computer:

    python -m peer.server --name alice

It listens on port 8765 and forwards chat messages to the Ollama running on
this laptop. Only models in MODEL_ALLOWLIST are allowed, and requests are
treated as plain text data (nothing from the network is ever executed).
"""
import argparse
import json
import os
import socket
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from peer.config import MAX_IMAGE_BYTES, MAX_IMAGES, MODEL_ALLOWLIST, MODELS, PEER_PORT
from peer.discovery import local_ip, start_responder
from peer.ollama import chat_stream, installed_models, pull, supports_images, warm_up
from peer.room import resolve_room, start_announcer
from peer import carbon, credits

ALLOWED_ROLES = {"system", "user", "assistant"}
MAX_MESSAGES = 100
MAX_CHARS_PER_MESSAGE = 400_000           # room for attached text files
MAX_BODY_BYTES = 120_000_000               # whole request, including images

# Downloads in progress on this laptop: {model: percent or None}
PULLING: dict[str, int | None] = {}
PULL_LOCK = threading.Lock()


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def validate_chat_request(body: dict) -> tuple[str, list[dict]]:
    """Check the request is plain data we're willing to run. Raises ValueError."""
    model = body.get("model")
    if model not in MODEL_ALLOWLIST:
        raise ValueError(f"model {model!r} is not allowed. Allowed: {MODEL_ALLOWLIST}")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES:
        raise ValueError("messages must be a non-empty list")
    clean = []
    for m in messages:
        if not isinstance(m, dict) or m.get("role") not in ALLOWED_ROLES:
            raise ValueError("each message needs a role of system/user/assistant")
        content = m.get("content")
        if not isinstance(content, str) or len(content) > MAX_CHARS_PER_MESSAGE:
            raise ValueError("each message needs text content (not too long)")
        item = {"role": m["role"], "content": content}
        images = m.get("images")
        if images:
            if not supports_images(model):
                raise ValueError(f"{model} can't look at images; pick a model that can")
            if (not isinstance(images, list) or len(images) > MAX_IMAGES
                    or not all(isinstance(i, str) and len(i) <= MAX_IMAGE_BYTES * 4 // 3 + 4 for i in images)):
                raise ValueError("images must be a short list of base64 strings")
            item["images"] = images  # pictures are data only: Ollama just looks at them
        clean.append(item)
    return model, clean


def start_pull(model: str) -> bool:
    """Download a model in the background. Returns False if it's already downloading."""
    with PULL_LOCK:
        if model in PULLING:
            return False
        PULLING[model] = None

    def work() -> None:
        size = MODELS.get(model, {}).get("size", "?")
        log(f"Downloading {model} (about {size}) so teammates can use it...")
        shown = [-10]

        def progress(percent, status):
            PULLING[model] = percent
            if percent is not None and percent >= shown[0] + 10:
                shown[0] = percent - percent % 10
                log(f"  {model}: {percent}%")
        try:
            pull(model, progress)
            log(f"{model} downloaded and shared.")
        except (RuntimeError, ValueError) as e:
            log(f"Couldn't download {model}: {e} (is Ollama up to date?)")
        finally:
            with PULL_LOCK:
                PULLING.pop(model, None)

    threading.Thread(target=work, daemon=True).start()
    return True


def stream_fake(model: str, messages: list[dict]):
    """Pretend model, for testing the network without Ollama installed."""
    text = f"(fake {model}) You said: {messages[-1]['content']}"
    for word in text.split(" "):
        time.sleep(0.05)
        yield word + " "


class PeerHandler(BaseHTTPRequestHandler):
    node_name = "peer"
    fake = False
    location: str | None = None

    # --- helpers -------------------------------------------------------
    def send_json(self, status: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def write_line(self, obj: dict) -> None:
        self.wfile.write((json.dumps(obj) + "\n").encode())
        self.wfile.flush()

    def log_message(self, format, *args) -> None:  # silence the default noisy logs
        pass

    # --- endpoints -----------------------------------------------------
    # Every request goes through do_GET/do_POST below, which catch any error, print it in this
    # window and still answer. (Without this, a crash just closes the connection and the other
    # laptop only sees "Remote end closed connection without response".)
    def send_response(self, *args, **kwargs) -> None:
        self._answered = True
        super().send_response(*args, **kwargs)

    def do_GET(self) -> None:
        self._safely(self._get)

    def do_POST(self) -> None:
        self._safely(self._post)

    def _safely(self, handler) -> None:
        self._answered = False
        try:
            handler()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the other laptop went away
        except Exception as e:
            log(f"ERROR while answering {self.client_address[0]} ({self.command} {self.path}):")
            print(traceback.format_exc(), flush=True)
            if not self._answered:
                try:
                    self.send_json(500, {"error": f"server error on {self.node_name}: {e!r}"})
                except OSError:
                    pass

    def _get(self) -> None:
        if self.path == "/info":
            models = MODEL_ALLOWLIST if self.fake else installed_models()
            with PULL_LOCK:  # a download thread may be changing it right now
                downloading = dict(PULLING)
            log(f"{self.client_address[0]} connected (asked for info)")
            self.send_json(200, {"name": self.node_name, "models": models, "fake": self.fake,
                                 "downloading": downloading, "location": self.location})
        else:
            self.send_json(404, {"error": "not found"})

    def handle_pull(self) -> None:
        """A teammate asks this laptop to download an (allowlisted) model."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            model = json.loads(self.rfile.read(min(length, 10_000)) or b"{}").get("model")
        except (ValueError, json.JSONDecodeError):
            model = None
        if model not in MODEL_ALLOWLIST:
            self.send_json(400, {"error": f"model {model!r} is not allowed"})
            return
        if self.fake or model in installed_models():
            self.send_json(200, {"status": "already have it"})
            return
        log(f"{self.client_address[0]} asked this laptop to download {model}")
        started = start_pull(model)
        self.send_json(200, {"status": "downloading" if started else "already downloading"})

    def _post(self) -> None:
        if self.path == "/pull":
            self.handle_pull()
            return
        if self.path != "/chat":
            self.send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY_BYTES:
                raise ValueError("request too big")
            body = json.loads(self.rfile.read(length) or b"{}")
            model, messages = validate_chat_request(body)
        except (ValueError, json.JSONDecodeError) as e:
            self.send_json(400, {"error": str(e)})
            return

        who = self.client_address[0]
        preview = messages[-1]["content"][:60].replace("\n", " ")
        pics = len(messages[-1].get("images", []))
        # The model is always the one the asking laptop chose; the host only decides what's downloaded.
        log(f"{who} asked for [model] {model}: \"{preview}\"" + (f" (+{pics} image(s))" if pics else ""))

        # Reply is streamed back as one JSON object per line.
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        start = time.time()
        chars = 0
        usage: dict = {}
        pieces = stream_fake(model, messages) if self.fake else chat_stream(model, messages, usage=usage)
        try:
            for piece in pieces:
                chars += len(piece)
                self.write_line({"token": piece})
            secs = round(time.time() - start, 1)
            if usage.get("seconds") is None:  # fake model, or Ollama didn't say
                usage["seconds"] = secs
            earned, detail = credits.credits_for(usage)
            self.write_line({"done": True, "seconds": secs, "node": self.node_name, "model": model,
                             "usage": usage})
            log(f"{who} <- reply done ({chars} chars, {secs}s)")
            log(credits.line("earned", earned, f"{detail} for {who}"))
        except (BrokenPipeError, ConnectionResetError):
            log(f"{who} disconnected mid-reply")
        except Exception as e:  # Ollama down, model missing, etc.
            log(f"error talking to Ollama: {e}")
            try:
                self.write_line({"error": f"Ollama error on {self.node_name}: {e}"})
            except OSError:
                pass


class PeerServer(ThreadingHTTPServer):
    daemon_threads = True
    # Only one server per port. (On Windows, "reuse address" would let a second server share
    # the port, and requests could go to the wrong/old one.)
    allow_reuse_address = os.name != "nt"

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main() -> None:
    parser = argparse.ArgumentParser(description="Share this laptop's Ollama with peers on the network.")
    parser.add_argument("--name", default=socket.gethostname(), help="friendly name shown to peers")
    parser.add_argument("--port", type=int, default=PEER_PORT)
    parser.add_argument("--fake", action="store_true", help="no Ollama: reply with a fake echo model")
    parser.add_argument("--location", type=str.upper, choices=list(carbon.provinces()),
                        help="province/territory this laptop is in (fake for now), e.g. QC")
    parser.add_argument("--room", help="room name to post this laptop's address in (remembered)")
    parser.add_argument("--models", nargs="*", default=[],
                        help=f"models to download (if needed) and share: {' '.join(MODEL_ALLOWLIST)}")
    args = parser.parse_args()
    bad = [m for m in args.models if m not in MODEL_ALLOWLIST]
    if bad:
        raise SystemExit(f"Unknown model(s): {' '.join(bad)}. Choose from: {' '.join(MODEL_ALLOWLIST)}")
    room = resolve_room(args.room)

    PeerHandler.node_name = args.name
    PeerHandler.fake = args.fake
    PeerHandler.location = args.location
    log(f"[carbon] Location: {carbon.label(args.location)}"
        + ("" if args.location else ". Askers will use you last; set one with --location"))

    if not args.fake:
        models = installed_models()
        if models:
            log(f"Ollama OK. Sharing models: {', '.join(models)}")
            first = next((m for m in args.models if m in models), models[0])
            threading.Thread(target=warm_up, args=(first,), daemon=True).start()
        elif not args.models:
            log("WARNING: no allowed model downloaded yet. Teammates' requests will start downloads,")
            log(f"  or start with e.g.:  python -m peer.server --models {MODEL_ALLOWLIST[0]}")
        for m in args.models:
            if m not in models:
                start_pull(m)

    try:
        server = PeerServer(("0.0.0.0", args.port), PeerHandler)
    except OSError as e:
        log(f"Can't start: port {args.port} is already in use ({e}).")
        log("  Another peer server (or another program) is already running on this laptop.")
        log("  Close the other one (check other terminals and the GUI's Host mode), or use --port 8770.")
        raise SystemExit(1)
    if not start_responder(args.name, args.port):
        log("WARNING: discovery port busy, so peer.split can't auto-find this laptop (use --peers).")
    log(f"Peer '{args.name}' is up.")
    if room:
        start_announcer(room, args.name, args.port)
    else:
        log("No room set, so teammates need your IP. (Make one with: python -m peer.room)")
    log(f"Tell your teammate to run:  python -m peer.chat --peer {local_ip()}")
    log("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Stopping. Bye!")
        server.server_close()


if __name__ == "__main__":
    main()
