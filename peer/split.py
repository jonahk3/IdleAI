"""Split one big prompt across every device on your grid, then put it back together.

    python -m peer.split "Give me the first 30 prime numbers"

What happens (the prompts for each step live in peer/prompts.py):
  1. FIND:    this device + every peer server in the room / on the Wi-Fi.
  2. PLAN:    this device's coordinator model (llama3.2:3b) writes one self-contained instruction per helper
              (only as many as make sense; short tasks get one).
  3. RUN:     every instruction goes to a different device at the same time. If a
              device fails, this device redoes its part.
  4. COMBINE: the coordinator model turns the helpers' answers into one final answer.
  The helpers run the model you pick with --model.
  Everything is shown and saved in results/.
"""
import argparse
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from peer import attachments, carbon, credits, ollama, prompts
from peer.attachments import Attachments
from peer.chat import PeerClient
from peer.config import (COORDINATOR_MODEL, DEFAULT_MODEL, KNOWN_PEERS_FILE, MODELS, PEER_PORT,
                         PLANNER_MAX_TOKENS, RESULTS_DIR)
from peer.discovery import discover, local_ip
from peer.room import lookup, resolve_room


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- devices
class Device:
    name: str = "?"
    where: str = "?"
    location: str | None = None   # province code, e.g. "QC" (fake for now)

    def run(self, model: str, messages: list[dict]) -> str:
        raise NotImplementedError

    def run_reported(self, model: str, messages: list[dict]) -> tuple[str, str, dict | None]:
        """Run, and also return the model that actually answered and the usage to pay for
        (None = free: work on this device costs nothing)."""
        return self.run(model, messages), model, None


class LocalDevice(Device):
    """This device's own Ollama."""

    def __init__(self, fake: bool = False, location: str | None = None) -> None:
        self.location = location
        self.name, self.where, self.fake = "this device", "localhost", fake

    def run(self, model: str, messages: list[dict], json_format: bool | dict = False,
            options: dict | None = None) -> str:
        if self.fake:
            time.sleep(0.3)
            return f"(fake local answer to: {messages[-1]['content'][-80:]})"
        return ollama.chat(model, messages, json_format, options)


class RemoteDevice(Device):
    """Another device running `python -m peer.server`."""

    def __init__(self, name: str, ip: str, port: int, location: str | None = None) -> None:
        self.name, self.where, self.location = name, f"{ip}:{port}", location
        self.client = PeerClient(ip, port)

    def run(self, model: str, messages: list[dict]) -> str:
        return self.run_reported(model, messages)[0]

    def run_reported(self, model: str, messages: list[dict]) -> tuple[str, str, dict | None]:
        text, used, usage = "", "?", None
        for piece in self.client.chat(model, messages):
            if isinstance(piece, dict):
                used = piece.get("model", "?")  # the helper says which model it ran
                # ...and how much work it was (older helpers only send the time)
                usage = piece.get("usage") or {"seconds": piece.get("seconds", 0)}
            else:
                text += piece
        return text, used, usage


def load_known_peers() -> list[tuple[str, int]]:
    """Devices that worked before (saved in known_peers.json)."""
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
        log(f"Trying {len(known)} device(s) that worked before ({KNOWN_PEERS_FILE})")
        candidates += known
    me = {local_ip(), "127.0.0.1"}
    if room:
        listed = [d for d in lookup(room) if d["ip"] not in me]
        log(f"Grid '{room}': {len(listed)} other device(s) listed")
        candidates += [(d["ip"], d["port"]) for d in listed]
    if use_discovery and not candidates:
        log("Looking for other devices on the Wi-Fi...")
        candidates += [(d["ip"], d["port"]) for d in discover() if d["ip"] not in me]

    candidates = list(dict.fromkeys(candidates))  # remove duplicates, keep order
    with ThreadPoolExecutor(max_workers=max(1, len(candidates))) as pool:
        answers = list(pool.map(lambda c: check_peer(*c), candidates))

    working: list[tuple[str, int]] = []
    for (host, port), info in zip(candidates, answers):
        if isinstance(info, Exception):
            log(f"  skip {host}:{port} (not reachable: {info})")
            log(f"    to find out why, run:  python -m peer.check {host}:{port}")
            continue
        if model not in info["models"]:
            if model in info.get("downloading", {}):
                pct = info["downloading"][model]
                log(f"  skip '{info['name']}' (still downloading {model}" + (f", {pct}%)" if pct is not None else ")"))
            else:
                status = PeerClient(host, port).request_pull(model)
                log(f"  skip '{info['name']}' (doesn't have {model} yet; asked it to download: {status})")
            continue
        devices.append(RemoteDevice(info["name"], host, port, info.get("location")))
        working.append((host, port))
    if working:
        save_known_peers(working)
    if len(devices) == 1:
        log("No other devices found. Check that `python -m peer.server` is running on them,")
        log("you're on the same Wi-Fi, and try `--peers <IP>` using the IP their server printed.")
    return devices


