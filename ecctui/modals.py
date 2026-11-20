"""ecctui modal screens (confirm, run, wizard, output, palette args, help, tour, failure, log)."""
from __future__ import annotations

import json
import shlex
import time
from pathlib import Path
from typing import Any

from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (Button, Checkbox, Input, Label, RichLog, Select,
                             Static, TextArea, Tree)

from . import ecc
from .theme import BAD, DIM, FG, OK, ORANGE, SOFT, WARN

AMBER = "#FF8F00"


def fmt_val(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    if isinstance(v, (list, dict)):
        return json.dumps(v)
    return str(v)


def colorize_log_line(line: str) -> Text:
    if any(k in line for k in ("Traceback", "Error", "ERROR", "FAIL", "error=")):
        return Text(line, style=f"bold {BAD}")
    if "WARNING" in line or "Warning" in line or "warning" in line:
        return Text(line, style=WARN)
    if "Success" in line or "✓" in line:
        return Text(line, style=OK)
    return Text(line, style=FG)


# ----------------------------------------------------------------- JSON tree --

def _fill(node, data: Any, depth: int = 0) -> None:
    if isinstance(data, dict):
        for k, v in list(data.items())[:300]:
            if isinstance(v, (dict, list)) and v:
                n = node.add(Text.assemble((str(k), f"bold {SOFT}"), (f"  {'{…}' if isinstance(v, dict) else f'[{len(v)}]'}", DIM)),
                             expand=depth < 1)
                _fill(n, v, depth + 1)
            else:
                node.add_leaf(Text.assemble((f"{k}: ", f"bold {SOFT}"), (fmt_val(v), FG)))
    elif isinstance(data, list):
        for i, v in enumerate(data[:300]):
            if isinstance(v, (dict, list)) and v:
                label = f"[{i}]"
                if isinstance(v, dict):
                    for key in ("key", "param", "component", "gate", "workspace_id", "report", "step", "record"):
                        if key in v:
                            label += f" {v[key]}"
                            break
                n = node.add(Text(label, style=f"bold {AMBER}"), expand=depth < 1 and i < 2)
                _fill(n, v, depth + 1)
            else:
                node.add_leaf(Text(f"[{i}] {fmt_val(v)}", style=FG))
        if len(data) > 300:
            node.add_leaf(Text(f"… {len(data) - 300} more", style=DIM))
    else:
        node.add_leaf(Text(fmt_val(data), style=FG))


class JsonTree(Tree):
    def show(self, data: Any, label: str = "json") -> None:
        self.clear()
        self.root.set_label(Text(label, style=f"bold {ORANGE}"))
        _fill(self.root, data)
        self.root.expand()


# -------------------------------------------------------------------- modals --


class ConfirmModal(ModalScreen):
    """Returns (confirmed, checkbox_state)."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, body: str, ok: str = "Confirm", check: str | None = None,
                 cmd: str | None = None, danger: bool = False):
        super().__init__()
        self._t, self._b, self._ok, self._check, self._cmd, self._danger = title, body, ok, check, cmd, danger

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog small"):
            yield Label(self._t, classes="dlg-title")
            yield Static(self._b, classes="dlg-body")
            if self._cmd:
                yield Static(Text(self._cmd, style=SOFT), classes="cmd-preview")
            if self._check:
                yield Checkbox(self._check, id="chk")
            with Horizontal(classes="buttons"):
                yield Button(self._ok, variant="error" if self._danger else "primary", id="ok")
                yield Button("Cancel", id="cancel")

    @on(Button.Pressed, "#ok")
    def _ok_(self) -> None:
        chk = self.query_one("#chk", Checkbox).value if self._check else False
        self.dismiss((True, chk))

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss((False, False))

    def action_cancel(self) -> None:
        self.dismiss((False, False))



MODES = [
    ("full run", "full"),
    ("--resume", "resume"),
    ("--from STEP", "from"),
    ("--only STEP", "only"),
    ("--only STEP --force", "only_force"),
    ("--from A --to B", "range"),
]




class RunModal(ModalScreen):
    """Run control dialog with a live command preview."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, project: str, workspace: str, presets: list[str], steps: list[str],
                 mode: str = "full", step: str | None = None):
        super().__init__()
        self.project, self.workspace = project, workspace
        self.presets, self.steps = presets, steps
        self.mode0, self.step0 = mode, step

    def compose(self) -> ComposeResult:
        steps = [(s, s) for s in self.steps] or [("(none)", "")]
        with VerticalScroll(classes="dialog wide"):
            yield Label("▶ Run flow", classes="dlg-title")
            yield Label("workspace")
            yield Input(self.workspace, id="ws")
            yield Label("preset")
            yield Select([("(project default)", "")] + [(p, p) for p in self.presets],
                         value="", allow_blank=False, id="preset")
            yield Label("mode")
            yield Select(MODES, value=self.mode0, allow_blank=False, id="mode")
            with Horizontal(id="steprow"):
                with Vertical(id="from_box"):
                    yield Label("from / only step")
                    yield Select(steps, value=self.step0 if self.step0 in self.steps else steps[0][1],
                                 allow_blank=False, id="from")
                with Vertical(id="to_box"):
                    yield Label("to step (inclusive)")
                    yield Select(steps, value=steps[-1][1], allow_blank=False, id="to")
            yield Label("--set key=value  (one per line; fresh/overwrite runs only)")
            yield TextArea("", id="sets", classes="sets")
            yield Checkbox("--overwrite (recreate workspace)", id="overwrite")
            yield Label("command")
            yield Static("", id="preview", classes="cmd-preview")
            with Horizontal(classes="buttons"):
                yield Button("▶ Run", variant="primary", id="run")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self._sync()

    def build(self) -> list[str]:
        q = self.query_one
        ws = q("#ws", Input).value.strip() or "default"
        a = ["run", "--project", self.project, "--workspace", ws]
        preset = q("#preset", Select).value
        if preset:
            a += ["--preset", str(preset)]
        mode = q("#mode", Select).value
        frm, to = q("#from", Select).value, q("#to", Select).value
        if mode == "resume":
            a += ["--resume"]
        elif mode == "from" and frm:
            a += ["--from", str(frm)]
        elif mode == "only" and frm:
            a += ["--only", str(frm)]
        elif mode == "only_force" and frm:
            a += ["--only", str(frm), "--force"]
        elif mode == "range" and frm and to:
            a += ["--from", str(frm), "--to", str(to)]
        for line in q("#sets", TextArea).text.splitlines():
            if line.strip():
                a += ["--set", line.strip()]
        if q("#overwrite", Checkbox).value:
            a += ["--overwrite"]
        return a + ["--plain"]

    def _sync(self) -> None:
        mode = self.query_one("#mode", Select).value
        self.query_one("#from_box").display = mode in ("from", "only", "only_force", "range")
        self.query_one("#to_box").display = mode == "range"
        self.query_one("#steprow").display = mode in ("from", "only", "only_force", "range")
        self.query_one("#preview", Static).update(Text(ecc.cmdline(self.build()), style=SOFT))

    @on(Select.Changed)
    def _c1(self, _) -> None:
        self._sync()

    @on(Input.Changed)
    def _c2(self, _) -> None:
        self._sync()

    @on(Checkbox.Changed)
    def _c3(self, _) -> None:
        self._sync()

    @on(TextArea.Changed)
    def _c4(self, _) -> None:
        self._sync()

    @on(Button.Pressed, "#run")
    def _run(self) -> None:
        self.dismiss(self.build())

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)



