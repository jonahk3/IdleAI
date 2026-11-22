"""The Idle app: a modern window for everything Idle does.

    python -m pip install nicegui pywebview      (one time)
    python -m peer.app

It opens in its own desktop window (like the simple Tkinter window). Without
pywebview it opens in your browser instead (or use --browser). Either way only
this laptop can reach it. Like peer.gui it runs the same commands you could type
(peer.server, peer.split, peer.chat, peer.check) and shows what they print, plus
a clear view of where each part went, which model ran it, CO2 and credits.
"""
import argparse
import os
import re
import time
from pathlib import Path

from nicegui import app, run, ui

from peer import carbon
from peer.api import Bridge, co2_summary, final_answer, models_used, routing
from peer.config import COORDINATOR_MODEL, MODELS, RESULTS_DIR, STARTING_CREDITS
from peer.room import new_room_name
from peer.settings import PROJECT_DIR, load_settings, save_settings

ASSETS = Path(__file__).with_name("assets")

# Brand colours (from the Idle palette)
SAGE, MINT, PAPER, NAVY, CHARCOAL = "#89B0AE", "#BEE3DB", "#FAF9F9", "#031926", "#2C363F"

CSS = f"""
@import url('https://fonts.googleapis.com/css2?family=Lexend:wght@300;400;600;700&family=Inter:wght@400;500;600&display=swap');
body {{ background: {PAPER}; color: {NAVY}; font-family: Inter, system-ui, 'Segoe UI', Roboto, Arial, sans-serif; }}
.idle-title {{ font-family: Lexend, Poppins, 'Helvetica Neue', Arial, sans-serif; font-weight: 600; letter-spacing: -0.01em; }}
.idle-caption {{ text-transform: uppercase; letter-spacing: 0.18em; font-size: 0.7rem; color: {CHARCOAL}; opacity: .75; }}
.idle-card {{ background: white; border: 2px solid {NAVY}; border-radius: 4px; box-shadow: none; }}
.idle-soft {{ background: white; border: 1px solid #d7dfdf; border-radius: 4px; box-shadow: none; }}
.idle-pill {{ border: 1.5px solid {NAVY}; border-radius: 999px; padding: 2px 12px; font-size: .85rem; }}
.idle-log {{ background: {CHARCOAL}; color: #e8eeee; font-family: ui-monospace, Menlo, Consolas, monospace;
            font-size: 12px; border-radius: 4px; }}
.log-model {{ color: #c9a7ff; }} .log-credits {{ color: #8fe3a8; }} .log-carbon {{ color: {MINT}; }}
.log-cmd {{ color: #9cc9ff; }} .log-error {{ color: #ff9b9b; }}
.nicegui-markdown h1 {{ font-size: 1.6rem; font-family: Lexend, Poppins, Arial, sans-serif; margin: .2em 0 .4em; }}
.nicegui-markdown h2 {{ font-size: 1.25rem; font-family: Lexend, Poppins, Arial, sans-serif; margin: .8em 0 .3em; }}
.nicegui-markdown h3 {{ font-size: 1.05rem; font-family: Lexend, Poppins, Arial, sans-serif; margin: .8em 0 .2em; }}
.bubble-you {{ background: {MINT}; color: {NAVY}; border-radius: 14px 14px 2px 14px; padding: 10px 14px; max-width: 75%; }}
.bubble-idle {{ background: white; color: {NAVY}; border: 1.5px solid {NAVY}; border-radius: 14px 14px 14px 2px;
               padding: 10px 14px; max-width: 85%; }}
.bubble-idle p:last-child, .bubble-you p:last-child {{ margin-bottom: 0; }}
.chat-box, .chat-box * {{ box-sizing: border-box; }}
.chat-box {{ max-width: 100%; overflow-x: hidden; }}
.chat-log pre, .chat-log code {{ white-space: pre-wrap !important; word-break: break-word; }}
.q-tab {{ font-family: Lexend, Poppins, 'Helvetica Neue', Arial, sans-serif; text-transform: none; }}
.q-tab--active {{ color: {NAVY}; }}
"""