# ---------------------------------------------------------------- carbon-aware routing
def choose_cleanest(devices: list[Device], parts: int) -> list[Device]:
    """The `parts` devices with the lowest CO2 per kWh. Ties: this device first (it's free),
    then the order they were found."""
    ranked = sorted(devices, key=lambda d: (carbon.intensity(d.location), not isinstance(d, LocalDevice)))
    log("  [carbon] cleanest first: " + ", ".join(f"{d.name} {carbon.label(d.location)}" for d in ranked))
    chosen = ranked[:parts]
    skipped = ranked[parts:]
    if skipped:
        log(f"  [carbon] not needed this time: {', '.join(d.name for d in skipped)}")
    return chosen


# ---------------------------------------------------------------- plan, run, combine
def plan(local: LocalDevice, model: str, task: str, available: int, att: Attachments) -> list[str]:
    """Ask this device's model for one self-contained instruction per helper."""
    if available == 1:
        return [task]
    if local.fake:
        return [f"(fake) part {i + 1} of {available} of: {task}" for i in range(available)]
    # Step 1: how many independent pieces does the task really have? (Asked without mentioning
    # the devices, so the model isn't tempted to use them all.) Code decides the number of parts.
    pieces = count_pieces(local, model, task, att)
    wanted = min(pieces, available) if pieces else 1  # can't tell: don't split
    if pieces:
        log(f"  the task has {pieces} independent piece(s) -> {wanted} part(s)"
            + (f" (only {available} device(s) available)" if pieces > available else ""))
    if wanted == 1:
        return [task]  # nothing to split: the whole task goes to the cleanest device
    # Step 2: write exactly that many parts.
    messages = [
        {"role": "system", "content": prompts.PLANNER_SYSTEM},
        # The planner gets the files' names, types and lengths only, never their contents.
        {"role": "user", "content": prompts.planner_user(task, wanted, att.summary)},
    ]
    options = {"temperature": 0, "num_predict": PLANNER_MAX_TOKENS}
    available = wanted  # from here on, never more parts than that
    try:
        try:
            reply = local.run(model, messages, json_format=prompts.plan_schema(wanted), options=options)
        except RuntimeError:  # older Ollama versions don't support schemas
            reply = local.run(model, messages, json_format=True, options=options)
        parts = clean_parts(json.loads(reply)["parts"])
    except (ValueError, KeyError, TypeError) as e:
        log(f"  planner gave an unusable answer ({e}); one device will do the whole task")
        return [task]
    if not parts:
        return [task]
    if len(parts) > available:  # more parts than devices: share them out evenly, in order
        log(f"  planner wrote {len(parts)} parts for {available} device(s); grouping them")
        size, extra = divmod(len(parts), available)
        groups, start = [], 0
        for i in range(available):
            end = start + size + (1 if i < extra else 0)
            groups.append(" ".join(parts[start:end]))
            start = end
        parts = groups
    return parts


def count_pieces(local: LocalDevice, model: str, task: str, att: Attachments) -> int | None:
    """Ask how many independent pieces of work the task has (None if the answer is unusable)."""
    messages = [
        {"role": "system", "content": prompts.COUNT_SYSTEM},
        {"role": "user", "content": prompts.count_user(task, att.summary)},
    ]
    options = {"temperature": 0, "num_predict": 30}
    try:
        try:
            reply = local.run(model, messages, json_format=prompts.COUNT_SCHEMA, options=options)
        except RuntimeError:
            reply = local.run(model, messages, json_format=True, options=options)
        return max(1, int(json.loads(reply)["pieces"]))
    except (ValueError, KeyError, TypeError) as e:
        log(f"  couldn't count the pieces ({e}); one device will do the whole task")
        return None


def clean_parts(raw: list) -> list[str]:
    """Tidy the planner's parts: drop labels like 'Helper 1:', empty parts and duplicates."""
    parts, seen = [], set()
    for item in raw:
        text = re.sub(r"^\s*(helper|part|task)\s*\d+\s*[:.)\-]\s*", "", str(item), flags=re.I).strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            parts.append(text)
    return parts


