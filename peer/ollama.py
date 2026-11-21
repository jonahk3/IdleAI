"""Talking to the Ollama running on THIS laptop."""
import json
import urllib.error
import urllib.request

from peer.config import MODEL_ALLOWLIST, OLLAMA_TIMEOUT, OLLAMA_URL


def installed_models() -> list[str]:
    """Models that are both downloaded in Ollama and on the allowlist."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, OSError):
        return []
    names = {m["name"] for m in data.get("models", [])}
    return [m for m in MODEL_ALLOWLIST if m in names]


def chat_stream(model: str, messages: list[dict], json_format: bool | dict = False,
                temperature: float | None = None):
    """Yield pieces of the reply as Ollama generates them.

    json_format=True forces valid JSON; a dict is a JSON schema the reply must follow.
    """
    if model not in MODEL_ALLOWLIST:
        raise ValueError(f"model {model!r} is not allowed")
    body = {"model": model, "messages": messages, "stream": True}
    if json_format:
        body["format"] = json_format if isinstance(json_format, dict) else "json"
    if temperature is not None:
        body["options"] = {"temperature": temperature}
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
        for line in resp:  # Ollama sends one JSON object per line
            if not line.strip():
                continue
            part = json.loads(line)
            if "error" in part:
                raise RuntimeError(part["error"])
            yield part.get("message", {}).get("content", "")
            if part.get("done"):
                return


def chat(model: str, messages: list[dict], json_format: bool | dict = False,
         temperature: float | None = None) -> str:
    """Return the whole reply as one string."""
    return "".join(chat_stream(model, messages, json_format, temperature))
