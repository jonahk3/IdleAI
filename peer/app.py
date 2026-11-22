"""The Idle app: a modern window for everything Idle does.

    python -m pip install nicegui pywebview      (one time)
    python -m peer.app

It opens in its own desktop window (like the simple Tkinter window). Without
pywebview it opens in your browser instead (or use --browser). Either way only
this device can reach it. Like peer.gui it runs the same commands you could type
(peer.server, peer.split, peer.chat, peer.check) and shows what they print, plus
a clear view of where each part went, which model ran it, CO2 and credits.
"""
import argparse
import json
import os
import re
import time
from functools import lru_cache
from pathlib import Path

from nicegui import app, run, ui

from peer import carbon
from peer.api import Bridge, co2_summary, final_answer, models_used, routing
from peer.config import COORDINATOR_MODEL, MODELS, RESULTS_DIR, STARTING_CREDITS
from peer.room import ROOM_PATTERN, new_room_name, normalize_room
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
.bubble-you, .bubble-idle, .bubble-you *, .bubble-idle * {{ user-select: text; -webkit-user-select: text; cursor: text; }}
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


def copy_button(text: str) -> None:
    def copy() -> None:
        ui.clipboard.write(text)
        ui.notify("Copied", position="bottom", timeout=1000)
    ui.button(icon="content_copy", on_click=copy).props("flat dense round size=sm color=primary").tooltip("Copy")


def chat_bubbles(prompt: str, attached: str, answer: str | None) -> None:
    with ui.column().classes("w-full items-end gap-0"):
        with ui.column().classes("bubble-you gap-1"):
            ui.markdown(prompt)
            if attached:
                ui.label(f"📎 {attached}").classes("text-xs opacity-70")
        copy_button(prompt)
    if answer is not None:
        with ui.column().classes("w-full items-start gap-0"):
            with ui.column().classes("bubble-idle gap-1"):
                ui.markdown(answer)
            copy_button(answer)


# ---------------------------------------------------------------- world map
MAP_COLOURS = [[0.0, MINT], [0.15, SAGE], [0.45, "#e0a458"], [1.0, "#b3261e"]]  # low -> high g CO2/kWh


MAP_URL = "/assets/world_map.geojson"  # the browser downloads the outlines once and keeps them


@lru_cache(maxsize=1)
def map_shapes() -> dict:
    return json.loads(carbon.MAP_FILE.read_text(encoding="utf-8"))


@lru_cache(maxsize=64)
def world_map(selected: str | None) -> dict:
    """A Plotly choropleth: every country (US states and Canadian provinces on their own), coloured by
    grid g CO2/kWh, with this device's place outlined. Outlines are bundled, so it works offline."""
    shapes = map_shapes()  # only read here to know which shapes exist; the outlines go by URL
    data = carbon.provinces()
    codes = [f["id"] for f in shapes["features"] if f["id"] in data]
    traces = [{
        "type": "choropleth", "geojson": MAP_URL, "featureidkey": "id", "locations": codes,
        "z": [data[c]["g_co2_per_kwh"] for c in codes],
        "text": [f"{carbon.place_name(c)}: {data[c]['g_co2_per_kwh']:g} g CO2/kWh" for c in codes],
        "hoverinfo": "text", "zmin": 0, "zmax": 800, "colorscale": MAP_COLOURS,
        "marker": {"line": {"color": "white", "width": 0.4}},
        "colorbar": {"title": {"text": "g CO2/kWh"}, "thickness": 12, "len": 0.8},
    }, {  # places we have no data for
        "type": "choropleth", "geojson": MAP_URL, "featureidkey": "id",
        "locations": [f["id"] for f in shapes["features"] if f["id"] not in data],
        "z": [0] * sum(1 for f in shapes["features"] if f["id"] not in data),
        "colorscale": [[0, "#e6e9e9"], [1, "#e6e9e9"]], "showscale": False, "hoverinfo": "skip",
        "marker": {"line": {"color": "white", "width": 0.4}},
    }]
    if selected in data:
        traces.append({"type": "choropleth", "geojson": MAP_URL, "featureidkey": "id", "locations": [selected],
                       "z": [1], "colorscale": [[0, "rgba(0,0,0,0)"], [1, "rgba(0,0,0,0)"]], "showscale": False,
                       "hoverinfo": "skip", "marker": {"line": {"color": NAVY, "width": 2.5}}})
    return {"data": traces, "layout": {
        "geo": {"visible": False, "projection": {"type": "natural earth"}, "lataxis": {"range": [-58, 85]},
                "bgcolor": "rgba(0,0,0,0)"},
        "margin": {"l": 0, "r": 0, "t": 0, "b": 0}, "height": 420, "paper_bgcolor": "rgba(0,0,0,0)",
        "dragmode": False}, "config": {"displayModeBar": False, "scrollZoom": False,
                   # Plotly loads a base map file even when it's hidden; use our bundled copy, not the internet
                   "topojsonURL": "/assets/topojson/"}}