@dataclass
class PartResult:
    index: int
    instruction: str
    device: str
    text: str = ""
    seconds: float = 0.0
    model: str = ""                 # the model that actually answered (reported by the helper)
    credits: float = 0.0            # what this part cost (0 if this device did it)
    credit_detail: str = ""
    location: str | None = None     # where it ran
    co2_grams: float = 0.0          # rough estimate: device power x time x grid intensity
    notes: list[str] = field(default_factory=list)


def run_part(index: int, instruction: str, device: Device, fallback: LocalDevice, model: str,
             att: Attachments) -> PartResult:
    messages = [
        {"role": "system", "content": prompts.WORKER_SYSTEM},
        attachments.user_message(instruction, att),  # helpers get the files too: they never see the task
    ]
    result = PartResult(index, instruction, device.name, location=device.location)
    start = time.time()
    try:
        text, result.model, usage = device.run_reported(model, messages)
    except Exception as e:  # device died, Wi-Fi dropped, Ollama error...
        if device is fallback:
            raise
        log(f"  part {index + 1}: '{device.name}' failed ({e}); redoing it on this device")
        result.notes.append(f"'{device.name}' failed: {e}")
        result.device, result.location = fallback.name, fallback.location
        text, result.model, usage = fallback.run_reported(model, messages)
    result.text = text.strip()
    if usage is not None:
        result.credits, result.credit_detail = credits.credits_for(usage)
    result.seconds = round(time.time() - start, 1)
    work_seconds = (usage or {}).get("seconds") or result.seconds  # the helper's own timing if it sent one
    result.co2_grams = carbon.estimate_grams(result.location, work_seconds)
    return result


def combine(local: LocalDevice, model: str, task: str, parts: list[PartResult], att: Attachments) -> str:
    """This device's model writes the final answer from the helpers' answers."""
    messages = [
        {"role": "system", "content": prompts.COMBINE_SYSTEM},
        # No files here: the combiner may only use what the helpers wrote.
        {"role": "user", "content": prompts.combine_user(
            task, [p.instruction for p in parts], [p.text for p in parts], att.summary)},
    ]
    return local.run(model, messages, options={"temperature": 0}).strip()