bridge = Bridge(token="")  # the same engine the web bridge uses, used directly (no network)


# ---------------------------------------------------------------- small helpers
def setting(key: str):
    return load_settings().get(key)


def save(key: str, value) -> None:
    s = load_settings()
    s[key] = value
    save_settings(s)


def grid_colour(g: float | None) -> str:
    if g is None:
        return "grey-6"
    return "teal-6" if g < 50 else "amber-8" if g <= 300 else "red-7"


def log_class(line: str) -> str | None:
    for marker, cls in (("$ python", "log-cmd"), ("[model]", "log-model"), ("[credits]", "log-credits"),
                        ("[carbon]", "log-carbon"), ("ERROR", "log-error"), ("[error]", "log-error")):
        if marker in line:
            return cls
    return None


class LogFeed:
    """Streams a job's output into a ui.log, a line at a time, with colours."""

    def __init__(self, job, height: str = "h-72") -> None:
        self.job, self.pos, self.pending = job, 0, ""
        self.view = ui.log(max_lines=2000).classes(f"idle-log w-full {height}")

    def reset(self) -> None:
        self.pos, self.pending = 0, ""
        self.view.clear()

    def update(self) -> None:
        snap = self.job.snapshot(self.pos)
        if snap["next"] < self.pos:  # the job restarted
            self.reset()
            snap = self.job.snapshot(0)
        self.pos = snap["next"]
        self.pending += snap["text"]
        *lines, self.pending = self.pending.split("\n")
        for line in lines:
            self.view.push(line, classes=log_class(line))
        if not snap["running"] and self.pending:
            self.view.push(self.pending, classes=log_class(self.pending))
            self.pending = ""


# ---------------------------------------------------------------- chat history
def parse_result(text: str) -> tuple[str, str, str]:
    """(prompt, attached, answer) from a saved results file (new chat format or the older one)."""
    if text.startswith("**You:**"):
        you, _, idle = text.partition("**Idle:**")
        you = you.replace("**You:**", "", 1).strip()
        attached = ""
        if "\n*Attached: " in you:
            you, _, attached = you.rpartition("\n*Attached: ")
            attached = attached.rstrip("*").strip()
        return you.strip(), attached, idle.strip()
    prompt = re.search(r"\*\*Prompt:\*\* (.*)", text)
    answer = re.search(r"## Final answer\n\n?(.*?)(\n## Parts|\Z)", text, re.S)
    attached = re.search(r"\*\*Attached:\*\* (.*)", text)
    return (prompt.group(1).strip() if prompt else "", attached.group(1).strip() if attached else "",
            answer.group(1).strip() if answer else text)


def chat_bubbles(prompt: str, attached: str, answer: str | None) -> None:
    with ui.row().classes("w-full justify-end"):
        with ui.column().classes("bubble-you gap-1"):
            ui.markdown(prompt)
            if attached:
                ui.label(f"📎 {attached}").classes("text-xs opacity-70")
    if answer is not None:
        with ui.row().classes("w-full justify-start no-wrap items-start gap-2"):
            ui.image("/assets/icon.png").classes("w-8 h-8 shrink-0")
            with ui.column().classes("bubble-idle gap-1"):
                ui.markdown(answer)


# ---------------------------------------------------------------- the page
conversation: list[dict] = []   # this session's chat: {"prompt", "attached", "answer", "meta", "details", ...}


