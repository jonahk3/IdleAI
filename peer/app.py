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
from pathlib import Path

from nicegui import app, run, ui

from peer import carbon
from peer.api import Bridge, models_used, routing
from peer.config import COORDINATOR_MODEL, MODELS, RESULTS_DIR
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
        with ui.tabs().classes("ml-6") as tabs:
            t_ask = ui.tab("Ask", icon="chat_bubble_outline")
            t_host = ui.tab("Host", icon="dns")
            t_net = ui.tab("Network", icon="hub")
            t_res = ui.tab("Results", icon="description")
        ui.space()
        ollama_badge = ui.label().classes("idle-pill text-xs")
        hosting_badge = ui.label("● Hosting").classes(f"idle-pill bg-[{MINT}] text-xs")
        credits_pill = ui.label().classes("idle-pill font-semibold")
        with credits_pill:
            credits_tip = ui.tooltip()

    with ui.tab_panels(tabs, value=t_ask, animated=False).classes(f"w-full max-w-6xl mx-auto bg-[{PAPER}]"):

        # ================================================================ ASK
        with ui.tab_panel(t_ask).classes("gap-4"):
            with ui.row().classes("w-full gap-4 items-stretch no-wrap"):
                # model
                with ui.card().classes("idle-card w-1/2"):
                    ui.label("Model for the helpers").classes("idle-caption")
                    model_options = {m: f"{m}  ·  {'text + images' if i['images'] else 'text'}  ·  ~{i['size']}"
                                     for m, i in MODELS.items()}
                    ui.select(model_options, value=s["model"],
                              on_change=lambda e: (save("model", e.value), refresh_attachments())
                              ).classes("w-full").props("outlined dense")
                    model_about = ui.label().classes("text-sm opacity-80")
                    ui.label(f"Splitting and combining always use {COORDINATOR_MODEL} on this laptop. "
                             "Missing models download automatically.").classes("text-xs opacity-60")
                # how to send
                with ui.card().classes("idle-card w-1/2"):
                    ui.label("How to send").classes("idle-caption")
                    send_toggle = ui.toggle({"split": "Split across laptops", "one": "Just one laptop"},
                                            value=s["send_mode"],
                                            on_change=lambda e: (save("send_mode", e.value), one_row.set_visibility(e.value == "one"))
                                            ).props(f"no-caps unelevated toggle-color=primary color=white text-color=dark")
                    with ui.row().classes("w-full items-center no-wrap") as one_row:
                        peer_select = ui.select({}, value=None, label="Laptop",
                                                on_change=lambda e: save("one_peer", e.value or "")
                                                ).classes("grow").props("outlined dense")
                        ui.button("Find", icon="search", on_click=lambda: find_laptops()).props("flat dense")
                    one_row.set_visibility(s["send_mode"] == "one")
                    ui.label("Parts go to the laptops on the cleanest power grids first.").classes("text-xs opacity-60")

            # attachments
            with ui.card().classes("idle-soft w-full"):
                with ui.row().classes("w-full items-center"):
                    ui.label("Attachments").classes("idle-caption")
                    ui.label("PDF, images (for image models), text files").classes("text-xs opacity-60")
                    ui.space()
                    pdf_button = ui.button("Install PDF support", icon="download",
                                           on_click=lambda: install_pdf()).props("outline dense no-caps")
                with ui.row().classes("w-full items-start no-wrap gap-4"):
                    ui.upload(multiple=True, auto_upload=True, on_upload=lambda e: on_upload(e),
                              label="Drop files or click +").props("flat bordered color=primary").classes("w-72")
                    with ui.column().classes("grow gap-1"):
                        chips = ui.row().classes("gap-2")
                        attach_warning = ui.label().classes("text-sm text-red-700")

            # prompt
            with ui.card().classes("idle-card w-full"):
                prompt = ui.textarea(placeholder="Ask anything… e.g. “Give me the first 30 prime numbers”"
                                     ).classes("w-full text-base").props("borderless autogrow input-style=min-height:90px")
                prompt.on("keydown.ctrl.enter", lambda: send())
                prompt.on("keydown.meta.enter", lambda: send())
                with ui.row().classes("w-full items-center"):
                    ui.label("Ctrl+Enter to send").classes("text-xs opacity-50")
                    ui.space()
                    stop_button = ui.button("Stop", icon="stop", on_click=lambda: bridge.ask.stop()
                                            ).props("outline no-caps color=negative")
                    send_button = ui.button("Send", icon="send", on_click=lambda: send()
                                            ).props("unelevated no-caps color=dark")

            # live run view
            with ui.card().classes("idle-soft w-full") as run_card:
                with ui.row().classes("w-full items-center"):
                    ui.label("This run").classes("idle-caption")
                    run_spinner = ui.spinner(size="sm", color="primary")
                    ui.space()
                    run_status = ui.label().classes("text-sm opacity-70")
                with ui.row().classes("w-full no-wrap gap-6 items-start"):
                    with ui.column().classes("w-80 shrink-0"):
                        steps_box = ui.column().classes("w-full gap-1")
                    with ui.column().classes("grow gap-2"):
                        answer_box = ui.column().classes("w-full")
                with ui.expansion("Show log", icon="terminal").classes("w-full"):
                    ask_feed = LogFeed(bridge.ask)

        # ================================================================ HOST
        with ui.tab_panel(t_host).classes("gap-4"):
            with ui.card().classes("idle-card w-full"):
                ui.label("Share this laptop's AI").classes("idle-title text-xl")
                ui.label("You choose what this laptop downloads. The person asking chooses which model runs "
                         "(missing ones download automatically).").classes("text-sm opacity-70")
                with ui.row().classes("w-full items-end gap-6"):
                    ui.input("Your name", value=s["host_name"],
                             on_change=lambda e: save("host_name", e.value.strip())).props("outlined dense")
                    ui.switch("Fake model (testing, no Ollama)", value=s["host_fake"],
                              on_change=lambda e: save("host_fake", e.value))
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
                    ui.label("Room").classes("idle-caption")
                    with ui.row().classes("w-full items-center no-wrap"):
                        room_input = ui.input(value=s["room"], placeholder="e.g. maple-otter-river-42",
                                              on_change=lambda e: save("room", e.value.strip())
                                              ).classes("grow").props("outlined dense")
                        ui.button("New room", on_click=lambda: room_input.set_value(new_room_name())
                                  ).props("outline no-caps")
                    ui.label("Everyone on your team uses the same room name; keep it private.").classes("text-xs opacity-60")
                with ui.card().classes("idle-card w-1/2"):
                    ui.label("Location").classes("idle-caption")
                    locations = {"": "Not set (used last)"} | {
                        code: f"{code} - {info['name']} ({info['g_co2_per_kwh']} g CO2/kWh)"
                        for code, info in carbon.provinces().items()}
                    ui.select(locations, value=s["location"] or "",
                              on_change=lambda e: save("location", e.value or "")).classes("w-full").props("outlined dense")
                    ui.label("Fake for now · parts go to the cleanest grids first").classes("text-xs opacity-60")
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
                    ui.label("Finding laptops").classes("idle-caption")
                    ui.input("Extra IPs (if a laptop isn't in the room)", value=s["extra_peers"],
                             on_change=lambda e: save("extra_peers", e.value)).classes("w-full").props("outlined dense")
                    ui.switch("Also search the Wi-Fi", value=s["search_wifi"], on_change=lambda e: save("search_wifi", e.value))
                    ui.switch("Fake model for asking (testing)", value=s["ask_fake"], on_change=lambda e: save("ask_fake", e.value))
                    ui.button("Find laptops", icon="search", on_click=lambda: find_laptops()).props("unelevated no-caps color=dark")
                    peers_box = ui.column().classes("w-full gap-1")
                with ui.card().classes("idle-card w-1/2"):
                    ui.label("Check a laptop").classes("idle-caption")
                    with ui.row().classes("w-full items-center no-wrap"):
                        check_input = ui.input(placeholder="10.122.27.108 or 10.122.27.108:8770").classes("grow").props("outlined dense")
                        ui.button("Check", on_click=lambda: check_laptop()).props("outline no-caps")
                    check_output = ui.code("", language="text").classes("w-full text-xs")
                    status_line = ui.label().classes("text-sm opacity-70")

        # ================================================================ RESULTS
        with ui.tab_panel(t_res).classes("gap-4"):
            with ui.row().classes("w-full no-wrap gap-4 items-start"):
                with ui.card().classes("idle-card w-72 shrink-0"):
                    with ui.row().classes("w-full items-center"):
                        ui.label("Saved results").classes("idle-caption")
                        ui.space()
                        ui.button(icon="refresh", on_click=lambda: list_results()).props("flat dense round")
                    results_list = ui.column().classes("w-full gap-0")
                with ui.card().classes("idle-soft grow"):
                    result_view = ui.markdown("Pick a result on the left.").classes("w-full")

    # ---------------------------------------------------------------- behaviour
    def refresh_attachments() -> None:
        chips.clear()
        state = bridge.state()
        with chips:
            if not state["attachments"]:
                ui.label("No attachments").classes("text-sm opacity-50")
            for f in state["attachments"]:
                ui.chip(f["summary"] + (f" — {f['error']}" if f["error"] else ""), removable=True,
                        icon="error_outline" if f["error"] else "attach_file",
                        color="red-2" if f["error"] else "accent",
                        on_value_change=lambda e, i=f["id"]: (bridge.remove_file(i), refresh_attachments()))
        attach_warning.set_text(state["attachment_warning"] or "")
        needs_pdf = any(f["name"].lower().endswith(".pdf") for f in state["attachments"]) and not state["pdf_supported"]
        pdf_button.set_visibility(needs_pdf)

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

    def send() -> None:
        try:
            bridge.start_ask(prompt.value or "")
        except ValueError as err:
            ui.notify(str(err), type="warning")
            return
        ask_feed.reset()

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
        options = {p["label"]: f"{p['name']}  ·  {carbon.label(p.get('location'))}  ·  {p['address']}" for p in peers}
        peer_select.set_options(options, value=next(iter(options), None) if peer_select.value not in options else peer_select.value)
        peers_box.clear()
        with peers_box:
            if not peers:
                ui.label("No other laptops found. Check the room name, or add their IP above.").classes("text-sm opacity-60")
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

    def list_results() -> None:
        results_list.clear()
        folder = PROJECT_DIR / RESULTS_DIR
        files = sorted(folder.glob("*.md"), reverse=True)[:40] if folder.is_dir() else []
        with results_list:
            if not files:
                ui.label("Nothing saved yet.").classes("text-sm opacity-60")
            for f in files:
                ui.button(f.stem, on_click=lambda f=f: result_view.set_content(f.read_text(encoding="utf-8"))
                          ).props("flat no-caps align=left").classes("w-full")

    last = {"ask_len": -1, "running": None}

    def render_run() -> None:
        output = bridge.ask.output
        steps_box.clear()
        answer_box.clear()
        if not bridge.ask.command:
            with steps_box:
                ui.label("Nothing sent yet.").classes("text-sm opacity-50")
            return
        where = {f"part {r['part']}": r for r in routing(output)}
        with steps_box:
            for step in models_used(output):
                r = where.get(step["step"])
                with ui.row().classes("w-full items-center no-wrap gap-2 idle-soft px-3 py-2"):
                    if step["done"]:
                        ui.icon("check_circle", color="positive")
                    else:
                        ui.spinner(size="xs", color="primary")
                    with ui.column().classes("gap-0 grow"):
                        ui.label(step["step"].capitalize()).classes("font-semibold text-sm")
                        ui.label(f"{step['laptop']} · {step['model']}").classes("text-xs opacity-70")
                    if r:
                        ui.badge(f"{r['location']} {r['g_co2_per_kwh']:g} g", color=grid_colour(r["g_co2_per_kwh"]))
        result = bridge.ask_result(0)
        with answer_box:
            if result["final_answer"]:
                ui.label("Answer").classes("idle-caption")
                ui.markdown(result["final_answer"]).classes("w-full")
            elif bridge.ask.running:
                ui.label("Working… the answer appears here when the helpers are done.").classes("text-sm opacity-60")
            elif result["exit_code"] not in (None, 0):
                ui.label("The run stopped before it finished. Open the log below to see why.").classes("text-sm text-red-700")
            if result["co2"]:
                c = result["co2"]
                with ui.row().classes(f"items-center gap-2 bg-[{MINT}] px-3 py-1 rounded"):
                    ui.icon("eco", color="dark")
                    ui.label(f"About {c['grams']:.3f} g CO2 (estimate) — sending it all to the dirtiest laptop "
                             f"would have used {c['dirtiest_grams']:.3f} g").classes("text-sm")
            if result["results_file"]:
                ui.label(f"Saved as {result['results_file']} (see Results)").classes("text-xs opacity-60")

    def tick() -> None:
        state = bridge.state()
        # header
        c = state["credits"]
        credits_pill.set_text(f"Credits {c['balance']:+.3f}")
        credits_pill.classes(replace=f"idle-pill font-semibold {'text-[#b3261e]' if c['balance'] < 0 else 'text-[' + NAVY + ']'}")
        credits_tip.set_text(f"earned {c['earned']:.3f} · spent {c['spent']:.3f} · resets each launch · tracker only")
        hosting_badge.set_visibility(state["hosting"]["running"])
        ollama_badge.set_text("Ollama ✓" if state["ollama_running"] else "Ollama not running")
        model_about.set_text(MODELS[state["settings"]["model"]]["about"])
        # ask
        running = bridge.ask.running
        send_button.set_enabled(not running)
        stop_button.set_visibility(running)
        run_spinner.set_visibility(running)
        run_status.set_text(bridge.ask.command[:110] + ("…" if len(bridge.ask.command) > 110 else ""))
        ask_feed.update()
        if len(bridge.ask.output) != last["ask_len"] or running != last["running"]:
            if last["running"] and not running:
                list_results()  # a run just finished: show its saved file
            last["ask_len"], last["running"] = len(bridge.ask.output), running
            render_run()
        # host
        host_running = state["hosting"]["running"]
        host_button.set_text("Stop hosting" if host_running else "Start hosting")
        host_button.props(f"color={'negative' if host_running else 'dark'} icon={'stop' if host_running else 'play_arrow'}")
        host_earned.set_text(f"earned this session: {c['earned']:.3f} credits")
        host_feed.update()
        status_line.set_text(("Ollama is running" if state["ollama_running"] else "Ollama isn't running on this laptop")
                             + " · PDF support " + ("installed" if state["pdf_supported"] else "not installed"))

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
