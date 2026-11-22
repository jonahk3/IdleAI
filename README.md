# Idle — share your device's AI with teammates

Devices on the same Wi-Fi share their AI models (via [Ollama](https://ollama.com)):
- **Chat** with the model on another device (`peer.chat`).
- **Split** one prompt across every device at once and collect the answers (`peer.split`).

Later this grows into a carbon-aware system that splits batch AI jobs across many
devices (see "Where this is going" at the bottom).

```
  Device A (you)                          Device B (teammate)
  ┌──────────────┐    Wi-Fi, port 8765    ┌────────────────┐     ┌────────┐
  │ peer.chat    │ ─────────────────────▶ │ peer.server    │ ──▶ │ Ollama │
  │ (you type)   │ ◀── reply, word by word│ (checks request)│ ◀── │ model  │
  └──────────────┘                        └────────────────┘     └────────┘
```

Both devices can run **both** pieces at the same time, so each can use the other's AI.

---

## The Idle app (recommended)

A modern desktop window in the Idle colours for everything below: chatting, hosting, grid
names, your CO2 use, finding/checking devices, attachments, chat history, credits and CO2.

```bash
python3 -m pip install nicegui pywebview
```
```bash
python3 -m peer.app
```

It opens in its own window, like the simple window further down. (Without `pywebview` it opens in
your browser instead; `--browser` does that on purpose.) Closing the window also stops hosting.
It runs exactly the same commands as the simple window, so everything in this README applies.

- **Ask**: a chat, with your **chats listed down the right side** (*New chat* starts one; click a
  chat to open it and keep adding to it). Type a message (Ctrl+Enter sends), add files with the 📎
  button, and pick the helpers' model under the message box. Idle splits the prompt across the
  devices on your grid automatically (lowest-carbon electricity first). *Details* under each reply
  shows which devices and models did the work, and the full log.
  **Start hosting** shares this device's AI with your grid; **Server window** pops out its log:
  each task as it arrives, when it's finished and sent back, and the answer it sent.
- **Your CO2 use**: how much CO2 this device emits per hour while running a model (from its
  location), how that compares with the cleanest and dirtiest provinces, and this session's totals.
- **Settings**: your name, grid name (`general` by default; *New private grid* makes one for your
  team), device location, the models to download and share, and *Merge with AI*. *Advanced* has
  extra IPs and Wi-Fi search. If you're hosting, hosting restarts by itself to use new settings.

**How answers are put together:** by default each helper's answer is shown under its own heading
(e.g. the file it rated), instantly and exactly as written. *Merge with AI* (or `peer.split
--ai-merge`) has this device's model rewrite them into one text instead; that's slower and a small
model can mix answers up.
- The header shows your **credits** (this session), whether you're hosting, and whether Ollama runs.

## Simple window (no install)

```bash
python3 -m peer.gui
```

- **Top toggle:** *Ask* (send prompts) or *Host* (share my model). Hosting keeps running
  while you switch to Ask, so one device can do both.
- **Grid name:** `general` by default; type another (or press *New grid*) for a private one. It's remembered.
- **Host:** enter your name, press *Start hosting*.
- **Ask:** type a prompt at the bottom and press **Send** (or Ctrl+Enter). It's split across the
  devices automatically.

The window just runs the same commands described below; it shows each one in blue
(`$ python -m ...`), so you can copy it into a terminal. Settings are saved in `gui_settings.json`.

> Mac: if you get `No module named 'tkinter'`, install Python from python.org
> (Homebrew users: `brew install python-tk`).

## Models, images and files

**The person asking picks the helpers' model** (GUI: *Model for the helpers*, terminal:
`--model NAME`), and every helper runs exactly that model for its part. **Splitting and combining
always use `llama3.2:3b`** on the asking device (the "coordinator", `COORDINATOR_MODEL` in
`peer/config.py`, or `--coordinator NAME`), because smaller models often return empty or one-part
plans. It downloads automatically if missing. Attached files and images go to the helpers only. Hosts only choose
which models they download; they never decide which one runs. Lines marked `[model]` in the
output (purple in the GUI, plus a *Models used (debug)* bar) show which model ran each step and
on which device.

**Missing models download automatically**:
your device downloads the one you pick, and helper devices that don't have it are asked to
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
  device that attaches the PDF (helpers don't need it): `python -m pip install pypdf`, or press
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

Every device picks its **location** (Idle app: *Settings*; terminal: `--location QC`, `US-CA`, `FRA`
for both `peer.server` and `peer.split`). `peer/carbon_data.json` has the approximate grid carbon
intensity (g CO2/kWh) of every Canadian province/territory, every US state and national averages
for ~140 other countries; the *Your CO2 use* tab shows them on a world map (outlines from Natural
Earth, bundled so it works offline). Replace the data with live figures later by changing only
`peer/carbon.py`.

After the plan is ready, **code (not the AI) decides where the parts go**: all available devices,
yours included, are ranked by g CO2/kWh and the cleanest ones get the parts. Even a task that
isn't split (1 part) is sent to a helper if that helper's grid is cleaner than yours; on a tie
your device keeps it (it's free). A device with no location is used last.

`[carbon]` lines (teal in the GUI) show the ranking, where each part went, and a rough CO2
estimate (device power x time x grid intensity, `LAPTOP_WATTS` in `peer/config.py`) compared
with sending everything to the dirtiest available device.

## Ports

The server normally uses port 8765. If that port is taken when it starts:
- another peer server is running and answering there: it's left alone and the new server uses the
  next free port (8770, 8771, ...). You only need one server per device.
- a peer server is stuck there (accepts connections but never answers): it's closed and the new
  server takes 8765 back. Only processes whose command line contains `peer.server` are closed.
- another program has it: the new server uses the next free port.

Teammates on your grid find the new port automatically; the server window also prints the address
to type (e.g. `10.0.0.5:8770`).

## Credits

A simple tracker for now (later it will be tied to an account):
- **Hosting earns** credits for every request your device answers for a teammate.
- **Asking spends** credits for every part a teammate's device answers for you. Work done on your
  own device (planning, combining, your own part) is free.
- The amount is based on **tokens** (what the model read + wrote, reported by Ollama):
  100 credits per 1,000 tokens. If a model doesn't report tokens, **compute time** is used instead:
  300 credits per minute. Every session **starts with 500 credits**. Both rates are in `peer/config.py`; the maths is in `peer/credits.py`,
  which host and asker share, so both sides always agree on the amount.
- The tools print `[credits] earned/spent ...` lines (green in the GUI). The GUI adds them up
  under the top bar. It **starts at 0 every time the GUI opens**, can go negative, and doesn't
  block anything.

## 1. One-time setup (on EVERY device)

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

## 2. Lend your device (run the server)

On the device that will **run the AI** (say, Bob's):

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

## 3. Chat with the other device

On the **other** device (Alice's), use the IP address Bob's server printed:

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

## 4. Split one prompt across ALL the devices

Every helper device runs `python3 -m peer.server` (step 2). Then, on the device
asking the question:

```bash
python3 -m peer.split "Write a beginner's guide to composting"
```

What happens (the prompts for each step are in `peer/prompts.py`):

1. **Find** the other devices (grid name, remembered devices, or the Wi-Fi).
2. **Plan**: first *your* device's coordinator model (`llama3.2:3b`) counts how many independent
   pieces the task has (without being told how many devices exist); code uses that many devices at
   most. Then it writes exactly that many complete instructions, one per helper device, each producing a
   different piece of the answer (e.g. "all primes from 2 to 29", "all primes from 31 to 71", ...).
   It only uses as many devices as make sense: a short task gets one.
3. **Run**: all parts are sent out at once. Helpers only see their own instruction. If a device
   dies, yours redoes its part.
4. **Combine**: the helpers' answers are joined in order, each under its own heading (or, with
   `--ai-merge`, the coordinator model rewrites them into one answer).

The final answer is shown and saved, chat-style (your prompt + the answer), to `results/<date-time>.md`.

This works best for **batch** tasks: lists, sets of items, rows to process, several
independent questions.

```
[14:10:02] 3 device(s) available:
[14:10:02]   - this device (localhost)
[14:10:02]   - bob (192.168.1.42:8765)
[14:10:02]   - carol (192.168.1.57:8765)
[14:10:05]   part 1 -> this device: Explain what composting is and why it helps
[14:10:05]   part 2 -> bob: List what can and can't go in a compost bin
[14:10:05]   part 3 -> carol: Give step-by-step instructions to start a compost pile
...
[14:10:31] Done in 29.4s using 3 device(s). Saved to results/20261121-141031.md
```

Options:
- `--peers 192.168.1.42 192.168.1.57`: add devices by IP (the IP their server printed), if
  auto-find doesn't work. Devices that connect are **remembered** in `known_peers.json` and
  tried automatically next time, so you only need `--peers` once. `--forget` clears the list.
- `--no-discover`: don't search the Wi-Fi; only use `--peers` and remembered devices.
- Leave out the prompt and it will ask you for one.

The device running `peer.split` needs Ollama + the model too, because it does the planning.

## 5. Grids: find each other without typing IPs (university Wi-Fi)

University Wi-Fi blocks the automatic search. Instead, every device posts its address under a shared
**grid name** on [ntfy.sh](https://ntfy.sh) (a free public message board, no account), and
`peer.split` reads it.

- Everyone starts on the grid **`general`**, so devices find each other with no setup.
- For a private grid just for your team, make a name and share it **privately**
  (anyone who knows the name can use your devices):
  ```bash
  python3 -m peer.room
  ```
  Then use it everywhere: in the apps (*Grid name*), or `--room <name>` in the terminal
  (`peer.server` and `peer.split`). It's remembered in `room.txt`.

Servers re-post their address automatically if it changes. Give each device a different name.
Only private network addresses (like `10.x`) are used, so someone who learns the grid name
can't redirect your prompts to a server on the internet.

## Testing on one device

Run the server in one terminal and `python3 -m peer.chat --peer localhost` in another.

## Troubleshooting

| Problem | Fix |
|---|---|
| An answer says it only saw part of a file | The model's reading window (its *context*) was too small, so Ollama cut the start off. Idle now sizes it for each request (up to `MAX_CONTEXT` in `peer/config.py`, 32,768 tokens) and sends each helper only the files its part names. Very long files still won't fit: the log warns you. |
| `token repeat limit reached` in a server window | The model got stuck repeating itself and Ollama stopped it. Idle now keeps the answer up to that point (the repeated part is removed), and every answer is sent with a repeat penalty and a length limit (`ANSWER_OPTIONS` in `peer/config.py`). |
| The chat sits on "Merging responses…" | This device is writing the final answer; the timer shows it's still working. It's capped at `COMBINE_MAX_TOKENS`, and if merging fails you get the helpers' answers one after another. |
| A device can't be reached | Run `python -m peer.check <its IP>` (or press **Check device** in the GUI). It tests the connection step by step and says what to fix. |
| `Remote end closed connection without response` | Something on that device accepted the connection but hung up. Its server now handles a busy port by itself (see *Ports* below); otherwise check its window for an ERROR and run `git pull` there. |
| `Could not reach http://...` | Is the server running on the other device? Are you on the **same Wi-Fi**? Venue Wi-Fi often blocks devices from seeing each other, so try a **phone hotspot**. Check the firewall prompt (see step 2). |
| `Models shared: (none ...)` | Ollama isn't running on the server device, or the model isn't downloaded yet. Run `ollama pull llama3.2:3b` there. |
| `model '...' is not allowed` | Only models in `peer/config.py` → `MODEL_ALLOWLIST` can run. |
| `peer.split` only finds "this device" | Auto-find only checks addresses that share your first three numbers (e.g. `10.0.3.x`). Big networks put devices on different ones. Run once with `--peers <their IP>` and it's remembered. |
| `Couldn't read/post to the grid` | The device can't reach ntfy.sh (no internet, or the Wi-Fi login page isn't done). Open a browser to check. Remembered devices and `--peers` still work. |
| The grid lists a device but it's skipped | Its server stopped, or its address changed and it hasn't re-posted yet (it checks every 30 s). |
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
| `peer/server.py` | Receives chat requests from other devices and passes them to local Ollama |
| `peer/chat.py` | Interactive chat client you run to talk to another device |
| `peer/prompts.py` | Every instruction given to the models (plan, helper, rewrite, check), in one place to tune |
| `peer/split.py` | Splits one prompt into parts, runs them on all devices at once, then collects and saves the answers |
| `peer/app.py` | The Idle app (NiceGUI): modern browser window in the Idle colours |
| `peer/assets/` | Idle logo and icon |
| `peer/api.py` | The engine behind the Idle app (runs the same commands as the GUI); `python -m peer.api` also serves it as a local web API |
| `peer/settings.py` | Saved settings shared by the GUI and the bridge |
| `peer/gui.py` | The window: builds and runs the commands below for you, remembers settings |
| `peer/attachments.py` | Reads attached files and images and adds them to messages |
| `peer/carbon.py` + `peer/carbon_data.json` | Grid CO2 per kWh by province, US state and country, used to send parts to the cleanest devices |
| `peer/assets/world_map.geojson` | Simplified world map outlines (Natural Earth, public domain) for the CO2 map |
| `peer/check.py` | `python -m peer.check IP`: explains why a device can't be reached |
| `peer/credits.py` | How many credits a piece of work costs/earns (tokens, or compute time) |
| `peer/room.py` | Grid names: posts/reads device addresses on ntfy.sh so peers find each other |
| `peer/discovery.py` | Finds other devices on the Wi-Fi (UDP broadcast on port 8766) |
| `peer/ollama.py` | Talks to the Ollama on this device |

## Where this is going

Planned next steps:
1. A **coordinator** that keeps track of every device that is "lending" (register + heartbeat).
2. **Batch jobs**: submit a CSV + prompt. The coordinator splits it into chunks and sends
   them to many devices, then puts the results back together in order.
3. **Carbon-aware routing**: prefer devices in provinces with clean power (QC, MB, BC).
4. **Credits** for lending compute, plus a live **dashboard**.
5. **Fault tolerance**: if a device dies, its chunks get reassigned.

## Credits for bundled data

- Map outlines: [Natural Earth](https://www.naturalearthdata.com/) (public domain), simplified into
  `peer/assets/world_map.geojson`.
- `peer/assets/topojson/world_110m.json`: from [plotly.js](https://github.com/plotly/plotly.js) (MIT
  licence), bundled so the map doesn't need the internet.
- Grid carbon intensities in `peer/carbon_data.json` are approximate, rounded sample figures.
