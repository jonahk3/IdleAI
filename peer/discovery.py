"""Finding other laptops on the same Wi-Fi.

Each peer server listens on a UDP port. To find peers we shout "who's out
there?" to the whole network (a broadcast) and collect the replies.
"""
import json
import socket
import threading
import time

from peer.config import DISCOVERY_PORT

DISCOVER_MSG = b"IDLE_DISCOVER_V1"


def local_ip() -> str:
    """Best guess at this laptop's address on the Wi-Fi."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no data is sent; this just picks the right network card
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def start_responder(name: str, http_port: int) -> bool:
    """Answer discovery broadcasts in a background thread. Returns False if the port is busy."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", DISCOVERY_PORT))
    except OSError:
        return False
    reply = json.dumps({"name": name, "port": http_port}).encode()

    def loop() -> None:
        while True:
            data, addr = sock.recvfrom(1024)
            if data == DISCOVER_MSG:
                sock.sendto(reply, addr)

    threading.Thread(target=loop, daemon=True).start()
    return True


def discover(timeout: float = 1.5) -> list[dict]:
    """Return [{"ip", "port", "name"}, ...] for every peer server that answered."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.2)
    targets = {"255.255.255.255"}
    ip = local_ip()
    if ip != "127.0.0.1":
        targets.add(ip.rsplit(".", 1)[0] + ".255")  # e.g. 192.168.1.255 (typical home/hotspot network)
    for t in targets:
        try:
            sock.sendto(DISCOVER_MSG, (t, DISCOVERY_PORT))
        except OSError:
            pass

    found: dict[str, dict] = {}
    end = time.time() + timeout
    while time.time() < end:
        try:
            data, (addr, _) = sock.recvfrom(1024)
            info = json.loads(data)
            found[addr] = {"ip": addr, "port": int(info["port"]), "name": str(info["name"])}
        except socket.timeout:
            continue
        except (ValueError, KeyError, OSError):
            continue
    sock.close()
    return list(found.values())