@ui.page("/")
def index() -> None:
    ui.add_css(CSS)
    ui.colors(primary=SAGE, secondary=CHARCOAL, accent=MINT, dark=NAVY, positive="#4f8f86", negative="#b3261e")
    s = load_settings()

    # ----- header -----
    with ui.header().classes(f"bg-[{PAPER}] text-[{NAVY}] border-b-2 border-[{NAVY}] px-6 py-2 items-center gap-4"):
        ui.image("/assets/icon.png").classes("w-11 h-11")
        with ui.column().classes("gap-0"):
            ui.label("Idle.").classes(f"idle-title text-2xl text-[{SAGE}] leading-none")
            ui.label("model computing software").classes("idle-caption leading-none")
        with ui.tabs().classes("ml-6") as tabs:
            t_ask = ui.tab("Ask", icon="chat_bubble_outline")
            t_host = ui.tab("Host", icon="dns")
            t_net = ui.tab("Network", icon="hub")
            t_hist = ui.tab("Chat history", icon="history")
        ui.space()
        ollama_badge = ui.label().classes("idle-pill text-xs")
        hosting_badge = ui.label("● Hosting").classes(f"idle-pill bg-[{MINT}] text-xs")
        credits_pill = ui.label().classes("idle-pill font-semibold")
        with credits_pill:
            credits_tip = ui.tooltip()

    with ui.tab_panels(tabs, value=t_ask, animated=False).classes(f"w-full max-w-5xl mx-auto bg-[{PAPER}]"):

        # ================================================================ ASK (chat)
        with ui.tab_panel(t_ask).classes("gap-3"):
            with ui.row().classes("w-full items-center no-wrap gap-3"):
                ui.label("Model").classes("idle-caption")
                model_options = {m: f"{m}  ·  {'text + images' if i['images'] else 'text'}  ·  ~{i['size']}"
                                 for m, i in MODELS.items()}
                ui.select(model_options, value=s["model"],
                          on_change=lambda e: (save("model", e.value), refresh_attachments())
                          ).classes("w-80").props("outlined dense")
                model_about = ui.label().classes("text-sm opacity-70")
                ui.space()
                ui.label(f"Split across laptops on the cleanest grids · planned by {COORDINATOR_MODEL}"
                         ).classes("text-xs opacity-50")

            chat_scroll = ui.scroll_area().classes("w-full h-[56vh] idle-soft")
            with chat_scroll:
                chat_box = ui.column().classes("chat-box w-full gap-3 p-2")

            # prompt bar: attachments + paperclip + text + send
            with ui.card().classes("idle-card w-full gap-2"):
                with ui.row().classes("w-full items-center gap-2") as chips_row:
                    chips = ui.row().classes("gap-2")
                    attach_warning = ui.label().classes("text-sm text-red-700")
                    pdf_button = ui.button("Install PDF support", icon="download",
                                           on_click=lambda: install_pdf()).props("outline dense no-caps")
                uploader = ui.upload(multiple=True, auto_upload=True, on_upload=lambda e: on_upload(e)
                                     ).style("position:absolute; width:1px; height:1px; opacity:0; overflow:hidden")
                # (invisible: the paperclip button below opens its file picker)
                with ui.row().classes("w-full items-end no-wrap gap-2"):
                    # Opens the file picker right in the page: browsers only allow that directly from a click.
                    ui.button(icon="attach_file").props("flat round color=dark").tooltip(
                        "Add files (PDF, images, text)").on(
                        "click", js_handler=f'() => document.querySelector("#{uploader.html_id} input[type=file]").click()')
                    prompt = ui.textarea(placeholder="Message Idle…  (Ctrl+Enter to send)"
                                         ).classes("grow text-base").props("borderless autogrow")
                    prompt.on("keydown.ctrl.enter", lambda: send())
                    prompt.on("keydown.meta.enter", lambda: send())
                    stop_button = ui.button(icon="stop", on_click=lambda: bridge.ask.stop()
                                            ).props("round unelevated color=negative").tooltip("Stop")
                    send_button = ui.button(icon="arrow_upward", on_click=lambda: send()
                                            ).props("round unelevated color=dark").tooltip("Send")

        # ================================================================ HOST
        with ui.tab_panel(t_host).classes("gap-4"):
            with ui.card().classes("idle-card w-full"):
                ui.label("Share this laptop's AI").classes("idle-title text-xl")
                ui.label("You choose what this laptop downloads. The person asking chooses which model runs "
                         "(missing ones download automatically).").classes("text-sm opacity-70")
                with ui.row().classes("w-full items-end gap-6"):
                    ui.input("Your name", value=s["host_name"],
                             on_change=lambda e: save("host_name", e.value.strip())).props("outlined dense")
                    ui.space()
                    host_button = ui.button(on_click=lambda: toggle_host()).props("unelevated no-caps size=lg")
                ui.label("Models to download and share").classes("idle-caption mt-2")
                with ui.row().classes("gap-6"):
                    for m, info in MODELS.items():
                        ui.checkbox(f"{m}  (~{info['size']}{', images' if info['images'] else ''})",
                                    value=m in s["host_models"],
                                    on_change=lambda e, m=m: toggle_host_model(m, e.value))
            with ui.row().classes("w-full items-center"):
                ui.label("Server window").classes("idle-caption")
                ui.space()
                host_earned = ui.label().classes("text-sm")
            host_feed = LogFeed(bridge.host, "h-96")

        # ================================================================ NETWORK
        with ui.tab_panel(t_net).classes("gap-4"):
            with ui.row().classes("w-full gap-4 items-stretch no-wrap"):
                with ui.card().classes("idle-card w-1/2"):
                    ui.label("Network name").classes("idle-caption")
                    with ui.row().classes("w-full items-center no-wrap"):
                        room_input = ui.input(value=s["room"], placeholder="general",
                                              on_change=lambda e: save("room", e.value.strip() or "general")
                                              ).classes("grow").props("outlined dense")
                        ui.button("New private network", on_click=lambda: room_input.set_value(new_room_name())
                                  ).props("outline no-caps")
                    ui.label("Everyone on the same network name can use each other's laptops. 'general' is "
                             "open to all; make a private name for just your team.").classes("text-xs opacity-60")
                with ui.card().classes("idle-card w-1/2"):
                    ui.label("Location").classes("idle-caption")
                    locations = {"": "Not set (used last)"} | {
                        code: f"{code} - {info['name']} ({info['g_co2_per_kwh']} g CO2/kWh)"
                        for code, info in carbon.provinces().items()}
                    ui.select(locations, value=s["location"] or "",
                              on_change=lambda e: save("location", e.value or "")).classes("w-full").props("outlined dense")
                    ui.label("Sample data for now · parts go to the cleanest grids first").classes("text-xs opacity-60")
            with ui.card().classes("idle-soft w-full"):
                ui.label("Grid carbon intensity (g CO2/kWh)").classes("idle-caption")
                worst = max(i["g_co2_per_kwh"] for i in carbon.provinces().values())
                with ui.column().classes("w-full gap-1"):
                    for code, info in carbon.provinces().items():
                        g = info["g_co2_per_kwh"]
                        with ui.row().classes("w-full items-center no-wrap gap-2"):
                            ui.label(code).classes("w-8 font-semibold text-sm")
                            ui.linear_progress(value=max(g / worst, 0.01), show_value=False, size="10px",
                                               color=grid_colour(g)).classes("grow")
                            ui.label(f"{g}").classes("w-10 text-right text-sm")
            with ui.row().classes("w-full gap-4 items-stretch no-wrap"):
                with ui.card().classes("idle-card w-1/2"):
                    ui.label("Laptops on your network").classes("idle-caption")
                    ui.input("Extra IPs (if a laptop isn't on the network)", value=s["extra_peers"],
                             on_change=lambda e: save("extra_peers", e.value)).classes("w-full").props("outlined dense")
                    ui.switch("Also search the Wi-Fi", value=s["search_wifi"], on_change=lambda e: save("search_wifi", e.value))
                    ui.button("Find laptops", icon="search", on_click=lambda: find_laptops()).props("unelevated no-caps color=dark")
                    peers_box = ui.column().classes("w-full gap-1")
                with ui.card().classes("idle-card w-1/2"):
                    ui.label("Check a laptop").classes("idle-caption")
                    with ui.row().classes("w-full items-center no-wrap"):
                        check_input = ui.input(placeholder="10.122.27.108 or 10.122.27.108:8770").classes("grow").props("outlined dense")
                        ui.button("Check", on_click=lambda: check_laptop()).props("outline no-caps")
                    check_output = ui.code("", language="text").classes("w-full text-xs")
                    status_line = ui.label().classes("text-sm opacity-70")

        # ================================================================ CHAT HISTORY
        with ui.tab_panel(t_hist).classes("gap-4"):
            with ui.row().classes("w-full no-wrap gap-4 items-start"):
                with ui.card().classes("idle-card w-72 shrink-0"):
                    with ui.row().classes("w-full items-center"):
                        ui.label("Past chats").classes("idle-caption")
                        ui.space()
                        ui.button(icon="refresh", on_click=lambda: list_results()).props("flat dense round")
                    results_list = ui.column().classes("w-full gap-0")
                with ui.card().classes("idle-soft grow"):
                    history_view = ui.column().classes("w-full gap-3")
                    with history_view:
                        ui.label("Pick a chat on the left.").classes("opacity-60")

    # ---------------------------------------------------------------- chat
    live = {"status": None}  # the "working…" label of the message being answered

    def render_chat() -> None:
        chat_box.clear()
        with chat_box:
            if not conversation:
                with ui.column().classes("w-full items-center py-16 gap-2 opacity-70"):
                    ui.image("/assets/icon.png").classes("w-20 h-20")
                    ui.label("Ask anything.").classes("idle-title text-xl")
                    ui.label("Your prompt is split across the laptops on your network, cleanest power grids "
                             "first, and Idle replies here.").classes("text-sm text-center max-w-md")
            for turn in conversation:
                chat_bubbles(turn["prompt"], turn["attached"], turn["answer"])
                if turn["answer"] is None:
                    with ui.row().classes("w-full items-center gap-2 pl-10"):
                        ui.spinner("dots", size="lg", color="primary")
                        live["status"] = ui.label(turn.get("status") or "Thinking…").classes("text-sm opacity-70")
                else:
                    with ui.row().classes("w-full items-center gap-3 pl-10"):
                        ui.label(turn["meta"]).classes("text-xs opacity-60")
                    if turn.get("details"):
                        with ui.row().classes("w-full pl-10"):
                            with ui.expansion("Details", icon="tune").classes("w-full text-sm"):
                                for line in turn["details"]:
                                    ui.label(line).classes("text-xs")
                                ui.code(turn["log"], language="text").classes(
                                    "chat-log w-full text-xs max-h-64 overflow-auto")
        chat_scroll.scroll_to(percent=1.0)

    def describe_progress(output: str) -> str:
        steps = models_used(output)
        parts = [st for st in steps if st["step"].startswith("part")]
        if any(st["step"] == "combine" for st in steps):
            return "Putting the answers together…"
        if parts:
            done = sum(st["done"] for st in parts)
            where = ", ".join(sorted({st["laptop"] for st in parts}))
            return f"Working on {len(parts)} part(s): {done} done ({where})"
        if "Downloading" in output:
            return "Downloading the model (one time)…"
        if any(st["step"] == "plan" for st in steps) or "PLAN:" in output:
            return "Planning how to split it…"
        return "Finding laptops on your network…"

    def finish_turn(turn: dict) -> None:
        output = bridge.ask.output
        answer = final_answer(output)
        if not answer:
            last_lines = [l for l in output.strip().splitlines() if l.strip()][-3:]
            answer = "Sorry, that didn't finish. " + (" ".join(last_lines)[-300:] if last_lines else "")
        steps = models_used(output)
        where = {f"part {r['part']}": r for r in routing(output)}
        laptops = sorted({st["laptop"] for st in steps if st["step"].startswith("part")})
        co2 = co2_summary(output)
        spent = re.search(r"Credits for this prompt: ([\d.]+) spent", output)
        meta = [f"{len(laptops)} laptop(s)"] if laptops else []
        if co2:
            meta.append(f"~{co2['grams']:.3f} g CO2")
        if spent:
            meta.append(f"{float(spent.group(1)):.2f} credits")
        turn.update(answer=answer, meta=" · ".join(meta),
                    details=[f"{st['step']}: {st['laptop']} · {st['model']}"
                             + (f" · {where[st['step']]['location']} ({where[st['step']]['g_co2_per_kwh']:g} g/kWh)"
                                if st["step"] in where else "") for st in steps],
                    log=output)
        for file_id in turn.get("file_ids", []):  # attachments go with one message only
            bridge.remove_file(file_id)
        refresh_attachments()
        list_results()
        render_chat()

    def send() -> None:
        text = (prompt.value or "").strip()
        try:
            bridge.start_ask(text)
        except ValueError as err:
            ui.notify(str(err), type="warning")
            return
        files = [f for f in bridge.files.values() if not f.get("error")]
        conversation.append({"prompt": text, "attached": ", ".join(f["name"] for f in files),
                             "file_ids": [f["id"] for f in files], "answer": None, "status": "Thinking…"})
        prompt.set_value("")
        chips_row.set_visibility(False)
        render_chat()

    # ---------------------------------------------------------------- attachments
    def refresh_attachments() -> None:
        chips.clear()
        state = bridge.state()
        sending = bridge.ask.running and conversation and conversation[-1]["answer"] is None
        shown = [f for f in state["attachments"]
                 if not (sending and f["id"] in conversation[-1].get("file_ids", []))]
        with chips:
            for f in shown:
                ui.chip(f["summary"] + (f" — {f['error']}" if f["error"] else ""), removable=True,
                        icon="error_outline" if f["error"] else "attach_file",
                        color="red-2" if f["error"] else "accent",
                        on_value_change=lambda e, i=f["id"]: (bridge.remove_file(i), refresh_attachments()))
        attach_warning.set_text(state["attachment_warning"] or "" if shown else "")
        needs_pdf = any(f["name"].lower().endswith(".pdf") for f in shown) and not state["pdf_supported"]
        pdf_button.set_visibility(needs_pdf)
        chips_row.set_visibility(bool(shown))

    async def on_upload(e) -> None:
        data = await e.file.read()
        try:
            bridge.add_file_bytes(e.file.name, data)
        except ValueError as err:
            ui.notify(str(err), type="negative")
        refresh_attachments()
        e.sender.reset()

    async def install_pdf() -> None:
        pdf_button.props("loading")
        result = await run.io_bound(bridge.install_pdf)
        pdf_button.props(remove="loading")
        ui.notify("PDF support installed" if result["pdf_supported"] else
                  "Install failed: run  python -m pip install pypdf  in a terminal",
                  type="positive" if result["pdf_supported"] else "negative")
        refresh_attachments()

    # ---------------------------------------------------------------- host / network
    def toggle_host() -> None:
        if bridge.host.running:
            bridge.host.stop()
        else:
            host_feed.reset()
            bridge.host.start(bridge.host_command(load_settings()))

    def toggle_host_model(model: str, on: bool) -> None:
        current = [m for m in setting("host_models") if m != model]
        save("host_models", current + [model] if on else current)

    async def find_laptops() -> None:
        n = ui.notification("Looking for laptops…", spinner=True, timeout=None)
        result = await run.io_bound(bridge.find_peers, load_settings())
        n.dismiss()
        peers = result["peers"]
        peers_box.clear()
        with peers_box:
            if not peers:
                ui.label("No other laptops found. Check the network name, or add their IP above.").classes("text-sm opacity-60")
            for p in peers:
                g = carbon.intensity(p.get("location")) if p.get("location") else None
                with ui.row().classes("w-full items-center no-wrap gap-2 idle-soft px-3 py-1"):
                    ui.icon("laptop_mac").classes("opacity-70")
                    ui.label(p["name"]).classes("font-semibold")
                    ui.label(p["address"]).classes("text-xs opacity-60")
                    ui.space()
                    ui.badge(carbon.label(p.get("location")), color=grid_colour(g))
        ui.notify(f"Found {len(peers)} other laptop(s)")

    async def check_laptop() -> None:
        if not (check_input.value or "").strip():
            ui.notify("Type the laptop's IP first", type="warning")
            return
        check_output.set_content("Checking…")
        result = await run.io_bound(bridge.check, check_input.value.strip())
        check_output.set_content(result["log"].split("\n", 1)[-1].strip())

    # ---------------------------------------------------------------- history
    def show_result(file: Path) -> None:
        you, attached, answer = parse_result(file.read_text(encoding="utf-8"))
        history_view.clear()
        with history_view:
            ui.label(time.strftime("%d %b %Y, %H:%M", time.strptime(file.stem, "%Y%m%d-%H%M%S"))
                     if re.fullmatch(r"\d{8}-\d{6}", file.stem) else file.stem).classes("idle-caption")
            chat_bubbles(you, attached, answer)

    def list_results() -> None:
        results_list.clear()
        folder = PROJECT_DIR / RESULTS_DIR
        files = sorted(folder.glob("*.md"), reverse=True)[:60] if folder.is_dir() else []
        with results_list:
            if not files:
                ui.label("No chats yet.").classes("text-sm opacity-60")
            for f in files:
                try:
                    title = parse_result(f.read_text(encoding="utf-8"))[0].splitlines()[0][:40] or f.stem
                except (OSError, IndexError):
                    title = f.stem
                with ui.button(on_click=lambda f=f: show_result(f)).props("flat no-caps align=left").classes("w-full"):
                    with ui.column().classes("gap-0 items-start"):
                        ui.label(title).classes("text-sm text-left")
                        ui.label(f.stem).classes("text-xs opacity-50")

    # ---------------------------------------------------------------- every second
    def tick() -> None:
        state = bridge.state()
        c = state["credits"]
        credits_pill.set_text(f"Credits {c['balance']:.2f}")
        credits_pill.classes(replace=f"idle-pill font-semibold {'text-[#b3261e]' if c['balance'] < 0 else 'text-[' + NAVY + ']'}")
        credits_tip.set_text(f"started at {STARTING_CREDITS:.0f} · earned {c['earned']:.2f} · spent {c['spent']:.2f}"
                             " · resets each launch · tracker only")
        hosting_badge.set_visibility(state["hosting"]["running"])
        ollama_badge.set_text("Ollama ✓" if state["ollama_running"] else "Ollama not running")
        model_about.set_text(MODELS[state["settings"]["model"]]["about"])
        # chat
        running = bridge.ask.running
        send_button.set_enabled(not running)
        stop_button.set_visibility(running)
        if conversation and conversation[-1]["answer"] is None:
            turn = conversation[-1]
            if running:
                turn["status"] = describe_progress(bridge.ask.output)
                if live["status"] is not None:
                    live["status"].set_text(turn["status"])
            else:
                finish_turn(turn)
        # host
        host_running = state["hosting"]["running"]
        host_button.set_text("Stop hosting" if host_running else "Start hosting")
        host_button.props(f"color={'negative' if host_running else 'dark'} icon={'stop' if host_running else 'play_arrow'}")
        host_earned.set_text(f"earned this session: {c['earned']:.2f} credits")
        host_feed.update()
        status_line.set_text(("Ollama is running" if state["ollama_running"] else "Ollama isn't running on this laptop")
                             + " · PDF support " + ("installed" if state["pdf_supported"] else "not installed"))

    render_chat()
    refresh_attachments()
    list_results()
    tick()
    ui.timer(1.0, tick)


def free_port(preferred: int) -> int:
    from peer.ports import port_free
    return next((p for p in range(preferred, preferred + 30) if port_free(p)), preferred)


def main() -> None:
    parser = argparse.ArgumentParser(description="The Idle app.")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--browser", action="store_true", help="open in your browser instead of a window")
    parser.add_argument("--no-browser", action="store_true", help="don't open anything (for testing)")
    args = parser.parse_args()
    os.chdir(PROJECT_DIR)
    app.add_static_files("/assets", str(ASSETS))
    app.on_shutdown(lambda: (bridge.host.stop(), bridge.ask.stop()))

    native = not (args.browser or args.no_browser)
    if native:
        try:
            import webview  # noqa: F401  (pywebview draws the desktop window)
        except ImportError:
            print("For a desktop window, install pywebview:  python -m pip install pywebview")
            print("Opening in your browser for now.")
            native = False
    port = free_port(args.port)
    ui.run(host="127.0.0.1", port=port, title="Idle", favicon=str(ASSETS / "icon.png"),
           reload=False, native=native, window_size=(1300, 900) if native else None,
           show=not args.no_browser, show_welcome_message=not native)


if __name__ in {"__main__", "__mp_main__"}:
    main()
