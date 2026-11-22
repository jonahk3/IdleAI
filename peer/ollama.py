"""Talking to the Ollama running on THIS device."""
import json
import re
import urllib.error
import urllib.request

from peer.config import ANSWER_OPTIONS, MAX_CONTEXT, MODEL_ALLOWLIST, MODELS, OLLAMA_TIMEOUT, OLLAMA_URL

# Keep the model in memory this long after each request. (Ollama's default is 5 minutes,
# after which the next request must reload the model first, which takes seconds.)
KEEP_ALIVE = "30m"


def canonical(name: str) -> str:
    """Ollama calls `ollama pull lfm3` "lfm3:latest"; we call it "lfm3"."""
    return name[: -len(":latest")] if name.endswith(":latest") else name


def supports_images(model: str) -> bool:
    return bool(MODELS.get(model, {}).get("images"))


def installed_models() -> list[str]:
    """Models that are both downloaded in Ollama and on the allowlist."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5) as resp:
            data = json.load(resp)
        names = {canonical(m["name"]) for m in data.get("models", [])}
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError, AttributeError):
        return []  # Ollama not running, or something else answered on its port
    return [m for m in MODEL_ALLOWLIST if m in names]


def ollama_running() -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5):
            return True
    except (urllib.error.URLError, OSError):
        return False


def pull(model: str, on_progress=None) -> None:
    """Download a model through Ollama (like `ollama pull`). Raises RuntimeError on failure.

    on_progress(percent, status) is called as the download goes (percent may be None).
    """
    if model not in MODEL_ALLOWLIST:
        raise ValueError(f"model {model!r} is not allowed")
    body = json.dumps({"model": model, "stream": True}).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/pull", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
            for line in resp:
                if not line.strip():
                    continue
                part = json.loads(line)
                if "error" in part:
                    raise RuntimeError(part["error"])
                total, done = part.get("total"), part.get("completed")
                percent = round(100 * done / total) if total and done is not None else None
                if on_progress:
                    on_progress(percent, part.get("status", ""))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Ollama refused the download: {e}") from None
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"couldn't reach Ollama: {e}") from None
    if model not in installed_models():
        raise RuntimeError(f"download finished but {model} isn't listed. Is your Ollama up to date?")


def ensure_model(model: str, log) -> bool:
    """Download the model if this device doesn't have it yet. Returns True when it's ready."""
    if model in installed_models():
        return True
    if not ollama_running():
        log("Ollama isn't running on this device. Open the Ollama app and try again.")
        return False
    size = MODELS.get(model, {}).get("size", "?")
    log(f"Downloading {model} (one time, about {size}). This can take a while...")
    shown = {"pct": -10}

    def progress(percent, status):
        if percent is not None and percent >= shown["pct"] + 10:
            shown["pct"] = percent - percent % 10
            log(f"  {model}: {percent}%")
        elif percent is None and status and status != shown.get("status") and status != "success" \
                and not status.startswith("pulling"):
            # e.g. "verifying sha256 digest", "writing manifest": can take a minute after 100%
            shown["status"] = status
            log(f"  {model}: {status}...")
    try:
        pull(model, progress)
    except (RuntimeError, ValueError) as e:
        log(f"Couldn't download {model}: {e}")
        log("  Make sure Ollama is up to date (https://ollama.com/download); newer models need a newer Ollama.")
        return False
    log(f"{model} downloaded.")
    return True


def needed_tokens(messages: list[dict], num_predict: int | None = None) -> int:
    """Rough number of tokens for the prompt (about 3 characters each, ~800 per image) plus the answer."""
    chars = sum(len(m.get("content") or "") for m in messages)
    images = sum(len(m.get("images") or []) for m in messages)
    return chars // 3 + images * 800 + (num_predict or 1024) + 512


def context_size(messages: list[dict], num_predict: int | None) -> int:
    """Room for the whole prompt plus the answer (Ollama silently drops whatever doesn't fit).

    About 3 characters per token (a safe guess), ~800 tokens per image, plus the answer and a margin,
    rounded up to a multiple of 2,048; at least 4,096 and at most MAX_CONTEXT.
    """
    size = -(-needed_tokens(messages, num_predict) // 2048) * 2048
    return max(4096, min(MAX_CONTEXT, size))


def chat_stream(model: str, messages: list[dict], json_format: bool | dict = False,
                options: dict | None = None, usage: dict | None = None):
    """Yield pieces of the reply as Ollama generates them.

    json_format=True forces valid JSON; a dict is a JSON schema the reply must follow.
    options are Ollama model settings, e.g. {"temperature": 0, "num_predict": 300}
    (num_predict = the most tokens the model may write).
    If a `usage` dict is given, it's filled in at the end with input_tokens, output_tokens
    and seconds (as reported by Ollama; a missing count stays None).
    """
    if model not in MODEL_ALLOWLIST:
        raise ValueError(f"model {model!r} is not allowed")
    body = {"model": model, "messages": messages, "stream": True, "keep_alive": KEEP_ALIVE}
    if json_format:
        body["format"] = json_format if isinstance(json_format, dict) else "json"
        body["options"] = dict(options or {})  # JSON is shaped by the schema; no repeat penalty
    else:
        body["options"] = {**ANSWER_OPTIONS, **(options or {})}  # stop loops and endless answers
    body["options"].setdefault("num_ctx", context_size(messages, body["options"].get("num_predict")))
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
                if "repeat" in str(part["error"]).lower():
                    # Ollama stopped a model that was repeating itself: keep what it wrote so far
                    # (trim_repetition() removes the repeated tail) instead of losing the answer.
                    if usage is not None:
                        usage["cut_off"] = True
                    return
                raise RuntimeError(part["error"])
            yield part.get("message", {}).get("content", "")
            if part.get("done"):
                if usage is not None:
                    usage.update(input_tokens=part.get("prompt_eval_count"),
                                 output_tokens=part.get("eval_count"),
                                 seconds=round((part.get("total_duration") or 0) / 1e9, 2) or None)
                return


def chat(model: str, messages: list[dict], json_format: bool | dict = False,
         options: dict | None = None) -> str:
    """Return the whole reply as one string."""
    return "".join(chat_stream(model, messages, json_format, options))


def warm_up(model: str) -> None:
    """Load the model into memory now, so the first real request doesn't wait for it."""
    if model not in MODEL_ALLOWLIST:
        return
    body = json.dumps({"model": model, "keep_alive": KEEP_ALIVE}).encode()  # no prompt = just load
    req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT):
            pass
    except (urllib.error.URLError, OSError):
        pass


def trim_repetition(text: str) -> str:
    """Remove the looping tail small models sometimes produce (the same line or sentence again and again)."""
    # 1. the same chunk of text repeated 3+ times at the end -> keep it once
    m = re.search(r"(.{12,}?)(?:\s*\1){2,}\s*$", text, re.S)
    if m:
        text = text[:m.start()] + m.group(1)
    # 2. lines: drop exact repeats that come right after each other, and any line seen 3+ times
    out, seen = [], {}
    for line in text.splitlines():
        key = line.strip()
        if key and out and out[-1].strip() == key:
            continue
        seen[key] = seen.get(key, 0) + 1
        if key and seen[key] > 2:
            continue
        out.append(line)
    return "\n".join(out).strip()
