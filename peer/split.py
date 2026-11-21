"""Split one big prompt across every laptop on the network, then put it back together.

    python -m peer.split "Write a beginner's guide to composting"

What happens (the prompts for each step live in peer/prompts.py):
  1. FIND:    this laptop + every peer server in the room / on the Wi-Fi.
  2. PLAN:    this laptop's model decides what the final answer looks like and
              cuts it into slices of the ANSWER (e.g. primes 1-7, 8-14, 15-20).
              Tasks that can't be cut up go to one laptop.
  3. RUN:     every slice goes to a different laptop at the same time. If a
              laptop fails, this laptop redoes its slice.
  4. COMBINE: slices are pasted together in order (or rewritten, if the plan says so).
  5. CHECK:   this laptop's model checks the result against the task and format.
  Everything is shown and saved in results/.
"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from peer import ollama, prompts
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

    def run(self, model: str, messages: list[dict], json_format: bool | dict = False,
            temperature: float | None = None) -> str:
        if self.fake:
            time.sleep(0.3)
            return f"(fake local answer to: {messages[-1]['content'][-80:]})"
        return ollama.chat(model, messages, json_format, temperature)


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


# ---------------------------------------------------------------- plan, run, join, check
@dataclass
class Plan:
    final_format: str
    can_split: bool
    combine: str                    # "join" or "rewrite"
    parts: list[dict]               # [{"instruction", "expected_output"}, ...]


def ask_json(local: LocalDevice, model: str, system: str, user: str, schema: dict) -> dict:
    """Ask this laptop's model for JSON matching a schema (falls back to plain JSON mode)."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        reply = local.run(model, messages, json_format=schema, temperature=0)
    except RuntimeError:  # older Ollama versions don't support schemas
        reply = local.run(model, messages, json_format=True, temperature=0)
    return json.loads(reply)


def plan(local: LocalDevice, model: str, task: str, helpers: int) -> Plan:
    """Ask this laptop's model what the answer looks like and how to cut it into slices."""
    whole = Plan("Whatever best answers the task.", False, "join",
                 [{"instruction": task, "expected_output": "the complete answer"}])
    if local.fake:
        return Plan("(fake) a numbered list", helpers > 1, "join", [
            {"instruction": f"(fake) slice {i + 1} of {helpers} of: {task}", "expected_output": f"slice {i + 1}"}
            for i in range(helpers)])
    try:
        raw = ask_json(local, model, prompts.PLANNER_SYSTEM, prompts.planner_user(task, helpers),
                       prompts.PLAN_SCHEMA)
        parts = [{"instruction": str(x["instruction"]).strip(),
                  "expected_output": str(x.get("expected_output", "")).strip()}
                 for x in raw["parts"] if str(x.get("instruction", "")).strip()]
        result = Plan(str(raw["final_format"]).strip(), bool(raw["can_split"]),
                      "rewrite" if raw.get("combine") == "rewrite" else "join", parts)
    except (ValueError, KeyError, TypeError) as e:
        log(f"Planner gave an unusable answer ({e}); running the whole prompt on one laptop.")
        return whole
    if not result.parts:
        return whole
    if not result.can_split or len(result.parts) == 1:
        # One slice only: send the original task (plus the format) rather than a paraphrase.
        return Plan(result.final_format, False, "join",
                    [{"instruction": task, "expected_output": result.final_format}])
    if len(result.parts) > helpers:  # more slices than laptops: fold extras into the last one
        extra = result.parts[helpers - 1:]
        result.parts = result.parts[:helpers - 1] + [{
            "instruction": " Then: ".join(x["instruction"] for x in extra),
            "expected_output": " then ".join(x["expected_output"] for x in extra)}]
    return result


@dataclass
class PartResult:
    index: int
    instruction: str
    device: str
    text: str = ""
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)


def clean_piece(text: str) -> str:
    """Drop chatty openers like 'Here is my part:' that small models add anyway."""
    lines = text.strip().splitlines()
    while lines and lines[0].strip().lower().startswith(("here is", "here are", "here's", "sure", "okay")) \
            and lines[0].rstrip().endswith((":", "!", ".")):
        lines.pop(0)
    return "\n".join(lines).strip()


def run_part(index: int, part: dict, total: int, the_plan: Plan, device: Device,
             fallback: LocalDevice, model: str, task: str) -> PartResult:
    messages = [
        {"role": "system", "content": prompts.WORKER_SYSTEM},
        {"role": "user", "content": prompts.worker_user(
            task, the_plan.final_format, part["instruction"], part["expected_output"], index + 1, total)},
    ]
    result = PartResult(index, part["instruction"], device.name)
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
    result.text = clean_piece(result.text)
    result.seconds = round(time.time() - start, 1)
    return result


def join_pieces(pieces: list[str]) -> str:
    """Paste pieces in order. List-like pieces go line after line; paragraphs get a blank line."""
    multi_paragraph = any("\n\n" in p for p in pieces)
    return ("\n\n" if multi_paragraph else "\n").join(pieces)


