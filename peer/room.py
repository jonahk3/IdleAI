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

from peer.config import ROOM_ANNOUNCE_EVERY, ROOM_FILE, ROOM_SERVICE_URL
from peer.discovery import local_ip

ROOM_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- room name
def resolve_room(cli_value: str | None) -> str | None:
    """Room from --room (and remember it), else the one saved in room.txt."""
    if cli_value:
        if not ROOM_PATTERN.match(cli_value):
            raise SystemExit("Room names must be 8-64 letters, numbers, - or _ (try: python -m peer.room)")
        Path(ROOM_FILE).write_text(cli_value + "\n")
        return cli_value
    try:
        saved = Path(ROOM_FILE).read_text().strip()
    except OSError:
        return None
    return saved if ROOM_PATTERN.match(saved) else None


# ---------------------------------------------------------------- posting
def announce(room: str, name: str, ip: str, port: int) -> None:
    body = json.dumps({"name": name, "ip": ip, "port": port, "v": 1}).encode()
    req = urllib.request.Request(f"{ROOM_SERVICE_URL}/{room}", data=body, method="POST")
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
                    log(f"Posted to room '{room}': {name} is at {ip}:{port}")
                    last_ip, last_post = ip, time.time()
                except (urllib.error.URLError, OSError) as e:
                    log(f"Couldn't post to room (will retry): {e}")
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
    url = f"{ROOM_SERVICE_URL}/{room}/json?poll=1&since=12h"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            lines = resp.read().decode().splitlines()
    except (urllib.error.URLError, OSError) as e:
        log(f"Couldn't read room '{room}': {e}")
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
    room = "idle-" + secrets.token_hex(6)
    Path(ROOM_FILE).write_text(room + "\n")
    print(f"New room: {room}")
    print(f"Saved to {ROOM_FILE}. Send this name privately to your teammates (not in the public repo!).")
    print(f"They run:  python -m peer.server --name THEIR_NAME --room {room}")


if __name__ == "__main__":
    main()
