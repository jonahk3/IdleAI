"""Settings shared by the peer server and the chat client."""
import os

# Port the peer server listens on (other devices connect to this).
PEER_PORT = 8765

# Where this device's own Ollama runs. Ollama is never exposed to the network
# directly; only our peer server talks to it.
OLLAMA_URL = os.environ.get("IDLE_OLLAMA_URL", "http://127.0.0.1:11434")  # not "localhost": slow to look up on Windows

# The models this project can use. These names are Ollama library names
# (https://ollama.com/library), so `ollama pull <name>` downloads them.
#   images: True if the model can look at pictures you attach.
#   size:   rough download size, shown in the GUI and logs.
MODELS = {
    "llama3.2:3b":   {"images": False, "size": "2 GB",  "about": "Meta Llama 3.2, small and fast"},
    "lfm3":          {"images": False, "size": "1 GB",  "about": "Liquid AI LFM3, tiny and fast"},
    "lfm3-omni":     {"images": True,  "size": "2 GB",  "about": "Liquid AI LFM3 Omni"},
    "mimo-v2.6":     {"images": True,  "size": "7 GB",  "about": "Xiaomi MiMo V2.6 9B, needs ~16 GB RAM"},
}

# Safety: the peer server only runs (and only downloads) models on this list,
# no matter what a remote device asks for.
MODEL_ALLOWLIST = list(MODELS)

DEFAULT_MODEL = "llama3.2:3b"

# The "coordinator" model plans (splits the prompt) and combines the answers on the asking
# device, whatever model you pick for the helpers. Small models like lfm3 often return empty
# or one-part plans, so this stays on llama. Override with peer.split --coordinator NAME.
COORDINATOR_MODEL = "llama3.2:3b"

# Attachments (files and images you add to a prompt).
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
MAX_FILE_CHARS = 200_000          # longer text files are cut off
MAX_IMAGE_BYTES = 10_000_000      # per image
MAX_IMAGES = 8

# Max time (seconds) to wait for Ollama to answer one message.
OLLAMA_TIMEOUT = 300

# UDP port used to find other devices on the Wi-Fi ("who's out there?").
DISCOVERY_PORT = 8766

# Folder where peer.split saves its answers.
RESULTS_DIR = "results"

# peer.split remembers devices that worked here, and tries them again next time.
KNOWN_PEERS_FILE = "known_peers.json"

# "Room" meeting point: devices post their address here so peers can find them
# on networks that block discovery (e.g. university Wi-Fi). Free, no account.
ROOM_SERVICE_URL = os.environ.get("IDLE_ROOM_SERVICE", "https://ntfy.sh")
ROOM_FILE = "room.txt"          # your grid name is remembered here (never commit it)
DEFAULT_GRID = "general"     # the grid everyone joins unless they pick another name
ROOM_ANNOUNCE_EVERY = 60 * 60   # re-post our address at least this often (seconds)

# The most tokens the planner may write. A plan is a few short instructions; this stops
# the planner from spending a long time working out the answer itself.
PLANNER_MAX_TOKENS = 800

# Credits (a tracker only for now: balances can go negative and nothing is blocked).
# Answering a teammate's request EARNS credits; having a teammate answer yours SPENDS them.
CREDITS_PER_1000_TOKENS = 100.0  # tokens = what the model read (input) + what it wrote (output)
CREDITS_PER_MINUTE = 300.0       # used instead when a model doesn't report token counts
STARTING_CREDITS = 500.0         # every session starts with this balance (until accounts exist)

# Carbon-aware routing: parts go to the devices on the cleanest power grids first.
# Locations are fake for now (each device picks one); data is in peer/provinces.json.
UNKNOWN_G_CO2_PER_KWH = 1000    # a device with no location is treated as the dirtiest
LAPTOP_WATTS = 30               # rough power draw while running a model (for CO2 estimates)

# Web app (Lovable UI) + local bridge (python -m peer.api).
API_PORT = 8800
# Web addresses allowed to use the bridge: Lovable-hosted apps and pages on this device.
API_ALLOWED_ORIGINS = os.environ.get(
    "IDLE_UI_ORIGINS",
    r"^(https://[a-z0-9-]+\.(lovable\.app|lovableproject\.com|lovable\.dev)|http://(localhost|127\.0\.0\.1)(:\d+)?)$",
)
# Where the web app is published (the bridge prints a link to it). Set after publishing.
UI_URL = os.environ.get("IDLE_UI_URL", "https://idle-ui.lovable.app")

# Safety settings for every normal answer, so small models don't loop or ramble forever
# (Ollama stops a looping model with "token repeat limit reached").
ANSWER_OPTIONS = {"repeat_penalty": 1.15, "repeat_last_n": 256, "num_predict": 1200}
COMBINE_MAX_TOKENS = 700        # the AI merge step's answer is capped too (shorter = faster)

# How much text a model can read at once (its "context", in tokens). Ollama's default is small
# (about 4k), and anything over it is silently cut off, so the size is worked out for each
# request (prompt + answer) up to this maximum. Bigger = more memory.
MAX_CONTEXT = 32768