def rewrite(local: LocalDevice, model: str, task: str, the_plan: Plan, pieces: list[str]) -> str:
    messages = [
        {"role": "system", "content": prompts.REWRITE_SYSTEM},
        {"role": "user", "content": prompts.rewrite_user(task, the_plan.final_format, pieces)},
    ]
    return local.run(model, messages, temperature=0)


def check(local: LocalDevice, model: str, task: str, the_plan: Plan, answer: str) -> tuple[bool, list[str]]:
    if local.fake:
        return True, []
    try:
        raw = ask_json(local, model, prompts.CHECK_SYSTEM,
                       prompts.check_user(task, the_plan.final_format, answer), prompts.CHECK_SCHEMA)
        problems = [str(p) for p in raw.get("problems", []) if str(p).strip()]
        return bool(raw.get("looks_correct")) and not problems, problems
    except (ValueError, KeyError, TypeError, RuntimeError) as e:
        return False, [f"(the check itself failed: {e})"]


def save(task: str, model: str, devices: list[Device], the_plan: Plan, parts: list[PartResult],
         final: str, verdict: tuple[bool, list[str]] | None, total_seconds: float) -> Path:
    out_dir = Path(RESULTS_DIR)
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}.md"
    lines = [
        "# Result", "", f"**Prompt:** {task}", "",
        f"**Model:** {model} · **Devices:** {', '.join(d.name for d in devices)} · "
        f"**Total time:** {total_seconds}s", "",
        f"**Planned format:** {the_plan.final_format} · **Combined by:** {the_plan.combine}", "",
    ]
    if verdict:
        ok, problems = verdict
        lines += ["**Check:** " + ("looks correct" if ok else "possible problems: " + "; ".join(problems)), ""]
    lines += ["## Final answer", "", final, "", "## Parts", ""]
    for p in parts:
        lines += [f"### Part {p.index + 1} — {p.device} ({p.seconds}s)", "", f"*{p.instruction}*", ""]
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
    parser.add_argument("--merge", action="store_true",
                        help="always have this laptop rewrite the parts into one answer (normally the plan decides)")
    parser.add_argument("--no-check", action="store_true", help="skip the final check step")
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

    log(f"PLAN: asking this laptop how to split the prompt ({len(devices)} laptop(s) available)...")
    the_plan = plan(local, args.model, prompt, len(devices))
    log(f"  final answer should be: {the_plan.final_format}")
    if the_plan.can_split:
        log(f"  split into {len(the_plan.parts)} slice(s), combined by: {the_plan.combine}")
        workers = devices[:len(the_plan.parts)]
    else:
        log("  this task can't be split into independent slices, so one laptop will do it")
        workers = [devices[1] if len(devices) > 1 else devices[0]]  # prefer a helper laptop
    for i, (part, d) in enumerate(zip(the_plan.parts, workers)):
        log(f"  part {i + 1} -> {d.name}: {part['instruction']}")

    log("RUN: sending parts out (all at the same time)...")
    parts: list[PartResult] = []
    total = len(the_plan.parts)
    with ThreadPoolExecutor(max_workers=total) as pool:
        futures = [pool.submit(run_part, i, part, total, the_plan, d, local, args.model, prompt)
                   for i, (part, d) in enumerate(zip(the_plan.parts, workers))]
        for f in as_completed(futures):
            r = f.result()
            parts.append(r)
            log(f"  part {r.index + 1} done by {r.device} in {r.seconds}s")
    parts.sort(key=lambda p: p.index)
    pieces = [p.text for p in parts]

    if len(parts) > 1 and (args.merge or the_plan.combine == "rewrite"):
        log("COMBINE: this laptop is rewriting the parts into one answer...")
        final = rewrite(local, args.model, prompt, the_plan, pieces)
    else:
        log("COMBINE: pasting the parts together in order")
        final = join_pieces(pieces)

    verdict = None
    if not args.no_check:
        log("CHECK: this laptop is checking the answer against the task and format...")
        verdict = check(local, args.model, prompt, the_plan, final)

    total_secs = round(time.time() - t0, 1)
    path = save(prompt, args.model, devices, the_plan, parts, final, verdict, total_secs)

    print("\n" + "=" * 70)
    for p in parts:
        print(f"\n## Part {p.index + 1} — by {p.device} ({p.seconds}s)\n{p.instruction}\n")
        print(p.text)
    print("\n" + "=" * 70 + "\n## Final answer\n")
    print(final.strip())
    print("\n" + "=" * 70)
    if verdict:
        ok, problems = verdict
        if ok:
            log("Check: looks correct (checked by this laptop's model, which can be wrong)")
        else:
            log("Check: possible problems (checked by this laptop's model, which can be wrong):")
            for prob in problems:
                log(f"  - {prob}")
    log(f"Done in {total_secs}s using {len(workers)} laptop(s). Saved to {path}")


if __name__ == "__main__":
    main()
