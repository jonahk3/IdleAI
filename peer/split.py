"""Split one big prompt across every laptop on the network, then put it back together.

    python -m peer.split "Write a beginner's guide to composting"

What happens:
  1. Find devices: this laptop + every peer server that answers on the Wi-Fi.
  2. PLAN:    this laptop's Ollama splits the prompt into one part per device.
  3. RUN:     each part is sent to a different device at the same time
              (this laptop does one part too). If a device fails, this laptop
              redoes its part.
  4. COLLECT: the answers are joined in order, shown, and saved in results/.
"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from peer import ollama
from peer.chat import PeerClient
from peer.config import DEFAULT_MODEL, KNOWN_PEERS_FILE, PEER_PORT, RESULTS_DIR
from peer.discovery import discover, local_ip
from peer.room import lookup, resolve_room


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- devices
class Device:
    name: str = "?"
    where: str = "?"

    def run(self, model: str, messages: list[dict]) -> str:
        raise NotImplementedError


class LocalDevice(Device):
    """This laptop's own Ollama."""

    def __init__(self, fake: bool = False) -> None:
        self.name, self.where, self.fake = "this laptop", "localhost", fake

    def run(self, model: str, messages: list[dict], json_format: bool = False) -> str:
        if self.fake:
            time.sleep(0.3)
            return f"(fake local answer to: {messages[-1]['content'][-80:]})"
        return ollama.chat(model, messages, json_format)


class RemoteDevice(Device):
    """Another laptop running `python -m peer.server`."""

    def __init__(self, name: str, ip: str, port: int) -> None:
        self.name, self.where = name, f"{ip}:{port}"
        self.client = PeerClient(ip, port)

    def run(self, model: str, messages: list[dict]) -> str:
        return "".join(p for p in self.client.chat(model, messages) if isinstance(p, str))


def load_known_peers() -> list[tuple[str, int]]:
    """Laptops that worked before (saved in known_peers.json)."""
    try:
        data = json.loads(Path(KNOWN_PEERS_FILE).read_text())
        return [(str(d["ip"]), int(d["port"])) for d in data]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def save_known_peers(peers: list[tuple[str, int]]) -> None:
    unique = list(dict.fromkeys(load_known_peers() + peers))
    Path(KNOWN_PEERS_FILE).write_text(json.dumps([{"ip": h, "port": p} for h, p in unique], indent=2))


def check_peer(host: str, port: int) -> dict | Exception:
    try:
        return PeerClient(host, port).info(timeout=3)
    except OSError as e:
        return e


def find_devices(model: str, manual_peers: list[str], use_discovery: bool, fake: bool,
                 room: str | None = None) -> list[Device]:
    devices: list[Device] = [LocalDevice(fake)]
    candidates: list[tuple[str, int]] = []
    for p in manual_peers:
        host, _, port = p.partition(":")
        candidates.append((host, int(port or PEER_PORT)))
    known = load_known_peers()
    if known:
        log(f"Trying {len(known)} laptop(s) that worked before ({KNOWN_PEERS_FILE})")
        candidates += known
    me = {local_ip(), "127.0.0.1"}
    if room:
        listed = [d for d in lookup(room) if d["ip"] not in me]
        log(f"Room '{room}': {len(listed)} other laptop(s) listed")
        candidates += [(d["ip"], d["port"]) for d in listed]
    if use_discovery and not candidates:
        log("Looking for other laptops on the Wi-Fi...")
        candidates += [(d["ip"], d["port"]) for d in discover() if d["ip"] not in me]

    candidates = list(dict.fromkeys(candidates))  # remove duplicates, keep order
    with ThreadPoolExecutor(max_workers=max(1, len(candidates))) as pool:
        answers = list(pool.map(lambda c: check_peer(*c), candidates))

    working: list[tuple[str, int]] = []
    for (host, port), info in zip(candidates, answers):
        if isinstance(info, Exception):
            log(f"  skip {host}:{port} (not reachable: {info})")
            continue
        if model not in info["models"]:
            log(f"  skip '{info['name']}' at {host}:{port} (doesn't have {model})")
            continue
        devices.append(RemoteDevice(info["name"], host, port))
        working.append((host, port))
    if working:
        save_known_peers(working)
    if len(devices) == 1:
        log("No other laptops found. Check that `python -m peer.server` is running on them,")
        log("you're on the same Wi-Fi, and try `--peers <IP>` using the IP their server printed.")
    return devices