class ArgsModal(ModalScreen):
    """Command-palette argument prompt."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, cmd: str, prefill: str):
        super().__init__()
        self.cmd, self.prefill = cmd, prefill

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(f"⚙ ecc {self.cmd}", classes="dlg-title")
            yield Label("arguments (shell-style quoting)")
            yield Input(self.prefill, id="args")
            yield Static("", id="preview", classes="cmd-preview")
            with Horizontal(classes="buttons"):
                yield Button("Run", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self._prev()
        self.query_one("#args").focus()

    def argv(self) -> list[str] | None:
        try:
            return self.cmd.split() + shlex.split(self.query_one("#args", Input).value)
        except ValueError:
            return None

    @on(Input.Changed)
    def _prev(self) -> None:
        a = self.argv()
        self.query_one("#preview", Static).update(
            Text(ecc.cmdline(a) if a else "(unbalanced quotes)", style=SOFT))

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        a = self.argv()
        if a:
            self.dismiss(a)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)



class OutputModal(ModalScreen):
    """Command output; JSON shown as a Tree, `t` toggles raw text."""

    BINDINGS = [("escape", "close", "Close"), ("t", "toggle", "Tree/raw")]

    def __init__(self, title: str, cl: str, rc: int, data: Any, raw: str):
        super().__init__()
        self._title, self.cl, self.rc, self.data, self.raw = title, cl, rc, data, raw
        self.raw_mode = data is None

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog output"):
            yield Label(self._title, classes="dlg-title")
            t = Text()
            t.append(self.cl, style=SOFT)
            t.append(f"   exit {self.rc}", style="bold #8BC34A" if self.rc == 0 else "bold #EF5350")
            yield Static(t, classes="cmd-preview")
            yield JsonTree("json", id="otree")
            yield RichLog(id="oraw", wrap=False, markup=False, highlight=False)
            with Horizontal(classes="buttons"):
                yield Button("Close", variant="primary", id="close")
                yield Button("Tree / raw  (t)", id="tog")

    def on_mount(self) -> None:
        if self.data is not None:
            self.query_one("#otree", JsonTree).show(self.data, "result")
        log = self.query_one("#oraw", RichLog)
        for ln in self.raw.splitlines():
            log.write(colorize_log_line(ln))
        self._apply()

    def _apply(self) -> None:
        self.query_one("#otree").display = not self.raw_mode
        self.query_one("#oraw").display = self.raw_mode

    def action_toggle(self) -> None:
        if self.data is not None:
            self.raw_mode = not self.raw_mode
            self._apply()

    @on(Button.Pressed, "#tog")
    def _tog(self) -> None:
        self.action_toggle()

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        self.dismiss(None)



# ------------------------------------------------------------- flow modals --

def classify_failure(text: str) -> tuple[str, str, str]:
    """(code, headline, advice) from the tail of an ecc run."""
    t = text
    if "workspace_running" in t or "workspace_busy" in t:
        return ("workspace_running", "Stale run lock",
                "ecc thinks another run owns this workspace. If no ecc process is alive it is a stale lock: "
                "Reconcile & resume reconciles the workspace lock and restarts.")
    if "env_not_ready" in t:
        return ("env_not_ready", "Environment not ready",
                "A required tool or PDK is missing. Open the palette (ctrl+p) and run `ecc doctor`, then retry.")
    if "flow_range_requires_pair" in t:
        return ("flow_range_requires_pair", "--to needs --from",
                "A bounded range needs both ends: use --from STEP --to STEP, or drop --to.")
    if "unknown_step" in t:
        return ("unknown_step", "Unknown step",
                "ecc does not know that step id in this flow. Pick a station and press space instead, "
                "or look the ids up with `ecc flow show` in the palette (ctrl+p).")
    if "set_requires_fresh_run" in t:
        return ("set_requires_fresh_run", "--set needs a fresh workspace",
                "One-off --set overrides only apply when the run creates the workspace. Use `ecc param set` instead.")
    return ("generic", "The run stopped with an error",
            "Open the log for the full story, fix the cause, then retry from the failing station.")


class RunPopover(ModalScreen):
    """Compact confirm popover for Space on a station."""

    BINDINGS = [("escape", "cancel", "Cancel"), ("enter", "go", "Go"), ("o", "only", "Only this"),
                ("m", "more", "More")]

    def __init__(self, label: str, args_from: list[str], args_only: list[str], prefix: str = "") -> None:
        super().__init__()
        self.label, self.a_from, self.a_only, self.prefix = label, args_from, args_only, prefix
        self.only = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog pop"):
            yield Label(f"▶ Run from {self.label}?", classes="dlg-title", id="pop-title")
            t = Text()
            t.append("⏎ ", style=f"bold {ORANGE}")
            t.append("go · ", style=FG)
            t.append("o ", style=f"bold {ORANGE}")
            t.append("only this · ", style=FG)
            t.append("m ", style=f"bold {ORANGE}")
            t.append("⋯ more · ", style=FG)
            t.append("esc ", style=f"bold {ORANGE}")
            t.append("cancel", style=FG)
            yield Static(t)
            yield Static(self._cmd(), id="pop-cmd", classes="cmd-preview")
            with Horizontal(classes="buttons"):
                yield Button("⏎ Run from here", variant="primary", id="go")
                yield Button("o Only this", id="only")
                yield Button("⋯ more", id="more")

    def _cmd(self) -> Text:
        a = self.a_only if self.only else self.a_from
        return Text((self.prefix + " " if self.prefix else "") + ecc.cmdline(a), style=DIM)

    def action_go(self) -> None:
        self.dismiss(("run", self.a_from))

    def action_only(self) -> None:
        self.dismiss(("run", self.a_only))

    def action_more(self) -> None:
        self.dismiss(("more", None))

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#go")
    def _b1(self) -> None:
        self.action_go()

    @on(Button.Pressed, "#only")
    def _b2(self) -> None:
        self.action_only()

    @on(Button.Pressed, "#more")
    def _b3(self) -> None:
        self.action_more()


class NewChipWizard(ModalScreen):
    """3-step wizard: name -> RTL files -> clock & frequency."""

    BINDINGS = [("escape", "cancel", "Cancel")]
    STEPS = ["name", "RTL file(s)", "clock & frequency"]

    def __init__(self, root: str) -> None:
        super().__init__()
        self.root = root
        self.step = 0

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wiz"):
            yield Label("✚ New chip", classes="dlg-title")
            yield Static("", id="wiz-steps")
            with Vertical(id="w0"):
                yield Label("project name")
                yield Input("", id="name", placeholder="my_design")
                yield Label("parent directory")
                yield Input(self.root, id="parent")
            with Vertical(id="w1"):
                yield Label("RTL file path(s), comma separated")
                yield Input("", id="rtl", placeholder="/path/a.v, /path/b.v")
                yield Label("design top (optional)")
                yield Input("", id="top")
            with Vertical(id="w2"):
                yield Label("clock port")
                yield Input("", id="clock", placeholder="clk")
                yield Label("frequency (MHz)")
                yield Input("", id="freq", placeholder="100")
            yield Static("", id="preview", classes="cmd-preview")
            with Horizontal(classes="buttons"):
                yield Button("◀ Back", id="back")
                yield Button("Next ▶", variant="primary", id="next")
                yield Button("✓ Create", variant="primary", id="ok")

    def on_mount(self) -> None:
        self._sync()

    def values(self) -> dict:
        g = lambda i: self.query_one(f"#{i}", Input).value.strip()
        return {"name": g("name"), "parent": g("parent"), "top": g("top"),
                "rtl": [x.strip() for x in g("rtl").split(",") if x.strip()],
                "clock": g("clock"), "freq": g("freq")}

    def _preview_text(self) -> str:
        v = self.values()
        lines = [f"(cd {shlex.quote(v['parent'])}) ecc init {shlex.quote(v['name'] or 'NAME')}"]
        if v["rtl"]:
            vflags = " ".join(f"--verilog {shlex.quote(f)}" for f in v["rtl"])
            lines.append(f"ecc rtl import {vflags} --project <P>")
        if v["top"]:
            lines.append(f"ecc project set design.top {shlex.quote(v['top'])} --project <P>")
        if v["clock"] or v["freq"]:
            clk = v["clock"] or "clk"
            freq = v["freq"] or "100"
            spec = f"{clk} MAX:{freq} WCL:{freq} MIN:{freq} ML:{freq} TYP:{freq}"
            lines.append(f"ecc param set sdc.clock_list {shlex.quote(spec)} --project <P>")
        return "\n".join(lines)

    def _sync(self) -> None:
        t = Text()
        for i, s in enumerate(self.STEPS):
            col = ORANGE if i == self.step else (OK if i < self.step else DIM)
            t.append(f"{'●' if i <= self.step else '○'} {i + 1} {s}", style=f"bold {col}" if i == self.step else col)
            if i < 2:
                t.append("  ──  ", style=DIM)
        self.query_one("#wiz-steps", Static).update(t)
        for i in range(3):
            self.query_one(f"#w{i}").display = i == self.step
        self.query_one("#back").display = self.step > 0
        self.query_one("#next").display = self.step < 2
        self.query_one("#ok").display = self.step == 2
        self.query_one("#preview", Static).update(Text(self._preview_text(), style=DIM))
        self.query_one(f"#{['name', 'rtl', 'clock'][self.step]}").focus()

    @on(Input.Changed)
    def _chg(self) -> None:
        self.query_one("#preview", Static).update(Text(self._preview_text(), style=DIM))

    @on(Input.Submitted)
    def _sub(self) -> None:
        if self.step < 2:
            self._next()
        else:
            self._ok()

    @on(Button.Pressed, "#next")
    def _next(self) -> None:
        if self.step == 0 and not self.values()["name"]:
            self.app.notify("give your chip a name first", severity="warning")
            return
        self.step = min(2, self.step + 1)
        self._sync()

    @on(Button.Pressed, "#back")
    def _back(self) -> None:
        self.step = max(0, self.step - 1)
        self._sync()

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        v = self.values()
        if not v["name"]:
            self.app.notify("project name required", severity="warning")
            return
        self.dismiss(v)

    def action_cancel(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Close"), ("question_mark", "close", "Close"), ("enter", "close", "Close")]
    KEYS = [
        ("←↑→↓ / mouse", "move"), ("⏎", "open a chip / pick a station"), ("space", "run from the selected station"),
        ("o", "run only this station"), ("l", "full log"), ("t", "timing corner matrix"),
        ("← →", "cycle die maps (density, RUDY, overflow, margin)"), ("w", "next workspace"),
        ("s  /", "sort / search chips (home)"), ("n", "new chip wizard"), ("ctrl+p", "command palette (every ecc command)"),
        ("x", "stop the active run"), ("esc", "back"), ("?", "this help"), ("q", "quit"),
    ]

    def compose(self) -> ComposeResult:
        t = Table.grid(padding=(0, 3))
        t.add_column(style=f"bold {ORANGE}", min_width=14)
        t.add_column(style=FG)
        for k, d in self.KEYS:
            t.add_row(k, d)
        with Vertical(classes="dialog"):
            yield Label("◆ ecctui · keys", classes="dlg-title")
            yield Static(t)
            yield Static(Text("\nEvery action shells out to the ecc CLI; commands are previewed before they run.", style=DIM))
            with Horizontal(classes="buttons"):
                yield Button("Close", variant="primary", id="close")
                yield Button("Replay tour", id="tour")

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#tour")
    def _tour(self) -> None:
        self.dismiss("tour")


class OnboardingScreen(ModalScreen):
    BINDINGS = [("escape", "done", "Done"), ("enter", "done", "Done"), ("space", "done", "Done")]

    def compose(self) -> ComposeResult:
        with Vertical(id="tour-box"):
            yield Label("Welcome to ECOS Mission Control", classes="dlg-title", id="tour-title")
            with Horizontal(id="tour-cards"):
                with Vertical(classes="tour-card"):
                    yield Static(self._flow_art(), classes="tour-art")
                    yield Label("1 · This is your flow", classes="tour-h")
                    yield Static("21 steps drawn as a metro line, in four colored phases. "
                                 "↑/↓ moves between stations.", classes="tour-p")
                with Vertical(classes="tour-card"):
                    yield Static(self._chip_art(), classes="tour-art")
                    yield Label("2 · This is your chip", classes="tour-h")
                    yield Static("A live die map from the real placement grids: density, routing demand, "
                                 "congestion. ← → switches map.", classes="tour-p")
                with Vertical(classes="tour-card"):
                    yield Static(self._run_art(), classes="tour-art")
                    yield Label("3 · Press space to run", classes="tour-h")
                    yield Static("Space re-runs from the selected station. "
                                 "ctrl+p holds every other command.", classes="tour-p")
            with Horizontal(classes="buttons"):
                yield Button("Let's go  ⏎", variant="primary", id="go")

    @staticmethod
    def _flow_art() -> Text:
        t = Text(no_wrap=True)
        rows = [("●", OK, "synthesis", "#FFB74D"), ("●", OK, "floorplan", "#FFB74D"), ("◉", ORANGE, "place", ORANGE),
                ("○", DIM, "cts", ORANGE), ("○", DIM, "route", "#D84315")]
        for i, (g, c, n, lc) in enumerate(rows):
            if i:
                t.append("\n")
            t.append("┃ ", style=f"bold {lc}")
            t.append(g, style=c)
            t.append("  " + n, style=FG if g != "○" else DIM)
        return t

    @staticmethod
    def _chip_art() -> Text:
        from .widgets import px_style, pixel_color, HEAT_LUT
        import math as m
        t = Text(no_wrap=True)
        for r in range(5):
            if r:
                t.append("\n")
            for x in range(18):
                def v(yy):
                    return max(0.0, min(1.0, 0.25 + 0.55 * m.exp(-(((x - 9) / 6) ** 2 + ((yy - 5) / 3) ** 2))
                                        + 0.2 * m.sin(x * 0.9 + yy)))
                t.append("▀", style=px_style(pixel_color(v(r * 2), HEAT_LUT), pixel_color(v(r * 2 + 1), HEAT_LUT)))
        return t

    @staticmethod
    def _run_art() -> Text:
        t = Text(no_wrap=True)
        t.append(" ▶ place · 4/21 · ETA 12m \n", style=f"bold {BG_} on {ORANGE}")
        t.append("\n")
        t.append("  space ", style=f"bold {BG_} on {ORANGE}")
        t.append("  run from here\n\n", style=FG)
        t.append("  ✦ ✧ • ◆  confetti", style=SOFT)
        return t

    def action_done(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#go")
    def _go(self) -> None:
        self.action_done()


BG_ = "#121011"


class FailureScreen(ModalScreen):
    """"What happened" card shown when a run fails."""

    BINDINGS = [("escape", "close", "Close"), ("r", "retry", "Retry"), ("l", "log", "Log")]

    def __init__(self, station: str, lines: list[str], code: str, head: str, advice: str, rc: int) -> None:
        super().__init__()
        self.station, self.lines, self.code, self.head, self.advice, self.rc = station, lines, code, head, advice, rc

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog fail"):
            yield Label(f"✗ What happened · {self.station}", classes="dlg-title fail-title")
            t = Text()
            t.append(self.head, style=f"bold {BAD}")
            t.append(f"   exit {self.rc} · {self.code}", style=DIM)
            yield Static(t)
            log = Text()
            for i, ln in enumerate((self.lines or ["(no output captured)"])[-6:]):
                log.append(("\n" if i else "") + ln[:260],
                           style=f"{BAD}" if any(k in ln for k in ("rror", "FAIL", "Traceback", "error=")) else DIM)
            yield Static(log, classes="fail-log")
            yield Static(Text(self.advice, style=FG), classes="dlg-body")
            with Horizontal(classes="buttons"):
                yield Button("⟲ Reconcile & resume", variant="primary", id="reconcile",
                             disabled=self.code != "workspace_running")
                yield Button("Open log  l", id="log")
                yield Button("Retry  r", id="retry")
                yield Button("Close", id="close")

    @on(Button.Pressed, "#reconcile")
    def _a(self) -> None:
        self.dismiss("reconcile")

    @on(Button.Pressed, "#log")
    def action_log(self) -> None:
        self.dismiss("log")

    @on(Button.Pressed, "#retry")
    def action_retry(self) -> None:
        self.dismiss("retry")

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        self.dismiss(None)


class LogScreen(ModalScreen):
    """Full log of a station, or the live output of the active run."""

    BINDINGS = [("escape", "close", "Close"), ("l", "close", "Close")]

    def __init__(self, title: str, lines: list[str], live: bool = False) -> None:
        super().__init__()
        self._title, self._lines, self.live = title, lines, live

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog logdlg"):
            yield Label(f"▤ {self._title}" + ("   ● live" if self.live else ""), classes="dlg-title")
            yield RichLog(id="biglog", wrap=False, markup=False, highlight=False, max_lines=5000)
            with Horizontal(classes="buttons"):
                yield Button("Close  esc", variant="primary", id="close")

    def on_mount(self) -> None:
        log = self.query_one("#biglog", RichLog)
        for ln in self._lines[-2000:]:
            log.write(colorize_log_line(ln))

    def on_unmount(self) -> None:
        if getattr(self.app, "log_screen", None) is self:
            self.app.log_screen = None

    def add_line(self, ln: str) -> None:
        if not self.is_mounted:
            self._lines.append(ln)
            return
        try:
            self.query_one("#biglog", RichLog).write(colorize_log_line(ln))
        except Exception:
            pass

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        if getattr(self.app, "log_screen", None) is self:
            self.app.log_screen = None
        self.dismiss(None)
