"""Pick a port for the peer server, dealing with one that's already taken.

If the preferred port (8765) is busy:
  - another peer server is running there and answering -> leave it alone, use the next free port
  - a peer server is there but STUCK (accepts, never answers) -> close it, take the port back
  - some other program has it -> use the next free port
Only processes whose command line contains "peer.server" are ever closed.
"""
import json
import os
import re
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request

FALLBACK_PORTS = range(8770, 8800)


def port_free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Same socket options as the real server, so the answer matches what it will see.
        if os.name == "nt":
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # ignore a just-closed server
        s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def probe(port: int) -> tuple[str, str]:
    """What's on this port: ("peer", name) / ("stuck", why) / ("other", what)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/info", timeout=3) as resp:
            info = json.load(resp)
        return ("peer", str(info.get("name"))) if "models" in info else ("other", "unexpected reply")
    except urllib.error.HTTPError as e:
        try:
            if "server error on" in json.load(e).get("error", ""):
                return "stuck", "it answers with errors"
        except ValueError:
            pass
        return "other", f"HTTP {e.code}"
    except ValueError:
        return "other", "not a peer server"
    except (urllib.error.URLError, OSError) as e:
        return "stuck", f"it doesn't answer ({getattr(e, 'reason', e)})"


def listening_pids(port: int) -> list[int]:
    """Process IDs listening on this TCP port (best effort, uses the system's own tools)."""
    try:
        if os.name == "nt":
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=10).stdout
            pids = {int(line.split()[-1]) for line in out.splitlines()
                    if "LISTENING" in line and re.search(rf":{port}\s", line)}
        else:
            out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                                 capture_output=True, text=True, timeout=10).stdout
            pids = {int(p) for p in out.split()}
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    return [p for p in pids if p != os.getpid()]


def command_line(pid: int) -> str:
    try:
        if os.name == "nt":
            cmd = ["powershell", "-NoProfile", "-Command",
                   f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"]
        else:
            cmd = ["ps", "-p", str(pid), "-o", "command="]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def stop_process(pid: int) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=10)
        else:
            os.kill(pid, signal.SIGTERM)
            for _ in range(20):
                time.sleep(0.1)
                try:
                    os.kill(pid, 0)
                except OSError:
                    return
            os.kill(pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass


def next_free_port() -> int | None:
    return next((p for p in FALLBACK_PORTS if port_free(p)), None)


def choose_port(preferred: int, log) -> int:
    """The port to use (see the top of this file). Raises SystemExit if nothing is free."""
    if port_free(preferred):
        return preferred
    kind, detail = probe(preferred)
    if kind == "stuck":
        stuck = [p for p in listening_pids(preferred) if "peer.server" in command_line(p)]
        if stuck:
            log(f"Port {preferred} is held by a stuck peer server ({detail}). Closing it (process {', '.join(map(str, stuck))})...")
            for pid in stuck:
                stop_process(pid)
            for _ in range(30):
                if port_free(preferred):
                    log(f"Closed it. Using port {preferred}.")
                    return preferred
                time.sleep(0.1)
            log("Couldn't close it.")
        else:
            log(f"Port {preferred} is busy ({detail}), and it isn't a peer server, so it was left alone.")
    elif kind == "peer":
        log(f"Another peer server ('{detail}') is already running on port {preferred} on this laptop.")
        log("  You only need one per laptop; close the other one if you didn't mean to run two.")
    else:
        log(f"Port {preferred} is used by another program ({detail}).")
    port = next_free_port()
    if port is None:
        raise SystemExit(f"No free port found ({FALLBACK_PORTS.start}-{FALLBACK_PORTS.stop - 1}).")
    log(f"Using port {port} instead. Teammates in your room find it automatically.")
    return port