def save(task: str, final: str, attached: list[str]) -> Path:
    """Save the conversation like a chat: your prompt and the final answer only."""
    out_dir = Path(RESULTS_DIR)
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}.md"
    lines = ["**You:**", "", task, ""]
    if attached:
        lines += [f"*Attached: {', '.join(attached)}*", ""]
    lines += ["**Idle:**", "", final, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ---------------------------------------------------------------- main
def main() -> None:
    parser = argparse.ArgumentParser(description="Split a prompt across devices on your grid.")
    parser.add_argument("prompt", nargs="?", help="what to ask (you'll be asked if you leave it out)")
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=list(MODELS),
                        help="the model the helpers run for each part")
    parser.add_argument("--coordinator", default=COORDINATOR_MODEL, choices=list(MODELS),
                        help="the model this device uses to plan and combine (default: %(default)s)")
    parser.add_argument("--location", type=str.upper, choices=list(carbon.provinces()),
                        help="province/territory this device is in (fake for now), e.g. QC")
    parser.add_argument("--file", nargs="*", default=[], help="files or images to attach to the prompt")
    parser.add_argument("--peers", nargs="*", default=[], help="extra peer IPs, e.g. 192.168.1.42")
    parser.add_argument("--no-discover", action="store_true", help="only use --peers, don't search the Wi-Fi")
    parser.add_argument("--fake", action="store_true", help=argparse.SUPPRESS)  # developer testing only
    parser.add_argument("--forget", action="store_true", help="forget saved devices (known_peers.json)")
    parser.add_argument("--room", help="grid name to find devices in (remembered; default: general)")
    parser.add_argument("--list", action="store_true", help="just list the devices that can be used, then exit")
    args = parser.parse_args()
    room = resolve_room(args.room)

    if args.forget:
        Path(KNOWN_PEERS_FILE).unlink(missing_ok=True)
        log("Forgot saved devices.")
    if args.list:
        devices = find_devices(args.model, args.peers, not args.no_discover, True, room)[1:]
        log(f"{len(devices)} other device(s) ready:")
        for d in devices:
            log(f"  - {d.name} ({d.where}) [carbon] {carbon.label(d.location)}")
        # One machine-readable line, used by the GUI to fill its peer list.
        print("PEERS_JSON " + json.dumps([{"name": d.name, "address": d.where, "location": d.location}
                                          for d in devices]), flush=True)
        return
    prompt = args.prompt or input("Prompt: ").strip()
    if not prompt:
        return
    try:
        att = attachments.load(args.file)
    except ValueError as e:
        log(f"Can't use the attachment: {e}")
        return
    if att.images and not ollama.supports_images(args.model):
        log(f"{args.model} can't look at images. Pick one that can: "
            + ", ".join(m for m, info in MODELS.items() if info["images"]))
        return
    if att:
        log(f"Attached: {', '.join(att.summary)}")
    local = LocalDevice(args.fake, args.location)
    if not args.fake and not ollama.ensure_model(args.model, log):  # downloads it if needed
        return

    # The asking device picks the helpers' model; every helper uses exactly this one (hosts only
    # choose which models they download). Planning and combining use the coordinator model.
    coordinator = args.coordinator
    log(f"[model] You chose {args.model} for the parts. Planning and combining use {coordinator}.")
    t0 = time.time()
    if not args.fake:  # load the model into memory while we look for devices
        threading.Thread(target=ollama.warm_up, args=(args.model,), daemon=True).start()
    devices = find_devices(args.model, args.peers, not args.no_discover, args.fake, room)
    devices[0] = local  # this device, with its location
    log(f"{len(devices)} device(s) available:")
    for d in devices:
        log(f"  - {d.name} ({d.where}) [carbon] {carbon.label(d.location)}")
    if len(devices) > 1 and coordinator != args.model and not args.fake:
        if not ollama.ensure_model(coordinator, log):  # downloads it if needed
            log(f"  can't plan without {coordinator}; one device will do the whole task")
            devices = devices[:1]
        else:
            threading.Thread(target=ollama.warm_up, args=(coordinator,), daemon=True).start()

    log(f"PLAN: this device is writing one instruction per helper ({len(devices)} device(s) available)...")
    if len(devices) > 1:
        log(f"  [model] plan on this device: {coordinator}")
    plan_start = time.time()
    instructions = plan(local, coordinator, prompt, len(devices), att)
    log(f"  planned {len(instructions)} part(s) in {time.time() - plan_start:.1f}s")
    # Carbon-aware routing (decided here in code, not by the planner): the parts go to the
    # devices on the cleanest grids. Even a single part leaves this device if a cleaner one exists.
    workers = choose_cleanest(devices, len(instructions))
    for i, (text, d) in enumerate(zip(instructions, workers)):
        log(f"  part {i + 1} -> {d.name} [carbon] {carbon.label(d.location)} [model] {args.model}: {text}")

    log("RUN: sending parts out (all at the same time)...")
    parts: list[PartResult] = []
    with ThreadPoolExecutor(max_workers=len(instructions)) as pool:
        futures = [pool.submit(run_part, i, text, d, local, args.model, att)
                   for i, (text, d) in enumerate(zip(instructions, workers))]
        for f in as_completed(futures):
            r = f.result()
            parts.append(r)
            log(f"  part {r.index + 1} done by {r.device} in {r.seconds}s [model] {r.model}")
            if r.credit_detail:
                log("  " + credits.line("spent", r.credits, f"{r.credit_detail} on {r.device}"))
            if r.model != args.model:
                log(f"  WARNING: part {r.index + 1} was answered by {r.model}, not {args.model}")
    parts.sort(key=lambda p: p.index)

    if len(parts) > 1:
        log("COMBINE: this device is writing the final answer from the helpers' answers...")
        log(f"  [model] combine on this device: {coordinator}")
        combine_start = time.time()
        final = combine(local, coordinator, prompt, parts, att)
        log(f"  combined in {time.time() - combine_start:.1f}s")
    else:
        final = parts[0].text

    total_secs = round(time.time() - t0, 1)
    path = save(prompt, final, att.names())

    print("\n" + "=" * 70 + "\n## Final answer\n")
    print(final)
    print("\n" + "=" * 70)
    spent = sum(p.credits for p in parts)
    log(f"Credits for this prompt: {spent:.2f} spent on helpers (work on this device is free)")
    grams = sum(p.co2_grams for p in parts)
    worst = max(carbon.intensity(d.location) for d in devices)
    worst_grams = sum(p.co2_grams / max(carbon.intensity(p.location), 1e-9) * worst for p in parts)
    log(f"[carbon] about {grams:.3f} g CO2 for the parts (estimate); the dirtiest available device "
        f"would have used about {worst_grams:.3f} g")
    log(f"Done in {total_secs}s using {len(workers)} device(s). Saved to {path}")


if __name__ == "__main__":
    main()
