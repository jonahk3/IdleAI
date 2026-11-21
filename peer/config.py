"""Settings shared by the peer server and the chat client."""

# Port the peer server listens on (other laptops connect to this).
PEER_PORT = 8765

# Where this laptop's own Ollama runs. Ollama is never exposed to the network
# directly; only our peer server talks to it.
OLLAMA_URL = "http://localhost:11434"

# Safety: the peer server only runs models on this list, no matter what a
# remote laptop asks for.
MODEL_ALLOWLIST = [
    "llama3.2:3b",
    "llama3.2:1b",
]

DEFAULT_MODEL = "llama3.2:3b"

# Max time (seconds) to wait for Ollama to answer one message.
OLLAMA_TIMEOUT = 300

# UDP port used to find other laptops on the Wi-Fi ("who's out there?").
DISCOVERY_PORT = 8766

# Folder where peer.split saves its answers.
RESULTS_DIR = "results"

# peer.split remembers laptops that worked here, and tries them again next time.
KNOWN_PEERS_FILE = "known_peers.json"
