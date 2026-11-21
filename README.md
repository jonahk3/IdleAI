# Idle — share your laptop's AI with teammates

Laptops on the same Wi-Fi share their AI models (via [Ollama](https://ollama.com)):
- **Chat** with the model on another laptop (`peer.chat`).
- **Split** one prompt across every laptop at once and collect the answers (`peer.split`).

Later this grows into a carbon-aware system that splits batch AI jobs across many
laptops (see "Where this is going" at the bottom).

```
  Laptop A (you)                          Laptop B (teammate)
  ┌──────────────┐    Wi-Fi, port 8765    ┌────────────────┐     ┌────────┐
  │ peer.chat    │ ─────────────────────▶ │ peer.server    │ ──▶ │ Ollama │
  │ (you type)   │ ◀── reply, word by word│ (checks request)│ ◀── │ model  │
  └──────────────┘                        └────────────────┘     └────────┘
```

Both laptops can run **both** pieces at the same time, so each can use the other's AI.

---

## 1. One-time setup (on EVERY laptop)

1. **Install Python 3.11 or newer** from <https://www.python.org/downloads/>.
   - Windows: tick **"Add Python to PATH"** in the installer.
   - Check it worked: open a terminal (Mac: *Terminal*; Windows: *PowerShell*) and type
     `python3 --version` (Mac) or `python --version` (Windows).
2. **Install Ollama** from <https://ollama.com/download> and open it once.
3. **Download the model** (about 2 GB, so use good Wi-Fi):
   ```bash
   ollama pull llama3.2:3b
   ```
   Check it works locally: `ollama run llama3.2:3b "say hi"`
4. **Get this code**: `git clone <this repo's URL>` then `cd Idle`.

No `pip install` is needed. This step uses only Python's built-in libraries.

> On Windows, type `python` wherever this guide says `python3`.

## 2. Lend your laptop (run the server)

On the laptop that will **run the AI** (say, Bob's):

```bash
python3 -m peer.server --name bob
```

You'll see something like:

```
[14:02:10] Ollama OK. Sharing models: llama3.2:3b
[14:02:10] Peer 'bob' is up.
[14:02:10] Tell your teammate to run:  python -m peer.chat --peer 192.168.1.42
```

Leave this window open. Every message that comes in gets logged here.

- **Mac:** if it asks "Allow incoming connections?", click **Allow**.
- **Windows:** if the firewall asks, allow access on **Private networks**.

## 3. Chat with the other laptop

On the **other** laptop (Alice's), use the IP address Bob's server printed:

```bash
python3 -m peer.chat --peer 192.168.1.42
```

```
Connected to 'bob' at http://192.168.1.42:8765
Models shared: llama3.2:3b
you> what's the capital of Manitoba?
bob> The capital of Manitoba is Winnipeg.
   (1.8s on bob)
```

Chat commands: `/models`, `/model NAME`, `/reset` (forget the conversation), `/quit`.

To go both ways, Alice also runs `peer.server`, and Bob runs `peer.chat --peer <Alice's IP>`.

## 4. Split one prompt across ALL the laptops

Every helper laptop runs `python3 -m peer.server` (step 2). Then, on the laptop
asking the question:

```bash
python3 -m peer.split "Write a beginner's guide to composting"
```

What happens:

1. **Find** other laptops on the Wi-Fi automatically (they answer a broadcast).
2. **Plan**: *your* Ollama splits the prompt into one part per laptop (yours included).
3. **Run**: all parts are sent out at the same time. If a laptop dies or disconnects,
   your laptop redoes its part, so you still get a full answer.
4. **Collect**: the answers are shown in order and saved to `results/<date-time>.md`.

```
[14:10:02] 3 device(s) available:
[14:10:02]   - this laptop (localhost)
[14:10:02]   - bob (192.168.1.42:8765)
[14:10:02]   - carol (192.168.1.57:8765)
[14:10:05]   part 1 -> this laptop: Explain what composting is and why it helps
[14:10:05]   part 2 -> bob: List what can and can't go in a compost bin
[14:10:05]   part 3 -> carol: Give step-by-step instructions to start a compost pile
...
[14:10:31] Done in 29.4s using 3 device(s). Saved to results/20261121-141031.md
```

Options:
- `--merge`: after collecting, your laptop rewrites the parts into one smooth answer.
- `--peers 192.168.1.42 192.168.1.57`: add laptops by IP (the IP their server printed), if
  auto-find doesn't work. Laptops that connect are **remembered** in `known_peers.json` and
  tried automatically next time, so you only need `--peers` once. `--forget` clears the list.
- `--no-discover`: don't search the Wi-Fi; only use `--peers` and remembered laptops.
- Leave out the prompt and it will ask you for one.

The laptop running `peer.split` needs Ollama + the model too, because it does the planning.

## Testing without Ollama

To check the network connection before Ollama is installed, run the server with a fake
model that just echoes what you type:

```bash
python3 -m peer.server --name bob --fake
```

You can also test everything on one laptop. Run the server in one terminal and
`python3 -m peer.chat --peer localhost` in another.

## Troubleshooting

| Problem | Fix |
|---|---|
| `Could not reach http://...` | Is the server running on the other laptop? Are you on the **same Wi-Fi**? Venue Wi-Fi often blocks laptops from seeing each other, so try a **phone hotspot**. Check the firewall prompt (see step 2). |
| `Models shared: (none ...)` | Ollama isn't running on the server laptop, or the model isn't downloaded yet. Run `ollama pull llama3.2:3b` there. |
| `model '...' is not allowed` | Only models in `peer/config.py` → `MODEL_ALLOWLIST` can run. |
| `peer.split` only finds "this laptop" | Auto-find only checks addresses that share your first three numbers (e.g. `10.0.3.x`). Big networks put laptops on different ones. Run once with `--peers <their IP>` and it's remembered. |
| Wrong IP printed | Find it yourself. Mac: `ipconfig getifaddr en0`. Windows: `ipconfig`, then look for "IPv4 Address". |

## Safety

- Ollama itself is **not** exposed to the network. Only `peer.server` is, and it only
  forwards chat messages.
- Only allowlisted models can run (`peer/config.py`).
- Requests are plain text data. Nothing received over the network is ever executed.
- There's no password yet, so anyone on the same Wi-Fi can chat with your model.
  Only run the server on networks you trust.

## Files

| File | What it does |
|---|---|
| `peer/config.py` | Port, Ollama address, model allowlist |
| `peer/server.py` | Receives chat requests from other laptops and passes them to local Ollama |
| `peer/chat.py` | Interactive chat client you run to talk to another laptop |
| `peer/split.py` | Splits one prompt into parts, runs them on all laptops at once, then collects and saves the answers |
| `peer/discovery.py` | Finds other laptops on the Wi-Fi (UDP broadcast on port 8766) |
| `peer/ollama.py` | Talks to the Ollama on this laptop |

## Where this is going

Planned next steps:
1. A **coordinator** that keeps track of every laptop that is "lending" (register + heartbeat).
2. **Batch jobs**: submit a CSV + prompt. The coordinator splits it into chunks and sends
   them to many laptops, then puts the results back together in order.
3. **Carbon-aware routing**: prefer laptops in provinces with clean power (QC, MB, BC).
4. **Credits** for lending compute, plus a live **dashboard**.
5. **Fault tolerance**: if a laptop dies, its chunks get reassigned.
