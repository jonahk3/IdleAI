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

## Easiest: use the window (GUI)

```bash
python3 -m peer.gui
```

- **Top toggle:** *Ask* (send prompts) or *Host* (share my model). Hosting keeps running
  while you switch to Ask, so one laptop can do both.
- **Room:** type your team's room (or press *New room*). It's remembered.
- **Host:** enter your name, press *Start hosting*.
- **Ask:** choose *Split across all laptops* or *Just one laptop* (press *Find laptops* to fill
  the list), type a prompt at the bottom and press **Send** (or Ctrl+Enter).

The window just runs the same commands described below; it shows each one in blue
(`$ python -m ...`), so you can copy it into a terminal. Settings are saved in `gui_settings.json`.

> Mac: if you get `No module named 'tkinter'`, install Python from python.org
> (Homebrew users: `brew install python-tk`).

## Models, images and files

**The person asking picks the helpers' model** (GUI: *Model for the helpers*, terminal:
`--model NAME`), and every helper runs exactly that model for its part. **Splitting and combining
always use `llama3.2:3b`** on the asking laptop (the "coordinator", `COORDINATOR_MODEL` in
`peer/config.py`, or `--coordinator NAME`), because smaller models often return empty or one-part
plans. It downloads automatically if missing. Attached files and images go to the helpers only. Hosts only choose
which models they download; they never decide which one runs. Lines marked `[model]` in the
output (purple in the GUI, plus a *Models used (debug)* bar) show which model ran each step and
on which laptop.

**Missing models download automatically**:
your laptop downloads the one you pick, and helper laptops that don't have it are asked to
download it in the background (they join in once it's done; their window shows progress).
In Host mode, tick the models to download and share up front (`peer.server --models ...`).

| Model | Reads | Download | Notes |
|---|---|---|---|
| `llama3.2:3b` | text | ~2 GB | default, fast |
| `lfm3` | text | ~1 GB | tiny and fast |
| `lfm3-omni` | text + **images** | ~2 GB | |
| `mimo-v2.6` | text + **images** | ~7 GB | needs ~16 GB RAM |

The newer models need a recent Ollama. Update it from <https://ollama.com/download> if a
download fails. The list lives in `peer/config.py` (`MODELS`).

**Attachments** (GUI: *Attach files...*; terminal: `--file a.png notes.txt`):
- Images (png, jpg, webp, gif, bmp) need a model that reads images.
- **PDFs**: their text is extracted and added to the prompt. This needs the `pypdf` library on the
  laptop that attaches the PDF (helpers don't need it): `python -m pip install pypdf`, or press
  *Install PDF support* in the GUI. Scanned PDFs (no selectable text) and password-protected
  PDFs are refused; use a screenshot with an image model instead.
- Text files (txt, md, csv, json, html, code...) are added to the prompt.
- Word, Excel, audio and video aren't supported yet (save Word/Excel as PDF).
- **Only the helpers get the files** (in full). The planner only gets each file's name, type and
  length (e.g. `Resume.pdf (PDF, 2 pages, 4,812 characters)`), and is told every helper has all
  of them, so parts can name the file to work on. The combine step gets no files, so it can only
  use what the helpers wrote.
- Answers are still text only.

## Credits

A simple tracker for now (later it will be tied to an account):
- **Hosting earns** credits for every request your laptop answers for a teammate.
- **Asking spends** credits for every part a teammate's laptop answers for you. Work done on your
  own laptop (planning, combining, your own part) is free.
- The amount is based on **tokens** (what the model read + wrote, reported by Ollama):
  1 credit per 1,000 tokens. If a model doesn't report tokens, **compute time** is used instead:
  3 credits per minute. Both rates are in `peer/config.py`; the maths is in `peer/credits.py`,
  which host and asker share, so both sides always agree on the amount.
- The tools print `[credits] earned/spent ...` lines (green in the GUI). The GUI adds them up
  under the top bar. It **starts at 0 every time the GUI opens**, can go negative, and doesn't
  block anything.

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

No `pip install` is needed, except `pypdf` if you want to attach PDFs (see below).

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

What happens (the prompts for each step are in `peer/prompts.py`):

1. **Find** the other laptops (room, remembered laptops, or the Wi-Fi).
2. **Plan**: *your* laptop's coordinator model (`llama3.2:3b`) writes one complete instruction per helper laptop, each producing a
   different piece of the answer (e.g. "all primes from 2 to 29", "all primes from 31 to 71", ...).
   It only uses as many laptops as make sense: a short task gets one.
3. **Run**: all parts are sent out at once. Helpers only see their own instruction. If a laptop
   dies, yours redoes its part.
4. **Combine**: the coordinator model writes the final answer from the helpers' answers (fixing numbering,
   duplicates and gaps).

Results are shown and saved to `results/<date-time>.md`.

This works best for **batch** tasks: lists, sets of items, rows to process, several
independent questions.

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
- `--peers 192.168.1.42 192.168.1.57`: add laptops by IP (the IP their server printed), if
  auto-find doesn't work. Laptops that connect are **remembered** in `known_peers.json` and
  tried automatically next time, so you only need `--peers` once. `--forget` clears the list.
- `--no-discover`: don't search the Wi-Fi; only use `--peers` and remembered laptops.
- Leave out the prompt and it will ask you for one.

The laptop running `peer.split` needs Ollama + the model too, because it does the planning.

## 5. Rooms: find each other without typing IPs (university Wi-Fi)

University Wi-Fi blocks the automatic search. Instead, every laptop posts its address to a
shared **room** on [ntfy.sh](https://ntfy.sh) (a free public message board, no account), and
`peer.split` reads the room.

1. **One person** makes a room (a random name), and sends it to the team **privately**
   (group chat, *not* the public repo, since anyone with the name can read and post):
   ```bash
   python3 -m peer.room
   ```
2. **Every helper laptop** starts its server with the room:
   ```bash
   python3 -m peer.server --name bob --room idle-1a2b3c4d5e6f
   ```
   It should print `Posted to room ...: bob is at 10.x.x.x:8765`.
3. **The asking laptop** runs split with the room:
   ```bash
   python3 -m peer.split "your prompt" --room idle-1a2b3c4d5e6f
   ```

The room name is saved in `room.txt`, so after the first time you can leave out `--room`.
Servers re-post their address automatically if it changes. Give each laptop a different `--name`.

Safety: only private network addresses (like `10.x`) from the room are used, so someone
who learns the room name can't redirect your prompts to a server on the internet.

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
| `Couldn't read/post to room` | The laptop can't reach ntfy.sh (no internet, or the Wi-Fi login page isn't done). Open a browser to check. Remembered laptops and `--peers` still work. |
| Room lists a laptop but it's skipped | Its server stopped, or its address changed and it hasn't re-posted yet (it checks every 30 s). |
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
| `peer/prompts.py` | Every instruction given to the models (plan, helper, rewrite, check), in one place to tune |
| `peer/split.py` | Splits one prompt into parts, runs them on all laptops at once, then collects and saves the answers |
| `peer/gui.py` | The window: builds and runs the commands below for you, remembers settings |
| `peer/attachments.py` | Reads attached files and images and adds them to messages |
| `peer/credits.py` | How many credits a piece of work costs/earns (tokens, or compute time) |
| `peer/room.py` | Rooms: posts/reads laptop addresses on ntfy.sh so peers find each other |
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
