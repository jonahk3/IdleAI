"""Chat client: talk to an AI model running on ANOTHER laptop.

    python -m peer.chat --peer 192.168.1.42

Type a message and press Enter. Commands:
    /models          list models the other laptop is sharing
    /model NAME      switch model
    /reset           forget the conversation so far
    /quit            exit
"""
import argparse
import json
import urllib.error
import urllib.request

from peer.config import DEFAULT_MODEL, OLLAMA_TIMEOUT, PEER_PORT


class PeerClient:
    def __init__(self, host: str, port: int = PEER_PORT) -> None:
        if ":" in host:
            host, port_str = host.rsplit(":", 1)
            port = int(port_str)
        self.base = f"http://{host}:{port}"

    def info(self) -> dict:
        with urllib.request.urlopen(f"{self.base}/info", timeout=5) as resp:
            return json.load(resp)

    def chat(self, model: str, messages: list[dict]):
        """Yield the reply piece by piece. The last item is a dict with stats."""
        payload = json.dumps({"model": model, "messages": messages}).encode()
        req = urllib.request.Request(
            f"{self.base}/chat", data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            resp = urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT)
        except urllib.error.HTTPError as e:
            raise RuntimeError(json.load(e).get("error", str(e))) from None
        with resp:
            for line in resp:
                if not line.strip():
                    continue
                part = json.loads(line)
                if "error" in part:
                    raise RuntimeError(part["error"])
                if part.get("done"):
                    yield part
                    return
                yield part["token"]
        # The stream ended without a "done" message: the other laptop went away mid-reply.
        raise RuntimeError("connection lost before the reply finished")


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with a model on another laptop.")
    parser.add_argument("--peer", required=True, help="other laptop's IP, e.g. 192.168.1.42")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    client = PeerClient(args.peer)
    try:
        info = client.info()
    except (urllib.error.URLError, OSError) as e:
        print(f"Could not reach {client.base}: {e}")
        print("Check: is `python -m peer.server` running there? Same Wi-Fi? Firewall?")
        return

    name = info["name"]
    print(f"Connected to '{name}' at {client.base}" + ("  [FAKE model mode]" if info.get("fake") else ""))
    print(f"Models shared: {', '.join(info['models']) or '(none - they need to `ollama pull` one)'}")
    model = args.model if args.model in info["models"] or not info["models"] else info["models"][0]
    print(f"Using model: {model}. Type /quit to exit.\n")

    history: list[dict] = []
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text == "/quit":
            break
        if text == "/reset":
            history.clear()
            print("(conversation cleared)")
            continue
        if text == "/models":
            print(", ".join(client.info()["models"]))
            continue
        if text.startswith("/model "):
            model = text.split(" ", 1)[1].strip()
            print(f"(now using {model})")
            continue

        history.append({"role": "user", "content": text})
        print(f"{name}> ", end="", flush=True)
        reply = ""
        try:
            for piece in client.chat(model, history):
                if isinstance(piece, dict):
                    print(f"\n   ({piece['seconds']}s on {piece['node']})\n")
                else:
                    reply += piece
                    print(piece, end="", flush=True)
        except (RuntimeError, urllib.error.URLError, OSError) as e:
            print(f"\n[error] {e}\n")
            history.pop()  # don't keep a message that never got an answer
            continue
        history.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    main()
