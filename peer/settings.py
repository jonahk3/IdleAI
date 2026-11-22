"""Saved settings, shared by the desktop GUI (peer.gui) and the web UI bridge (peer.api)."""
import json
import socket
from pathlib import Path

from peer import carbon
from peer.config import DEFAULT_MODEL, DEFAULT_GRID, MODELS, ROOM_FILE

SETTINGS_FILE = "gui_settings.json"
PROJECT_DIR = Path(__file__).resolve().parent.parent  # the folder that contains peer/

DEFAULTS = {
    "mode": "ask",              # "ask" or "host"
    "room": DEFAULT_GRID,    # grid name (shown as "Grid name")
    "model": DEFAULT_MODEL,
    "host_name": socket.gethostname(),
    "search_wifi": True,
    "extra_peers": "",
    "location": "",             # province code (fake for now), used for carbon-aware routing
    "host_models": [DEFAULT_MODEL],  # models to download and share while hosting
}


def load_settings() -> dict:
    settings = dict(DEFAULTS)
    saved: dict = {}
    try:
        saved = json.loads(Path(SETTINGS_FILE).read_text())
        settings.update({k: v for k, v in saved.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    if settings.get("location") not in carbon.provinces():
        settings["location"] = ""
    if settings["model"] not in MODELS:  # e.g. a model that was removed from the list
        settings["model"] = DEFAULT_MODEL
    settings["host_models"] = [m for m in settings.get("host_models") or [] if m in MODELS]
    if "room" not in saved:  # never chosen in a window yet: use the one the terminal tools saved
        try:
            settings["room"] = Path(ROOM_FILE).read_text().strip() or DEFAULT_GRID
        except OSError:
            settings["room"] = DEFAULT_GRID
    settings["room"] = settings["room"] or DEFAULT_GRID
    return settings


def save_settings(settings: dict) -> None:
    try:
        Path(SETTINGS_FILE).write_text(json.dumps(settings, indent=2))
    except OSError:
        pass