# ---------------------------------------------------------------- the three steps
PLANNER_SYSTEM = (
    "You split a task into independent parts so that different helpers can work on them "
    "at the same time, without seeing each other's work. Each part must be specific and "
    "self-contained, and together the parts must cover the whole task without overlapping. "
    'Reply with JSON only, in the form {"subtasks": ["...", "..."]}.'
)
WORKER_SYSTEM = (
    "You are one of several helpers working on a larger task. Do ONLY your assigned part. "
    "Don't write an introduction or conclusion for the whole task. Be clear and concise."
)
MERGE_SYSTEM = (
    "You combine several parts written by different helpers into one smooth, well-organised "
    "final answer. Keep all the useful content, remove repetition, and don't add new facts."
)


def plan(local: LocalDevice, model: str, prompt: str, n: int) -> list[str]:
    """Ask this laptop's model to split the prompt into n parts."""
    if n == 1:
        return [prompt]
    if local.fake:
        return [f"Part {i + 1} of {n} of: {prompt}" for i in range(n)]
    messages = [
        {"role": "system", "content": PLANNER_SYSTEM},
        {"role": "user", "content": f"Task: {prompt}\n\nSplit it into exactly {n} subtasks."},
    ]
    try:
        subtasks = json.loads(local.run(model, messages, json_format=True))["subtasks"]
        subtasks = [str(s).strip() for s in subtasks if str(s).strip()]
    except (ValueError, KeyError, TypeError) as e:
        log(f"Planner gave an unusable answer ({e}); running the whole prompt as one part.")
        return [prompt]
    if not subtasks:
        return [prompt]
    if len(subtasks) > n:  # fold any extras into the last part
        subtasks = subtasks[: n - 1] + ["; ".join(subtasks[n - 1:])]
    return subtasks


@dataclass
class PartResult:
    index: int
    subtask: str
    device: str
    text: str = ""
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)


def run_part(index: int, subtask: str, device: Device, fallback: LocalDevice,
             model: str, prompt: str) -> PartResult:
    messages = [
        {"role": "system", "content": WORKER_SYSTEM},
        {"role": "user", "content": f"Overall task (for context): {prompt}\n\nYour part: {subtask}"},
    ]
    result = PartResult(index, subtask, device.name)
    start = time.time()
    try:
        result.text = device.run(model, messages)
    except Exception as e:  # device died, Wi-Fi dropped, Ollama error...
        if device is fallback:
            raise
        log(f"  part {index + 1}: '{device.name}' failed ({e}); redoing it on this laptop")
        result.notes.append(f"'{device.name}' failed: {e}")
        result.device = fallback.name
        result.text = fallback.run(model, messages)
    result.seconds = round(time.time() - start, 1)
    return result


def merge(local: LocalDevice, model: str, prompt: str, parts: list[PartResult]) -> str:
    joined = "\n\n".join(f"--- Part {p.index + 1}: {p.subtask}\n{p.text}" for p in parts)
    messages = [
        {"role": "system", "content": MERGE_SYSTEM},
        {"role": "user", "content": f"Task: {prompt}\n\nParts:\n{joined}\n\nWrite the final answer."},
    ]
    return local.run(model, messages)


