"""Rooms: a shared online notice board where laptops post their address.

Every peer server posts "I'm <name> at <ip>:<port>" to a room on ntfy.sh (a free
public message service). peer.split reads the room and connects to everyone
listed. Nobody has to type IP addresses.

The room name acts like a password: anyone who knows it can read and post.
Make a random one with:

    python -m peer.room
"""
import ipaddress
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from peer.config import DEFAULT_NETWORK, ROOM_ANNOUNCE_EVERY, ROOM_FILE, ROOM_SERVICE_URL
from peer.discovery import local_ip

ROOM_PATTERN = re.compile(r"^[a-z0-9_-]{3,64}$")
TOPIC_PREFIX = "idle-net-"  # ntfy topic = prefix + network name, so "general" doesn't clash with other apps


def topic(room: str) -> str:
    return TOPIC_PREFIX + room

# Short, common, easy-to-spell words for room names like "maple-otter-river-42".
WORDS = """
apple arrow aspen badge baker beach berry birch black blaze bloom board brave brick
brook brush cabin camel candy canoe cedar chalk charm chess chief cider cliff cloud
clover coast cobra comet coral crane creek crown daisy dance delta denim diner drift
eagle earth ember fable fairy falcon fern field flame flint flute forest frost fruit
garden ghost giant ginger glade globe grape grass gravel harbor hazel heron honey
horse igloo iris island ivory jacket jade jelly jewel jolly kayak kettle kiwi koala
lake lemon lilac lime linen lion lotus lucky lunar mango maple marble meadow melon
mint moose mossy north oasis ocean olive onion orbit otter owl panda paper
peach pearl pepper piano pilot pine pixel planet plum polar pond poppy prairie
puffin quartz quail rabbit radar raven reef river robin rocket rose ruby saddle
salmon sand satin shell silver sky slate snow solar spark spruce squid star stone
storm sugar summit sunny swan tango teal thunder tiger toast topaz tulip tundra
turtle velvet violet walnut water whale willow window winter wolf yarrow zebra
""".split()


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- room name
def normalize_room(text: str) -> str:
    """Be forgiving when typing: "Maple Otter River 42" -> "maple-otter-river-42"."""
    return "-".join(text.strip().lower().replace("_", "-").split()).replace("--", "-")


def new_room_name() -> str:
    words = [secrets.choice(WORDS) for _ in range(3)]
    return "-".join(words) + f"-{secrets.randbelow(90) + 10}"


def resolve_room(cli_value: str | None) -> str:
    """Network name from --room (and remember it), else the saved one, else "general"."""
    if cli_value:
        cli_value = normalize_room(cli_value)
        if not ROOM_PATTERN.match(cli_value):
            raise SystemExit("Network names must be 3-64 letters, numbers or dashes (try: python -m peer.room)")
        Path(ROOM_FILE).write_text(cli_value + "\n")
        return cli_value
    try:
        saved = normalize_room(Path(ROOM_FILE).read_text())
    except OSError:
        saved = ""
    return saved if ROOM_PATTERN.match(saved) else DEFAULT_NETWORK


# ---------------------------------------------------------------- posting
def announce(room: str, name: str, ip: str, port: int) -> None:
    body = json.dumps({"name": name, "ip": ip, "port": port, "v": 1}).encode()
    req = urllib.request.Request(f"{ROOM_SERVICE_URL}/{topic(room)}", data=body, method="POST")
    with urllib.request.urlopen(req, timeout=10):
        pass


def start_announcer(room: str, name: str, port: int) -> None:
    """Post our address now, whenever it changes, and once an hour (background thread)."""

    def loop() -> None:
        last_ip, last_post = None, 0.0
        while True:
            ip = local_ip()
            if ip != "127.0.0.1" and (ip != last_ip or time.time() - last_post > ROOM_ANNOUNCE_EVERY):
                try:
                    announce(room, name, ip, port)
                    log(f"Posted to network '{room}': {name} is at {ip}:{port}")
                    last_ip, last_post = ip, time.time()
                except (urllib.error.URLError, OSError) as e:
                    log(f"Couldn't post to the network (will retry): {e}")
            time.sleep(30)

    threading.Thread(target=loop, daemon=True).start()


# ---------------------------------------------------------------- reading
def _is_lan_address(ip: str) -> bool:
    """Only accept private network addresses (10.x, 192.168.x, ...), never internet ones.

    Stops someone who learns the room name from redirecting prompts to their server.
    """
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def lookup(room: str) -> list[dict]:
    """Return [{"ip", "port", "name"}, ...] posted in the last 12 hours (newest per name)."""
    url = f"{ROOM_SERVICE_URL}/{topic(room)}/json?poll=1&since=12h"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            lines = resp.read().decode().splitlines()
    except (urllib.error.URLError, OSError) as e:
        log(f"Couldn't read network '{room}': {e}")
        return []

    latest: dict[str, dict] = {}
    for line in lines:
        try:
            event = json.loads(line)
            if event.get("event") != "message":
                continue
            info = json.loads(event["message"])
            entry = {"ip": str(info["ip"]), "port": int(info["port"]), "name": str(info["name"])[:40]}
        except (ValueError, KeyError, TypeError):
            continue  # ignore anything that isn't one of our announcements
        if _is_lan_address(entry["ip"]):
            latest[entry["name"]] = entry  # later messages replace earlier ones
    return list(latest.values())


def main() -> None:
    room = new_room_name()
    Path(ROOM_FILE).write_text(room + "\n")
    print(f"New network name: {room}")
    print(f"Saved to {ROOM_FILE}. Send this name privately to your teammates (not in the public repo!).")
    print(f"They run:  python -m peer.server --name THEIR_NAME --room {room}")


if __name__ == "__main__":
    main()
