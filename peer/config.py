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
