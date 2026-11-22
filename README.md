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

## The Idle app (recommended)

A modern desktop window in the Idle colours for everything below: chatting, hosting, network
names, locations, finding/checking laptops, attachments, chat history, credits and CO2.

```bash
python3 -m pip install nicegui pywebview
```
```bash
python3 -m peer.app
```

It opens in its own window, like the simple window further down. (Without `pywebview` it opens in
your browser instead; `--browser` does that on purpose.) Closing the window also stops hosting.
It runs exactly the same commands as the simple window, so everything in this README applies.

- **Ask**: a chat. Pick the helpers' model, type a message (Ctrl+Enter sends), and add files
  with the 📎 button. Idle splits the prompt across the laptops on your network automatically
  (cleanest grids first) and replies with the final answer. *Details* under each reply shows
  which laptops and models did the work, and the full log.
- **Host**: your name, the models to download and share, **Start hosting**, and the server log.
- **Network**: your network name (`general` by default), your location, the grid-intensity
  chart, extra IPs, *Find laptops*, and *Check a laptop* (explains why one can't be reached).
- **Chat history**: every past prompt and its answer.
- The header shows your **credits** (this session), whether you're hosting, and whether Ollama runs.

## Simple window (no install)

```bash
python3 -m peer.gui
```

- **Top toggle:** *Ask* (send prompts) or *Host* (share my model). Hosting keeps running
  while you switch to Ask, so one laptop can do both.
- **Network name:** `general` by default; type another (or press *New network*) for a private one. It's remembered.
- **Host:** enter your name, press *Start hosting*.
- **Ask:** type a prompt at the bottom and press **Send** (or Ctrl+Enter). It's split across the
  laptops automatically.

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

## Carbon-aware routing

Every laptop picks its **location** (GUI: *Location* in the settings; terminal: `--location QC`
for both `peer.server` and `peer.split`). Locations are sample data for now; the grid carbon intensity
of each province/territory is in `peer/provinces.json` (approximate, from Canada's National
Inventory Report). Replace it with live data later by changing only `peer/carbon.py`.

After the plan is ready, **code (not the AI) decides where the parts go**: all available laptops,
yours included, are ranked by g CO2/kWh and the cleanest ones get the parts. Even a task that
isn't split (1 part) is sent to a helper if that helper's grid is cleaner than yours; on a tie
your laptop keeps it (it's free). A laptop with no location is used last.

`[carbon]` lines (teal in the GUI) show the ranking, where each part went, and a rough CO2
estimate (laptop power x time x grid intensity, `LAPTOP_WATTS` in `peer/config.py`) compared
with sending everything to the dirtiest available laptop.

## Ports

The server normally uses port 8765. If that port is taken when it starts:
- another peer server is running and answering there: it's left alone and the new server uses the
  next free port (8770, 8771, ...). You only need one server per laptop.
- a peer server is stuck there (accepts connections but never answers): it's closed and the new
  server takes 8765 back. Only processes whose command line contains `peer.server` are closed.
- another program has it: the new server uses the next free port.

Teammates on your network find the new port automatically; the server window also prints the address
to type (e.g. `10.0.0.5:8770`).

## Credits

A simple tracker for now (later it will be tied to an account):
- **Hosting earns** credits for every request your laptop answers for a teammate.
- **Asking spends** credits for every part a teammate's laptop answers for you. Work done on your
  own laptop (planning, combining, your own part) is free.
- The amount is based on **tokens** (what the model read + wrote, reported by Ollama):
  100 credits per 1,000 tokens. If a model doesn't report tokens, **compute time** is used instead:
  300 credits per minute. Every session **starts with 500 credits**. Both rates are in `peer/config.py`; the maths is in `peer/credits.py`,
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

1. **Find** the other laptops (network name, remembered laptops, or the Wi-Fi).
2. **Plan**: first *your* laptop's coordinator model (`llama3.2:3b`) counts how many independent
   pieces the task has (without being told how many laptops exist); code uses that many laptops at
   most. Then it writes exactly that many complete instructions, one per helper laptop, each producing a
   different piece of the answer (e.g. "all primes from 2 to 29", "all primes from 31 to 71", ...).
   It only uses as many laptops as make sense: a short task gets one.
3. **Run**: all parts are sent out at once. Helpers only see their own instruction. If a laptop
   dies, yours redoes its part.
4. **Combine**: the coordinator model writes the final answer from the helpers' answers (fixing numbering,
   duplicates and gaps).

The final answer is shown and saved, chat-style (your prompt + the answer), to `results/<date-time>.md`.

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

## 5. Network names: find each other without typing IPs (university Wi-Fi)

University Wi-Fi blocks the automatic search. Instead, every laptop posts its address under a shared
**network name** on [ntfy.sh](https://ntfy.sh) (a free public message board, no account), and
`peer.split` reads it.

- Everyone starts on the network **`general`**, so laptops find each other with no setup.
- For a private network just for your team, make a name and share it **privately**
  (anyone who knows the name can use your laptops):
  ```bash
  python3 -m peer.room
  ```
  Then use it everywhere: in the apps (*Network name*), or `--room <name>` in the terminal
  (`peer.server` and `peer.split`). It's remembered in `room.txt`.

Servers re-post their address automatically if it changes. Give each laptop a different name.
Only private network addresses (like `10.x`) are used, so someone who learns the network name
can't redirect your prompts to a server on the internet.

## Testing on one laptop

Run the server in one terminal and `python3 -m peer.chat --peer localhost` in another.

## Troubleshooting

| Problem | Fix |
|---|---|
| A laptop can't be reached | Run `python -m peer.check <its IP>` (or press **Check laptop** in the GUI). It tests the connection step by step and says what to fix. |
| `Remote end closed connection without response` | Something on that laptop accepted the connection but hung up. Its server now handles a busy port by itself (see *Ports* below); otherwise check its window for an ERROR and run `git pull` there. |
| `Could not reach http://...` | Is the server running on the other laptop? Are you on the **same Wi-Fi**? Venue Wi-Fi often blocks laptops from seeing each other, so try a **phone hotspot**. Check the firewall prompt (see step 2). |
| `Models shared: (none ...)` | Ollama isn't running on the server laptop, or the model isn't downloaded yet. Run `ollama pull llama3.2:3b` there. |
| `model '...' is not allowed` | Only models in `peer/config.py` → `MODEL_ALLOWLIST` can run. |
| `peer.split` only finds "this laptop" | Auto-find only checks addresses that share your first three numbers (e.g. `10.0.3.x`). Big networks put laptops on different ones. Run once with `--peers <their IP>` and it's remembered. |
| `Couldn't read/post to the network` | The laptop can't reach ntfy.sh (no internet, or the Wi-Fi login page isn't done). Open a browser to check. Remembered laptops and `--peers` still work. |
| The network lists a laptop but it's skipped | Its server stopped, or its address changed and it hasn't re-posted yet (it checks every 30 s). |
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
| `peer/app.py` | The Idle app (NiceGUI): modern browser window in the Idle colours |
| `peer/assets/` | Idle logo and icon |
| `peer/api.py` | The engine behind the Idle app (runs the same commands as the GUI); `python -m peer.api` also serves it as a local web API |
| `peer/settings.py` | Saved settings shared by the GUI and the bridge |
| `peer/gui.py` | The window: builds and runs the commands below for you, remembers settings |
| `peer/attachments.py` | Reads attached files and images and adds them to messages |
| `peer/carbon.py` + `peer/provinces.json` | Grid CO2 per kWh by province, used to send parts to the cleanest laptops |
| `peer/check.py` | `python -m peer.check IP`: explains why a laptop can't be reached |
| `peer/credits.py` | How many credits a piece of work costs/earns (tokens, or compute time) |
| `peer/room.py` | Network names: posts/reads laptop addresses on ntfy.sh so peers find each other |
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
