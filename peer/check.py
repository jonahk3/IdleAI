"""Check why another laptop can't be reached, step by step.

    python -m peer.check 10.122.27.108
    python -m peer.check 10.122.27.108:8770     (if that laptop uses another port)
"""
import argparse
import http.client
import json
import socket

from peer.config import PEER_PORT
from peer.discovery import local_ip


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the connection to another laptop's peer server.")
    parser.add_argument("address", help="the other laptop's IP (as its server window shows it)")
    args = parser.parse_args()
    host, _, port_text = args.address.partition(":")
    port = int(port_text or PEER_PORT)
    print(f"This laptop is {local_ip()}. Checking {host}:{port} ...\n")

    # Step 1: can we open a connection at all?
    print("1. Opening a connection...", end=" ", flush=True)
    try:
        socket.create_connection((host, port), timeout=4).close()
        print("OK")
    except socket.timeout:
        print("TIMED OUT")
        print("\n-> Nothing answered. Usually: wrong IP, the laptops are on different networks/Wi-Fi,")
        print("   or a firewall on that laptop blocks Python. On Windows: 'Allow an app through Windows")
        print("   Firewall' -> tick Python for Private AND Public. On Mac: allow incoming connections.")
        return
    except OSError as e:
        print(f"FAILED ({e})")
        print("\n-> The laptop is there but nothing is listening on that port: its peer server isn't")
        print("   running, or it uses a different port (check the 'Tell your teammate' line in its window).")
        return

    # Step 2: does a peer server answer?
    print("2. Asking the peer server for its info...", end=" ", flush=True)
    conn = http.client.HTTPConnection(host, port, timeout=8)
    try:
        conn.request("GET", "/info")
        resp = conn.getresponse()
        body = resp.read(2000).decode("utf-8", "replace")
    except (http.client.RemoteDisconnected, ConnectionResetError) as e:
        print(f"CONNECTION CLOSED ({e})")
        print("\n-> Something on that laptop accepted the connection but hung up without answering.")
        print("   On THAT laptop:")
        print("   a) Look at its server window: is there an ERROR / Traceback? Send it to your team.")
        print("   b) Make sure only ONE server runs there (check other terminal windows and the GUI's")
        print("      Host mode). Close them all, then start one again.")
        print("   c) Run `git pull` there and restart the server (old code may not match).")
        print(f"   d) Another program may be using port {port}: start the server with --port 8770 and")
        print(f"      connect to {host}:8770 instead.")
        return
    except (OSError, http.client.HTTPException) as e:
        print(f"FAILED ({e})")
        return
    finally:
        conn.close()
    try:
        info = json.loads(body)
    except ValueError:
        print(f"GOT SOMETHING ELSE (HTTP {resp.status})")
        print(f"\n-> Another program is answering on port {port}, not a peer server:\n   {body[:200]!r}")
        print(f"   Start the peer server with --port 8770 and connect to {host}:8770.")
        return
    if resp.status != 200 or "error" in info:
        print(f"SERVER ERROR (HTTP {resp.status}): {info.get('error')}")
        print("\n-> The peer server is running but hit an error. Its window shows the details.")
        return
    print("OK")
    print(f"\n   Name:     {info.get('name')}")
    print(f"   Models:   {', '.join(info.get('models', [])) or '(none downloaded yet)'}")
    print(f"   Location: {info.get('location') or '(not set)'}")
    if info.get("downloading"):
        print(f"   Downloading: {info['downloading']}")
    print("\nAll good: this laptop can use it.")


if __name__ == "__main__":
    main()
