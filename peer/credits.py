"""How many credits a piece of work is worth.

The helper that does the work EARNS this amount, and the device that asked SPENDS
the same amount. Both sides use this one function, so they always agree.

- Normally it's based on tokens: what the model read + what it wrote (Ollama
  reports both with every answer).
- If a model doesn't report tokens, compute time is used instead.

Work done on your own device (planning, combining, your own part) is free:
you'd be paying yourself.
"""
from peer.config import CREDITS_PER_1000_TOKENS, CREDITS_PER_MINUTE


def credits_for(usage: dict | None) -> tuple[float, str]:
    """usage = {"input_tokens", "output_tokens", "seconds"} -> (credits, how it was measured)."""
    usage = usage or {}
    tin, tout = usage.get("input_tokens"), usage.get("output_tokens")
    if tin is not None or tout is not None:
        tokens = (tin or 0) + (tout or 0)
        return round(tokens / 1000 * CREDITS_PER_1000_TOKENS, 2), f"{tokens:,} tokens ({tin or 0:,} in, {tout or 0:,} out)"
    seconds = float(usage.get("seconds") or 0)
    return round(seconds / 60 * CREDITS_PER_MINUTE, 2), f"{seconds:.1f}s of compute (no token count)"


def line(kind: str, amount: float, detail: str) -> str:
    """The [credits] line printed by the tools (the GUI reads these to keep the balance)."""
    return f"[credits] {kind} {amount:.2f} credits ({detail})"
