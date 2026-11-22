"""Settings shared by the peer server and the chat client."""
import os

# Port the peer server listens on (other laptops connect to this).
PEER_PORT = 8765

# Where this laptop's own Ollama runs. Ollama is never exposed to the network
# directly; only our peer server talks to it.
OLLAMA_URL = os.environ.get("IDLE_OLLAMA_URL", "http://localhost:11434")

# The models this project can use. These names are Ollama library names
# (https://ollama.com/library), so `ollama pull <name>` downloads them.
#   images: True if the model can look at pictures you attach.
#   size:   rough download size, shown in the GUI and logs.
MODELS = {
    "llama3.2:3b":   {"images": False, "size": "2 GB",  "about": "Meta Llama 3.2, small and fast"},
    "lfm3":          {"images": False, "size": "1 GB",  "about": "Liquid AI LFM3, tiny and fast"},
    "lfm3-omni":     {"images": True,  "size": "2 GB",  "about": "Liquid AI LFM3 Omni"},
    "mimo-v2.6":     {"images": True,  "size": "7 GB",  "about": "Xiaomi MiMo V2.6 9B, needs ~16 GB RAM"},
    "autotrust-jev": {"images": False, "size": "11 GB", "about": "AutoTrust JEV 9B decision model (choices/ratings)"},
}

# Safety: the peer server only runs (and only downloads) models on this list,
# no matter what a remote laptop asks for.
MODEL_ALLOWLIST = list(MODELS)

DEFAULT_MODEL = "llama3.2:3b"

# Attachments (files and images you add to a prompt).
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
MAX_FILE_CHARS = 200_000          # longer text files are cut off
MAX_IMAGE_BYTES = 10_000_000      # per image
MAX_IMAGES = 8

# Max time (seconds) to wait for Ollama to answer one message.
OLLAMA_TIMEOUT = 300

# UDP port used to find other laptops on the Wi-Fi ("who's out there?").
DISCOVERY_PORT = 8766

# Folder where peer.split saves its answers.
RESULTS_DIR = "results"

# peer.split remembers laptops that worked here, and tries them again next time.
KNOWN_PEERS_FILE = "known_peers.json"

# "Room" meeting point: laptops post their address here so peers can find them
# on networks that block discovery (e.g. university Wi-Fi). Free, no account.
ROOM_SERVICE_URL = os.environ.get("IDLE_ROOM_SERVICE", "https://ntfy.sh")
ROOM_FILE = "room.txt"          # your room name is remembered here (never commit it)
ROOM_ANNOUNCE_EVERY = 60 * 60   # re-post our address at least this often (seconds)