# ---------------------------------------------------------------- saved chats
CHATS_DIR = PROJECT_DIR / "chats"   # one JSON file per chat (a chat can hold many prompts)
conversation: list[dict] = []       # the open chat's turns: {"prompt", "attached", "answer", "meta", ...}
current_chat: dict = {"id": None}   # which saved chat is open (None = a new one)


def save_chat() -> None:
    done = [t for t in conversation if t.get("answer") is not None]
    if not done:
        return
    if not current_chat["id"]:
        current_chat["id"] = time.strftime("%Y%m%d-%H%M%S")
    keep = ("prompt", "attached", "answer", "meta", "details", "log", "results_file")
    turns = [{k: (t.get(k)[-20000:] if k == "log" and t.get(k) else t.get(k)) for k in keep} for t in done]
    CHATS_DIR.mkdir(exist_ok=True)
    (CHATS_DIR / f"{current_chat['id']}.json").write_text(json.dumps(
        {"id": current_chat["id"], "title": done[0]["prompt"][:60], "updated": time.time(), "turns": turns},
        indent=1), encoding="utf-8")


def saved_chats() -> list[dict]:
    """Saved chats (newest first), then older single prompts that aren't part of a chat."""
    chats = []
    for f in CHATS_DIR.glob("*.json") if CHATS_DIR.is_dir() else []:
        try:
            chats.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    chats.sort(key=lambda c: c.get("updated", 0), reverse=True)
    in_chats = {t.get("results_file") for c in chats for t in c.get("turns", [])}
    folder = PROJECT_DIR / RESULTS_DIR
    for f in sorted(folder.glob("*.md"), reverse=True)[:100] if folder.is_dir() else []:
        if f.name in in_chats:
            continue
        try:
            you, attached, answer = parse_result(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        chats.append({"id": None, "file": f.name, "title": (you.splitlines() or [f.stem])[0][:60],
                      "updated": f.stat().st_mtime,
                      "turns": [{"prompt": you, "attached": attached, "answer": answer, "meta": ""}]})
    return chats


# ---------------------------------------------------------------- the page


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
        with ui.tabs().classes("absolute left-1/2 -translate-x-1/2") as tabs:  # centred in the header
            t_ask = ui.tab("Ask", icon="chat_bubble_outline")
            t_co2 = ui.tab("Your CO2 use", icon="eco")
            t_set = ui.tab("Settings", icon="settings")
        ui.space()
        ollama_badge = ui.label().classes("idle-pill text-xs")
        hosting_badge = ui.label("● Hosting").classes(f"idle-pill bg-[{MINT}] text-xs")
        with ui.row().classes("idle-pill items-center gap-1 no-wrap") as credits_box:
            ui.icon("toll", color="primary").classes("text-lg")
            credits_pill = ui.label().classes("font-semibold")
            credits_tip = ui.tooltip()

    # ----- server window (pops out over the app) -----
    with ui.dialog().props("maximized=false") as server_dialog, ui.card().classes("idle-card w-[900px] max-w-[95vw] gap-2"):
        with ui.row().classes("w-full items-center"):
            ui.label("Server window").classes("idle-title text-xl")
            server_state = ui.label().classes("idle-pill text-xs")
            ui.space()
            host_earned = ui.label().classes("text-sm")
            ui.button(icon="close", on_click=lambda: server_dialog.close()).props("flat round")
        ui.label("Requests from other devices on your grid appear here: when a task arrives, and when it's "
                 "finished and the answer is sent back.").classes("text-xs opacity-60")
        host_feed = LogFeed(bridge.host, "h-[60vh]")

    with ui.tab_panels(tabs, value=t_ask, animated=False).classes(f"w-full max-w-7xl bg-[{PAPER}]"):

        # ================================================================ ASK (chat + history)
        with ui.tab_panel(t_ask).classes("gap-3"):
            with ui.row().classes("w-full no-wrap gap-4 items-stretch"):
                # ----- the chat (left)
                with ui.column().classes("grow gap-3 min-w-0"):
                    with ui.row().classes("w-full items-center no-wrap gap-3"):
                        host_button = ui.button(on_click=lambda: toggle_host()).props("unelevated no-caps").classes("shrink-0")
                        ui.button("Server window", icon="open_in_new", on_click=lambda: server_dialog.open()
                                  ).props("unelevated no-caps color=accent text-color=dark").classes("shrink-0")
                        ui.label("Hosting shares this device's AI with your grid. Prompts are split across your "
                                 "grid, lowest-carbon electricity first.").classes("text-xs opacity-50")

                    chat_scroll = ui.scroll_area().classes("w-full h-[58vh] idle-soft")
                    with chat_scroll:
                        chat_box = ui.column().classes("chat-box w-full gap-3 p-2")

                    # prompt bar: attachments + paperclip + text + send, then the model picker
                    with ui.card().classes("idle-card w-full gap-2"):
                        with ui.row().classes("w-full items-center gap-2") as chips_row:
                            chips = ui.row().classes("gap-2")
                            attach_warning = ui.label().classes("text-sm text-red-700")
                            pdf_button = ui.button("Install PDF support", icon="download",
                                                   on_click=lambda: install_pdf()).props("unelevated dense no-caps color=accent text-color=dark")
                        uploader = ui.upload(multiple=True, auto_upload=True, on_upload=lambda e: on_upload(e)
                                             ).style("position:absolute; width:1px; height:1px; opacity:0; overflow:hidden")
                        # (invisible: the paperclip button below opens its file picker)
                        with ui.row().classes("w-full items-end no-wrap gap-2"):
                            # Opens the file picker right in the page: browsers only allow that directly from a click.
                            ui.button(icon="attach_file").props("flat round color=primary").tooltip(
                                "Add files (PDF, images, text)").on(
                                "click", js_handler=f'() => document.querySelector("#{uploader.html_id} input[type=file]").click()')
                            prompt = ui.textarea(placeholder="Message Idle…  (Ctrl+Enter to send)"
                                                 ).classes("grow text-base").props("borderless autogrow")
                            prompt.on("keydown.ctrl.enter", lambda: send())
                            prompt.on("keydown.meta.enter", lambda: send())
                            stop_button = ui.button(icon="stop", on_click=lambda: bridge.ask.stop()
                                                    ).props("round unelevated color=negative").tooltip("Stop")
                            send_button = ui.button(icon="arrow_upward", on_click=lambda: send()
                                                    ).props("round unelevated color=primary").tooltip("Send")
                        with ui.row().classes("w-full items-center no-wrap gap-2"):
                            ui.label("Model").classes("idle-caption")
                            model_options = {m: f"{m}  ·  {'text + images' if i['images'] else 'text'}  ·  ~{i['size']}"
                                             for m, i in MODELS.items()}
                            ui.select(model_options, value=s["model"],
                                      on_change=lambda e: (save("model", e.value), refresh_attachments())
                                      ).classes("w-72").props("outlined dense")
                            model_about = ui.label().classes("text-xs opacity-60")
                            ui.space()
                            ui.label(f"planned by {COORDINATOR_MODEL}").classes("text-xs opacity-40")

                # ----- chat history (right side)
                with ui.card().classes("idle-card w-72 shrink-0 gap-2 self-stretch flex flex-col"):  # as tall as the chat
                    with ui.row().classes("w-full items-center justify-between no-wrap"):
                        ui.label("Chats").classes("idle-caption")
                        ui.button("New chat", icon="add", on_click=lambda: new_chat()
                                  ).props("unelevated dense no-caps color=accent text-color=dark padding='4px 12px'"
                                          ).classes("shrink-0")
                    with ui.scroll_area().classes("w-full").style("flex: 1 1 0; min-height: 0"):
                        history_list = ui.column().classes("w-full gap-1")

        # ================================================================ YOUR CO2 USE
        with ui.tab_panel(t_co2).classes("gap-4"):
            with ui.row().classes("w-full gap-4 items-stretch no-wrap"):
                with ui.card().classes("idle-card w-1/2 gap-2"):
                    ui.label("Your device").classes("idle-caption")
                    co2_where = ui.label().classes("text-sm")
                    co2_rate = ui.label().classes(f"idle-title text-4xl text-[{NAVY}] mt-2")
                    co2_rate_note = ui.label().classes("text-sm opacity-70")
                    co2_status = ui.label().classes("idle-pill text-xs self-start")
                    ui.label("Estimate: a typical device uses about 30 W while running a model. Location data "
                             "is sample data for now.").classes("text-xs opacity-50")
                with ui.card().classes("idle-card w-1/2 gap-3"):
                    ui.label("Compared with other places (g CO2 per hour)").classes("idle-caption")
                    compare_box = ui.column().classes("w-full gap-2")
                    ui.label("Helpers on the lowest-carbon electricity get your prompts first.").classes("text-xs opacity-60")
            with ui.card().classes("idle-card w-full gap-1"):
                ui.label("Electricity around the world (g CO2 per kWh)").classes("idle-caption")
                co2_map = ui.plotly(world_map(s["location"])).classes("w-full")
                ui.label("Countries use national averages; US states and Canadian provinces are shown on their own. "
                         "Your location is outlined. Approximate figures.").classes("text-xs opacity-60")
            with ui.card().classes("idle-soft w-full"):
                ui.label("This session").classes("idle-caption")
                with ui.row().classes("w-full gap-10"):
                    with ui.column().classes("gap-0"):
                        co2_device = ui.label().classes("idle-title text-2xl")
                        co2_device_note = ui.label().classes("text-xs opacity-60")
                    with ui.column().classes("gap-0"):
                        co2_prompts = ui.label().classes("idle-title text-2xl")
                        co2_prompts_note = ui.label().classes("text-xs opacity-60")

        # ================================================================ SETTINGS
        with ui.tab_panel(t_set).classes("gap-4"):
            with ui.card().classes("idle-card w-full gap-3"):
                ui.label("Settings").classes("idle-title text-xl")
                ui.label("Changes are saved right away. If you're hosting, hosting restarts by itself to use them."
                         ).classes("text-xs opacity-60")
                with ui.row().classes("w-full gap-6 items-start no-wrap"):
                    with ui.column().classes("w-1/2 gap-1"):
                        ui.label("Your name").classes("idle-caption")
                        ui.input(value=s["host_name"], placeholder="e.g. Jonah's laptop",
                                 on_change=lambda e: changed("host_name", e.value.strip())
                                 ).classes("w-full").props("outlined dense")
                        ui.label("Shown to other devices on your grid.").classes("text-xs opacity-60")
                    with ui.column().classes("w-1/2 gap-1"):
                        ui.label("Device location").classes("idle-caption")
                        locations = {"": "Not set"} | dict(sorted(
                            ((code, f"{carbon.place_name(code)} ({info['g_co2_per_kwh']:g} g CO2/kWh)")
                             for code, info in carbon.provinces().items()), key=lambda kv: kv[1]))
                        ui.select(locations, value=s["location"] or "", with_input=True,
                                  on_change=lambda e: changed("location", e.value or "")
                                  ).classes("w-full").props("outlined dense").tooltip("Type to search")
                ui.label("Grid name").classes("idle-caption mt-2")
                with ui.row().classes("w-full items-start no-wrap gap-2"):
                    grid_input = ui.input(value=s["room"], placeholder="general",
                                          validation={"3-64 letters, numbers or dashes":
                                                      lambda v: bool(ROOM_PATTERN.match(normalize_room(v or "general")))}
                                          ).classes("grow").props("outlined dense")
                    grid_input.on("keydown.enter", lambda: apply_grid())
                    grid_input.on("blur", lambda: apply_grid())
                    ui.button("Use this grid", on_click=lambda: apply_grid()).props("unelevated no-caps color=primary")
                    ui.button("New private grid", on_click=lambda: (grid_input.set_value(new_room_name()), apply_grid())
                              ).props("unelevated no-caps color=accent text-color=dark")
                ui.label("Every device on the same grid name can share its AI. 'general' is open to all; make a "
                         "private grid for just your team (share the name privately).").classes("text-xs opacity-60")
                ui.label("Models to download and share").classes("idle-caption mt-2")
                with ui.row().classes("gap-6"):
                    for m, info in MODELS.items():
                        ui.checkbox(f"{m}  (~{info['size']}{', images' if info['images'] else ''})",
                                    value=m in s["host_models"],
                                    on_change=lambda e, m=m: toggle_host_model(m, e.value))
                ui.label("You choose what this device downloads. The person asking chooses which model runs "
                         "(missing ones download automatically).").classes("text-xs opacity-60")
                ui.label("Answers").classes("idle-caption mt-2")
                ui.switch("Merge with AI (slower): rewrite the helpers' answers into one text",
                          value=s["ai_merge"], on_change=lambda e: save("ai_merge", e.value))
                ui.label("Off (recommended): each helper's answer is shown under its own heading, instantly and "
                         "exactly as written.").classes("text-xs opacity-60")
                with ui.expansion("Advanced", icon="tune").classes("w-full mt-2"):
                    ui.input("Extra IPs (devices that aren't found on the grid automatically)", value=s["extra_peers"],
                             on_change=lambda e: save("extra_peers", e.value)).classes("w-full").props("outlined dense")
                    ui.switch("Also search the Wi-Fi", value=s["search_wifi"], on_change=lambda e: save("search_wifi", e.value))
                    status_line = ui.label().classes("text-sm opacity-70")

    # The map is drawn while its tab is hidden, so redraw it at the right size when the tab opens.
    tabs.on_value_change(lambda e: co2_map.update() if e.value in (t_co2, "Your CO2 use") else None)

    # ---------------------------------------------------------------- chat
    live = {"status": None}  # the "working…" label of the message being answered

    def render_chat() -> None:
        chat_box.clear()
        with chat_box:
            if not conversation:
                with ui.column().classes("w-full items-center py-16 gap-2 opacity-70"):
                    ui.image("/assets/icon.png").classes("w-20 h-20")
                    ui.label("Ask anything.").classes("idle-title text-xl")
                    ui.label("Your prompt is split across the devices on your grid, lowest-carbon electricity "
                             "first, and Idle replies here.").classes("text-sm text-center max-w-md")
            for turn in conversation:
                chat_bubbles(turn["prompt"], turn["attached"], turn["answer"])
                if turn["answer"] is None:
                    with ui.row().classes("w-full items-center gap-2"):
                        ui.spinner("dots", size="lg", color="primary")
                        live["status"] = ui.label(turn.get("status") or "Thinking…").classes("text-sm opacity-70")
                else:
                    with ui.row().classes("w-full items-center gap-3"):
                        ui.label(turn["meta"]).classes("text-xs opacity-60")
                    if turn.get("details"):
                        with ui.row().classes("w-full"):
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
            return "Merging responses…"
        if parts:
            done = sum(st["done"] for st in parts)
            where = ", ".join(sorted({st["device"] for st in parts}))
            return f"Working on {len(parts)} part(s): {done} done ({where})"
        if "Downloading" in output:
            return "Downloading the model (one time)…"
        if any(st["step"] == "plan" for st in steps) or "PLAN:" in output:
            return "Planning how to split it…"
        return "Finding devices on your grid…"

    def finish_turn(turn: dict) -> None:
        output = bridge.ask.output
        answer = final_answer(output)
        if not answer:
            last_lines = [l for l in output.strip().splitlines() if l.strip()][-3:]
            answer = "Sorry, that didn't finish. " + (" ".join(last_lines)[-300:] if last_lines else "")
        steps = models_used(output)
        where = {f"part {r['part']}": r for r in routing(output)}
        devices = sorted({st["device"] for st in steps if st["step"].startswith("part")})
        co2 = co2_summary(output)
        spent = re.search(r"Credits for this prompt: ([\d.]+) spent", output)
        meta = [f"{len(devices)} device(s)"] if devices else []
        if co2:
            meta.append(f"~{co2['grams']:.3f} g CO2")
        if spent:
            meta.append(f"{float(spent.group(1)):.2f} credits")
        saved = re.search(r"Saved to (\S+\.md)", output)
        turn["results_file"] = Path(saved.group(1).replace("\\", "/")).name if saved else None
        turn.update(answer=answer, meta=" · ".join(meta),
                    details=[f"{st['step']}: {st['device']} · {st['model']}"
                             + (f" · {where[st['step']]['location']} ({where[st['step']]['g_co2_per_kwh']:g} g/kWh)"
                                if st["step"] in where else "") for st in steps],
                    log=output)
        for file_id in turn.get("file_ids", []):  # attachments go with one message only
            bridge.remove_file(file_id)
        refresh_attachments()
        save_chat()
        list_history()
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
                             "file_ids": [f["id"] for f in files], "answer": None, "status": "Thinking…",
                             "started": time.time()})
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

    # ---------------------------------------------------------------- host / grid
    def toggle_host() -> None:
        if bridge.host.running:
            bridge.host.stop()
        else:
            host_feed.reset()
            bridge.host.start(bridge.host_command(load_settings()))

    def toggle_host_model(model: str, on: bool) -> None:
        current = [m for m in setting("host_models") if m != model]
        changed("host_models", current + [model] if on else current)

    pending = {"timer": None}

    def changed(key: str, value) -> None:
        """Save a setting; if hosting, restart it shortly after (so typing doesn't restart it per key)."""
        save(key, value)
        if pending["timer"]:
            pending["timer"].cancel()
        pending["timer"] = ui.timer(1.5, restart_hosting_if_running, once=True)

    def restart_hosting_if_running() -> None:
        pending["timer"] = None
        if bridge.host.running:
            bridge.host.stop()
            bridge.host.wait(5)
            host_feed.reset()
            bridge.host.start(bridge.host_command(load_settings()))
            ui.notify(f"Hosting restarted on grid '{setting('room')}'")

    def apply_grid() -> None:
        name = normalize_room(grid_input.value or "") or "general"
        if not ROOM_PATTERN.match(name):
            ui.notify("Grid names are 3-64 letters, numbers or dashes", type="warning")
            return
        grid_input.set_value(name)
        if name != setting("room"):
            changed("room", name)
            ui.notify(f"Now on grid '{name}'")

    # ---------------------------------------------------------------- history (right side)
    def list_history() -> None:
        history_list.clear()
        chats = saved_chats()
        with history_list:
            if not chats:
                ui.label("No chats yet.").classes("text-sm opacity-60")
            for chat in chats:
                is_open = chat.get("id") and chat.get("id") == current_chat["id"]
                when = time.strftime("%d %b, %H:%M", time.localtime(chat.get("updated", 0)))
                n = len(chat.get("turns", []))
                with ui.button(on_click=lambda c=chat: open_chat(c)).props(
                        "flat no-caps align=left padding='6px 8px'").classes(
                        "w-full rounded" + (f" bg-[{MINT}]" if is_open else "")):
                    with ui.column().classes("gap-0 items-start w-full text-left"):
                        ui.label(chat.get("title") or "(untitled)").classes("text-sm text-left leading-tight")
                        ui.label(f"{when}" + (f" · {n} messages" if n > 1 else "")).classes("text-xs opacity-50")

    def open_chat(chat: dict) -> None:
        if bridge.ask.running:
            ui.notify("Wait for the current answer first (or press Stop)", type="warning")
            return
        conversation[:] = [dict(t) for t in chat.get("turns", [])]
        # A single prompt saved before chats existed becomes a chat once you add to it.
        current_chat["id"] = chat.get("id")
        if not chat.get("id") and chat.get("file"):
            current_chat["id"] = Path(chat["file"]).stem
            for t in conversation:
                t["results_file"] = chat["file"]
        render_chat()
        list_history()

    def new_chat() -> None:
        if bridge.ask.running:
            ui.notify("Wait for the current answer first (or press Stop)", type="warning")
            return
        conversation.clear()
        current_chat["id"] = None
        render_chat()
        list_history()
        prompt.run_method("focus")

    # ---------------------------------------------------------------- your CO2 use
    drawn: dict = {}

    def update_co2(c: dict) -> None:
        if c["g_per_kwh"] is None:
            co2_rate.set_text("Set your location")
            co2_rate_note.set_text("Pick where this device is to see its CO2.")
        else:
            co2_rate.set_text(f"{c['g_per_hour']:.2f} g CO2 / hour")
            co2_rate_note.set_text(f"while running a model · electricity in {carbon.place_name(c['location'])}: "
                                   f"{c['g_per_kwh']:g} g CO2/kWh")
        co2_status.set_text("● Working now" if c["working_now"] else "○ Idle (almost no extra CO2)")
        key = (c["g_per_hour"], c["location"])
        if drawn.get("compare") != key:  # only redraw when it changes
            drawn["compare"] = key
            co2_map.update_figure(world_map(c["location"]))
            compare_box.clear()
            name = lambda code: carbon.provinces()[code]["name"]
            rows = [(f"{name(c['cleanest']['code'])} (cleanest)", c["cleanest"]["g_per_hour"]),
                    (f"You ({name(c['location'])})" if c["location"] else "You (not set)", c["g_per_hour"]),
                    (f"{name(c['dirtiest']['code'])} (dirtiest)", c["dirtiest"]["g_per_hour"])]
            top = c["dirtiest"]["g_per_hour"] or 1
            with compare_box:
                for label, value in rows:
                    with ui.row().classes("w-full items-center no-wrap gap-2"):
                        ui.label(label).classes("w-40 text-sm font-semibold")
                        ui.linear_progress(value=max((value or 0) / top, 0.01), show_value=False, size="12px",
                                           color=grid_colour(None if value is None else value / c["watts"] * 1000)
                                           ).classes("grow")
                        ui.label("—" if value is None else f"{value:.2f}").classes("w-12 text-right text-sm")
        minutes = (c["hosting_seconds"] + c["own_seconds"]) / 60
        co2_device.set_text("—" if c["this_device_grams"] is None else f"~{c['this_device_grams']:.3f} g CO2")
        co2_device_note.set_text(f"this device: {minutes:.1f} min of AI work "
                                 f"({c['hosting_seconds'] / 60:.1f} hosting for others, {c['own_seconds'] / 60:.1f} for your prompts)")
        co2_prompts.set_text(f"~{c['prompts_grams']:.3f} g CO2")
        co2_prompts_note.set_text(f"your {c['prompts']} prompt(s), on every device that helped")

    # ---------------------------------------------------------------- every second
    def tick() -> None:
        state = bridge.state()
        c = state["credits"]
        credits_pill.set_text(f"Credits: {c['balance']:.2f}")
        credits_pill.classes(replace=f"font-semibold {'text-[#b3261e]' if c['balance'] < 0 else 'text-[' + NAVY + ']'}")
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
                elapsed = int(time.time() - turn.get("started", time.time()))
                turn["status"] = f"{describe_progress(bridge.ask.output)}  ·  {elapsed // 60}:{elapsed % 60:02d}"
                if live["status"] is not None:
                    live["status"].set_text(turn["status"])
            else:
                finish_turn(turn)
        # host
        host_running = state["hosting"]["running"]
        server_state.set_text("● Hosting" if host_running else "○ Not hosting")
        loc = state["settings"]["location"]
        co2_where.set_text(f"Location: {carbon.place_name(loc)} (change it in Settings)" if loc else
                           "Location not set: set it in Settings")
        host_button.set_text("Stop hosting" if host_running else "Start hosting")
        host_button.props(f"color={'negative' if host_running else 'primary'} icon={'stop' if host_running else 'play_arrow'}")
        host_earned.set_text(f"earned this session: {c['earned']:.2f} credits")
        host_feed.update()
        update_co2(state["co2"])
        status_line.set_text(("Ollama is running" if state["ollama_running"] else "Ollama isn't running on this device")
                             + " · PDF support " + ("installed" if state["pdf_supported"] else "not installed"))

    render_chat()
    refresh_attachments()
    list_history()
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
    if native:
        app.native.window_args["text_select"] = True  # the desktop window blocks selecting text by default
    port = free_port(args.port)
    ui.run(host="127.0.0.1", port=port, title="Idle", favicon=str(ASSETS / "icon.png"),
           reload=False, native=native, window_size=(1300, 900) if native else None,
           show=not args.no_browser, show_welcome_message=not native)


if __name__ in {"__main__", "__mp_main__"}:
    main()