def save(prompt: str, model: str, devices: list[Device], parts: list[PartResult],
         final: str, total_seconds: float) -> Path:
    out_dir = Path(RESULTS_DIR)
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}.md"
    lines = [
        "# Result", "", f"**Prompt:** {prompt}", "",
        f"**Model:** {model} · **Devices:** {', '.join(d.name for d in devices)} · "
        f"**Total time:** {total_seconds}s", "", "## Final answer", "", final, "", "## Parts", "",
    ]
    for p in parts:
        lines += [f"### Part {p.index + 1} — {p.device} ({p.seconds}s)", "", f"*{p.subtask}*", ""]
        lines += [f"> note: {n}" for n in p.notes] + ([""] if p.notes else [])
        lines += [p.text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ---------------------------------------------------------------- main
def main() -> None:
    parser = argparse.ArgumentParser(description="Split a prompt across laptops on the network.")
    parser.add_argument("prompt", nargs="?", help="what to ask (you'll be asked if you leave it out)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--peers", nargs="*", default=[], help="extra peer IPs, e.g. 192.168.1.42")
    parser.add_argument("--no-discover", action="store_true", help="only use --peers, don't search the Wi-Fi")
    parser.add_argument("--merge", action="store_true", help="have this laptop rewrite the parts into one answer")
    parser.add_argument("--fake", action="store_true", help="this laptop pretends to have a model (testing)")
    parser.add_argument("--forget", action="store_true", help="forget saved laptops (known_peers.json)")
    parser.add_argument("--room", help="room name to find laptops in (remembered)")
    parser.add_argument("--list", action="store_true", help="just list the laptops that can be used, then exit")
    args = parser.parse_args()
    room = resolve_room(args.room)

    if args.forget:
        Path(KNOWN_PEERS_FILE).unlink(missing_ok=True)
        log("Forgot saved laptops.")
    if args.list:
        devices = find_devices(args.model, args.peers, not args.no_discover, True, room)[1:]
        log(f"{len(devices)} other laptop(s) ready:")
        for d in devices:
            log(f"  - {d.name} ({d.where})")
        # One machine-readable line, used by the GUI to fill its peer list.
        print("PEERS_JSON " + json.dumps([{"name": d.name, "address": d.where} for d in devices]), flush=True)
        return
    prompt = args.prompt or input("Prompt: ").strip()
    if not prompt:
        return
    local = LocalDevice(args.fake)
    if not args.fake and args.model not in ollama.installed_models():
        print(f"This laptop's Ollama doesn't have {args.model}. Run: ollama pull {args.model}")
        return

    t0 = time.time()
    devices = find_devices(args.model, args.peers, not args.no_discover, args.fake, room)
    log(f"{len(devices)} device(s) available:")
    for d in devices:
        log(f"  - {d.name} ({d.where})")

    log(f"PLAN: asking this laptop to split the prompt into {len(devices)} part(s)...")
    subtasks = plan(local, args.model, prompt, len(devices))
    for i, s in enumerate(subtasks):
        log(f"  part {i + 1} -> {devices[i].name}: {s}")

    log("RUN: sending parts out...")
    parts: list[PartResult] = []
    with ThreadPoolExecutor(max_workers=len(subtasks)) as pool:
        futures = [pool.submit(run_part, i, s, devices[i], local, args.model, prompt)
                   for i, s in enumerate(subtasks)]
        for f in as_completed(futures):
            r = f.result()
            parts.append(r)
            log(f"  part {r.index + 1} done by {r.device} in {r.seconds}s")
    parts.sort(key=lambda p: p.index)

    if args.merge and len(parts) > 1:
        log("MERGE: this laptop is combining the parts...")
        final = merge(local, args.model, prompt, parts)
    else:
        final = "\n\n".join(p.text.strip() for p in parts)

    total = round(time.time() - t0, 1)
    path = save(prompt, args.model, devices, parts, final, total)

    print("\n" + "=" * 70)
    for p in parts:
        print(f"\n## Part {p.index + 1} — by {p.device} ({p.seconds}s)\n{p.subtask}\n")
        print(p.text.strip())
    if args.merge and len(parts) > 1:
        print("\n" + "=" * 70 + "\n## Final (merged) answer\n")
        print(final.strip())
    print("\n" + "=" * 70)
    log(f"Done in {total}s using {len(devices)} device(s). Saved to {path}")


if __name__ == "__main__":
    main()
