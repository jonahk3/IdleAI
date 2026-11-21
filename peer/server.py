"""Peer server: lets other laptops on the same network chat with YOUR Ollama.

Run it on the laptop that will lend its computer:

    python -m peer.server --name alice

It listens on port 8765 and forwards chat messages to the Ollama running on
this laptop. Only models in MODEL_ALLOWLIST are allowed, and requests are
treated as plain text data (nothing from the network is ever executed).
"""
import argparse
import json
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from peer.config import MODEL_ALLOWLIST, PEER_PORT
from peer.discovery import local_ip, start_responder
from peer.ollama import chat_stream, installed_models

ALLOWED_ROLES = {"system", "user", "assistant"}
MAX_MESSAGES = 100
MAX_CHARS_PER_MESSAGE = 20_000


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
        clean.append({"role": m["role"], "content": content})
    return model, clean


def stream_fake(model: str, messages: list[dict]):
    """Pretend model, for testing the network without Ollama installed."""
    text = f"(fake {model}) You said: {messages[-1]['content']}"
    for word in text.split(" "):
        time.sleep(0.05)
        yield word + " "


class PeerHandler(BaseHTTPRequestHandler):
    node_name = "peer"
    fake = False

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
    def do_GET(self) -> None:
        if self.path == "/info":
            models = MODEL_ALLOWLIST if self.fake else installed_models()
            log(f"{self.client_address[0]} connected (asked for info)")
            self.send_json(200, {"name": self.node_name, "models": models, "fake": self.fake})
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/chat":
            self.send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            model, messages = validate_chat_request(body)
        except (ValueError, json.JSONDecodeError) as e:
            self.send_json(400, {"error": str(e)})
            return

        who = self.client_address[0]
        preview = messages[-1]["content"][:60].replace("\n", " ")
        log(f"{who} -> {model}: \"{preview}\"")

        # Reply is streamed back as one JSON object per line.
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        start = time.time()
        chars = 0
        gen = stream_fake if self.fake else chat_stream
        try:
            for piece in gen(model, messages):
                chars += len(piece)
                self.write_line({"token": piece})
            secs = round(time.time() - start, 1)
            self.write_line({"done": True, "seconds": secs, "node": self.node_name})
            log(f"{who} <- reply done ({chars} chars, {secs}s)")
        except (BrokenPipeError, ConnectionResetError):
            log(f"{who} disconnected mid-reply")
        except Exception as e:  # Ollama down, model missing, etc.
            log(f"error talking to Ollama: {e}")
            try:
                self.write_line({"error": f"Ollama error on {self.node_name}: {e}"})
            except OSError:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Share this laptop's Ollama with peers on the network.")
    parser.add_argument("--name", default=socket.gethostname(), help="friendly name shown to peers")
    parser.add_argument("--port", type=int, default=PEER_PORT)
    parser.add_argument("--fake", action="store_true", help="no Ollama: reply with a fake echo model")
    args = parser.parse_args()

    PeerHandler.node_name = args.name
    PeerHandler.fake = args.fake

    if not args.fake:
        models = installed_models()
        if not models:
            log("WARNING: couldn't find an allowed model in Ollama.")
            log(f"  Is Ollama running? Try:  ollama pull {MODEL_ALLOWLIST[0]}")
        else:
            log(f"Ollama OK. Sharing models: {', '.join(models)}")

    server = ThreadingHTTPServer(("0.0.0.0", args.port), PeerHandler)
    if not start_responder(args.name, args.port):
        log("WARNING: discovery port busy, so peer.split can't auto-find this laptop (use --peers).")
    log(f"Peer '{args.name}' is up.")
    log(f"Tell your teammate to run:  python -m peer.chat --peer {local_ip()}")
    log("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Stopping. Bye!")
        server.server_close()


if __name__ == "__main__":
    main()
