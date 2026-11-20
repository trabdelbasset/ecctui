"""ecctui application: screens, run manager, command palette."""
from __future__ import annotations

import os
import re
import shlex
import time
from functools import partial
from pathlib import Path
from typing import Any

from rich.text import Text
from textual import work
from textual.app import App
from textual.command import DiscoveryHit, Hit, Hits, Provider

from . import data as D
from . import ecc
from .modals import (ArgsModal, ConfirmModal, FailureScreen, HelpScreen, LogScreen, NewChipWizard,
                     OnboardingScreen, OutputModal, classify_failure)
from .screens import ChipScreen, HomeScreen, TimingScreen, run_bg
from .theme import ECOS_THEME

ECC_COMMANDS = [
    "version", "doc", "check", "run", "status", "log", "config", "doctor", "check-update",
    "rtl import",
    *[f"param {s}" for s in "list show set unset diff apply".split()],
    *[f"flow {s}" for s in "list show validate".split()],
    *[f"macro {s}" for s in "show set remove import".split()],
    *[f"pdk {s}" for s in "show verify set-root unset".split()],
    *[f"project {s}" for s in "show set unset add remove doctor reconcile baseline apply".split()],
    *[f"process {s}" for s in "inspect cancel reconcile".split()],
    *[f"workspace {s}" for s in "create import refresh derive archive delete reset-flow reconcile-deletes".split()],
    *[f"signoff {s}" for s in "inspect check export".split()],
    *[f"report {s}" for s in "project snapshot qor checklist summary step".split()],
    "compare run",
    *[f"reproducible {s}" for s in "export inspect verify report".split()],
    *[f"sweep {s}" for s in "create show list run compare verify".split()],
    *[f"explore {s}" for s in "rank export promote".split()],
    "diagnose run", "gdsview", "chipview",
]
_NO_ARGS = {"version", "doc", "check-update", "flow list", "flow show", "flow validate", "gdsview", "chipview"}
_WS_FIRST = {"run", "status", "log", "config", "param", "process", "signoff", "diagnose", "compare"}
_WS_REPORT = {"qor", "checklist", "summary", "step"}
_WS_WORKSPACE = {"refresh", "archive", "delete", "reset-flow"}


def prefill_for(cmd: str, project: str | None, ws: str | None) -> str:
    if cmd in _NO_ARGS or not project:
        return ""
    parts = cmd.split()
    wants_ws = (parts[0] in _WS_FIRST or (parts[0] == "report" and len(parts) > 1 and parts[1] in _WS_REPORT)
                or (parts[0] == "workspace" and len(parts) > 1 and parts[1] in _WS_WORKSPACE))
    s = f"--project {shlex.quote(project)}"
    if wants_ws and ws:
        s += f" --workspace {shlex.quote(ws)}"
    return s


def config_dir() -> Path:
    return Path(os.environ.get("ECCTUI_CONFIG_DIR") or (Path.home() / ".config" / "ecctui"))


class EccCommands(Provider):
    """Command palette: app actions plus every ecc command."""

    def _entries(self):
        app: EccTui = self.app  # type: ignore[assignment]
        yield "Timing corner matrix", app.action_timing, "open the STA corner matrix (t)"
        yield "Reconcile stale run", app.action_reconcile, "ecc process reconcile with run_id from project.json"
        yield "Resume run", app.action_resume, "ecc run --resume"
        yield "Stop run", app.action_cancel_run, "ecc process cancel"
        yield "New chip…", app.action_new_chip, "wizard: name, RTL, clock"
        yield "Show tour", app.action_tour, "replay the onboarding"
        yield "Help", app.action_help, "keys"
        for c in ECC_COMMANDS:
            yield f"ecc {c}", partial(app.open_args, c), f"opens an argument dialog, then runs ecc {c} --json"

    async def discover(self) -> Hits:
        for text, cb, help_ in self._entries():
            yield DiscoveryHit(text, cb, help=help_)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for text, cb, help_ in self._entries():
            score = matcher.match(text)
            if score > 0:
                yield Hit(score, matcher.highlight(text), cb, help=help_)


class EccTui(App):
    CSS_PATH = "styles.tcss"
    TITLE = "ECOS Mission Control"
    COMMANDS = {EccCommands}
    ENABLE_COMMAND_PALETTE = True
    BINDINGS = [("question_mark", "help", "Help"), ("n", "new_chip", "New chip"), ("q", "quit_app", "Quit")]

    def __init__(self, root: Path, ecc_bin: str | None = None):
        super().__init__()
        self.root_dir = Path(root).expanduser().resolve()
        ecc.set_ecc(ecc_bin)
        self.register_theme(ECOS_THEME)
        self.theme = "ecos"
        self.run_handle: ecc.RunHandle | None = None
        self.run_state: dict | None = None
        self.run_lines: list[str] = []
        self.dirty: set[str] = set()
        self.current: tuple[str, str] | None = None
        self.log_screen = None
        self._polling = False
        self._poll_timer = None

    @property
    def run_active(self) -> bool:
        return bool(self.run_handle and self.run_handle.active)

    def on_mount(self) -> None:
        self.push_screen(HomeScreen())
        self.set_interval(0.5, self._tick)

    def _tick(self) -> None:
        scr = self.screen_stack[-1] if self.screen_stack else None
        for s in self.screen_stack:
            if hasattr(s, "on_app_tick"):
                s.on_app_tick()
        del scr

    # ---------------------------------------------------------------- screens --
    def open_chip(self, project: str, ws: str) -> None:
        self.push_screen(ChipScreen(project, ws))

    def home(self) -> HomeScreen | None:
        for s in self.screen_stack:
            if isinstance(s, HomeScreen):
                return s
        return None

    def chip_screen(self) -> ChipScreen | None:
        for s in reversed(self.screen_stack):
            if isinstance(s, ChipScreen):
                return s
        return None

    def action_timing(self) -> None:
        cs = self.chip_screen()
        if cs:
            cs.action_timing()
        else:
            self.notify("open a chip first", severity="warning")

    def action_help(self) -> None:
        if isinstance(self.screen, HelpScreen):
            return
        self.push_screen(HelpScreen(), lambda r: r == "tour" and self.action_tour())

    def action_tour(self) -> None:
        self.push_screen(OnboardingScreen(), self._tour_done)

    def maybe_onboard(self) -> None:
        flag = config_dir() / "seen_onboarding"
        if not flag.exists() and not isinstance(self.screen, OnboardingScreen):
            self.action_tour()

    def _tour_done(self, _r) -> None:
        try:
            d = config_dir()
            d.mkdir(parents=True, exist_ok=True)
            (d / "seen_onboarding").write_text("1\n")
        except OSError:
            pass
        hs = self.home()
        if hs:
            hs.focus_first()

    def action_quit_app(self) -> None:
        if self.run_active:
            self.push_screen(ConfirmModal("Quit ecctui?", "A run is active. Quitting leaves the ecc process running "
                                          "in the background.", "Quit anyway", danger=True),
                             lambda ok: ok[0] and self.exit())
        else:
            self.exit()

    # ----------------------------------------------------------------- palette --
    def open_args(self, cmd: str) -> None:
        p, w = self.current or (None, None)
        self.push_screen(ArgsModal(cmd, prefill_for(cmd, p, w)), lambda argv: argv and self._exec(cmd, argv))

    def _exec(self, cmd: str, argv: list[str]) -> None:
        def go():
            return ecc.run_json(argv)

        def done(res):
            if isinstance(res, Exception):
                self.notify(str(res), severity="error")
                return
            rc, data, raw, cl, secs = res
            self.push_screen(OutputModal(f"ecc {cmd}", cl, rc, data, raw))

        run_bg(self, go, done)

    # -------------------------------------------------------------- run manager --
    def _scope_tokens(self, args: list[str], stations: list[dict]) -> list[str]:
        toks = [s["token"] for s in stations] or [t[0] for t in D.STATIONS]

        def idx(name: str) -> int | None:
            n = D.norm(name)
            for i, t in enumerate(toks):
                if n in (D.norm(t), D.norm(D.flow_id_for(t)), D.norm(D.label_for(t))):
                    return i
                if t == "placement" and n == "place" or t == "routing" and n == "route":
                    return i
            return None

        if "--only" in args:
            i = idx(args[args.index("--only") + 1])
            return toks[i:i + 1] if i is not None else toks
        if "--from" in args:
            i = idx(args[args.index("--from") + 1]) or 0
            j = idx(args[args.index("--to") + 1]) if "--to" in args else None
            return toks[i:(j + 1 if j is not None else None)]
        if "--resume" in args:
            rest = [s["token"] for s in stations if s["status"] != "success"]
            return rest or toks
        return toks

    def start_run(self, args: list[str] | None, project: str, ws: str, stations: list[dict]) -> None:
        if not args:
            return
        if self.run_active:
            self.notify("a run is already active (x to stop)", severity="warning")
            return
        scope = self._scope_tokens(args, stations)
        prior = {s["token"]: s["secs"] for s in stations if s.get("secs")}
        h = ecc.RunHandle(args, project)
        self.run_handle = h
        self.run_lines = [f"$ {h.display}"]
        self.run_state = {
            "project": project, "ws": ws, "name": Path(project).name, "scope": scope, "total": len(scope),
            "done": 0, "step": scope[0] if scope else None, "step_token": scope[0] if scope else None,
            "started": time.time(), "eta": None, "prior": prior, "args": args, "armed": set(),
            "step_started": time.time(), "frac": 0.0, "stations": stations, "finished": False,
            "baseline": dict(prior),
        }
        self.run_state["step"] = D.label_for(scope[0]) if scope else None
        self.notify(f"run started · {h.display}", title="▶ ecc run", timeout=4)
        self._poll_timer = self.set_interval(2.0, self._poll_run)
        self._stream(h)
        self.call_later(self._poll_run)

    @work(thread=True, exit_on_error=False, group="run")
    def _stream(self, handle: ecc.RunHandle) -> None:
        def on_line(ln: str) -> None:
            self.run_lines.append(ln)
            ls = self.log_screen
            if ls is not None and ls in self.screen_stack:
                self.call_from_thread(ls.add_line, ln)
        handle.stream(on_line)
        self.call_from_thread(self._run_done, handle)

    def _poll_run(self) -> None:
        st = self.run_state
        if not (self.run_active and st) or self._polling:
            return
        self._polling = True
        project, ws = st["project"], st["ws"]

        def job():
            return ecc.run_json(["status", "--project", project, "--workspace", ws], echo=False)

        def done(res):
            self._polling = False
            if isinstance(res, Exception) or not self.run_state:
                return
            rc, data, *_ = res
            head, steps = ecc.records(data)
            steps = [s for s in steps if s.get("step")]
            if not steps:
                return
            stations = D.station_defs(steps)
            state = self.run_state
            scope = [t for t in state["scope"] if any(s["token"] == t for s in stations)] or state["scope"]
            by = {s["token"]: s for s in stations}
            now = time.time()
            for t in scope:
                s = by.get(t)
                if s and (s["status"] != "success" or s["secs"] != state["baseline"].get(t, -1.0)):
                    state["armed"].add(t)
            done_toks = [t for t in scope if t in state["armed"] and by.get(t, {}).get("status") == "success"]
            running = next((t for t in scope if by.get(t, {}).get("status") == "running"), None)
            state["running"] = running
            if running:
                state["seen_running"] = True
            if running and running != state["step_token"]:
                state["step_started"] = now
            if running:
                state["step_token"] = running
                state["step"] = D.label_for(running)
            elif len(done_toks) < len(scope):
                nxt = next((t for t in scope if t not in done_toks), None)
                if nxt:
                    state["step_token"], state["step"] = nxt, D.label_for(nxt)
            state["done"], state["total"] = len(done_toks), len(scope)
            # ETA from the previous run of this workspace, else from the average of finished steps
            durs = [by[t]["secs"] for t in done_toks if by[t]["secs"] > 0]
            avg = sum(durs) / len(durs) if durs else None
            remaining = [t for t in scope if t not in done_toks]
            eta, known = 0.0, True
            for t in remaining:
                p = state["prior"].get(t) or avg
                if p is None:
                    known = False
                    break
                if t == running:
                    p = max(0.0, p - (now - state["step_started"]))
                eta += p
            state["eta"] = eta if known and (state["prior"] or avg is not None) else None
            if running and state["prior"].get(running):
                state["frac"] = min(0.95, (now - state["step_started"]) / state["prior"][running])
            else:
                state["frac"] = 0.0
            state["stations"] = stations
            cs = self.chip_screen()
            if cs and str(cs.project) == project and cs.ws == ws:
                cs.apply_status(stations)

        run_bg(self, job, done, group="poll")
        self.set_timer(10, lambda: setattr(self, "_polling", False))

    def _run_done(self, h: ecc.RunHandle) -> None:
        if self._poll_timer:
            self._poll_timer.stop()
        st = self.run_state or {}
        st["finished"] = True
        rc = h.rc if h.rc is not None else 1
        ok = rc == 0
        self.run_lines.append(f"[ecctui] exit code {rc} after {ecc.fmt_secs(h.seconds)}")
        project = st.get("project")
        self.dirty.add(project)
        target = self.chip_screen() if (self.chip_screen() and str(self.chip_screen().project) == project) else self.home()
        if ok:
            if target:
                target.run_finished(True, st)
            self.notify(f"✓ {st.get('name')} finished · {ecc.fmt_secs(h.seconds)}", title="ecc run", timeout=8)
            self._success_toast(st, h.seconds)
        else:
            if target:
                target.run_finished(False, st)
            text = "\n".join(h.output[-200:])
            code, head, advice = classify_failure(text)
            errs = [ln for ln in h.output[-60:] if any(k in ln for k in ("rror", "FAIL", "Traceback", "error=", "Exception"))]
            lines = (errs or [ln for ln in h.output[-8:] if ln.strip()])[-6:]
            self.push_screen(FailureScreen(D.label_for(st["step_token"]) if st.get("seen_running") and st.get("step_token") else "run",
                                           lines, code, head, advice, rc), self._failure_action)

    def _success_toast(self, st: dict, secs: float) -> None:
        proj, ws = st["project"], st["ws"]

        def job():
            return D.load_chip(Path(proj), ws)

        def done(d):
            if isinstance(d, Exception):
                return
            dur = ecc.fmt_secs(secs)
            if d.get("score") is not None:
                self.notify(f"✓ {st['name']} finished · {d['score']:.1f} {d['band']} · {dur}", title="ECC", timeout=10)
            else:
                n = sum(1 for s in d["stations"] if s["status"] == "success")
                self.notify(f"✓ {st['name']} finished · {n}/{len(d['stations'])} steps · {dur}", title="ECC", timeout=10)

        run_bg(self, job, done, group="toast")

    def _failure_action(self, act: str | None) -> None:
        st = self.run_state or {}
        if act == "reconcile":
            self.action_reconcile(resume=True)
        elif act == "log":
            self.log_screen = LogScreen("run output", list(self.run_lines))
            self.push_screen(self.log_screen)
        elif act == "retry" and st.get("args"):
            self.start_run(st["args"], st["project"], st["ws"], st.get("stations", []))

    def action_resume(self) -> None:
        cur = self.current
        cs = self.chip_screen()
        if cur and cs:
            self.start_run(["run", "--project", cur[0], "--workspace", cur[1], "--resume", "--plain"],
                           cur[0], cur[1], cs.data["stations"] if cs.data else [])

    def action_cancel_run(self) -> None:
        st = self.run_state
        cur = (st["project"], st["ws"]) if (st and self.run_active) else self.current
        if not cur:
            self.notify("no chip selected", severity="warning")
            return
        own_live = bool(self.run_active and self.run_handle and st and (st["project"], st["ws"]) == cur)
        rid = ecc.runtime_run_id(cur[0], cur[1])
        if not rid and not own_live:
            self.notify("no active run to stop", severity="warning")
            return
        if rid:
            a = ["process", "cancel", "--project", cur[0], "--workspace", cur[1], "--run-id", rid]
            self.push_screen(
                ConfirmModal(
                    "Stop run",
                    f"Cancel the active run {rid} of {cur[1]}?",
                    "Stop run",
                    check="--force (kill)",
                    cmd=ecc.cmdline(a) + "  [--force]",
                    danger=True,
                ),
                lambda ok: ok and ok[0] and self._do_cancel(a, ok[1], own_live),
            )
        else:
            self.push_screen(
                ConfirmModal(
                    "Stop run",
                    f"Stop the active run of {cur[1]}?",
                    "Stop run",
                    check="--force (kill)",
                    danger=True,
                ),
                lambda ok: ok and ok[0] and self._stop_local(ok[1]),
            )

    def _stop_local(self, force: bool) -> None:
        if self.run_handle and self.run_handle.stop(force=force):
            self.notify("run stopped", severity="information")
        else:
            self.notify("no active process to stop", severity="warning")

    def _do_cancel(self, a: list[str], force: bool, own_live: bool = False) -> None:
        args = a + (["--force"] if force else [])

        def done(res):
            if isinstance(res, Exception):
                if own_live:
                    self._stop_local(force)
                return
            rc, data, raw, cl, secs = res
            if rc == 0:
                self.notify("run cancelled", severity="information")
                return
            if own_live and self.run_handle and self.run_handle.active:
                self._stop_local(force)
                return
            self.notify(f"cancel failed (exit {rc})", severity="error")
            self.push_screen(OutputModal("process cancel", cl, rc, data, raw))

        run_bg(self, lambda: ecc.run_json(args), done)

    def action_reconcile(self, resume: bool = False) -> None:
        st = self.run_state
        cur = (st["project"], st["ws"]) if (resume and st) else (
            self.current or ((st["project"], st["ws"]) if st else None)
        )
        if not cur:
            self.notify("open a chip first", severity="warning")
            return
        rid = ecc.runtime_run_id(cur[0], cur[1])
        a = ["process", "reconcile", "--project", cur[0], "--workspace", cur[1]]
        if rid:
            a += ["--run-id", rid]

        def done(res):
            if isinstance(res, Exception):
                return
            rc, data, raw, cl, secs = res
            if rc != 0:
                self.push_screen(OutputModal("✗ reconcile failed", cl, rc, data, raw))
                return
            self.notify(f"reconciled {rid or cur[1]}", title="⟲ reconcile")
            if resume:
                cs = self.chip_screen()
                stations = (
                    cs.data.get("stations", [])
                    if cs and cs.data and (str(cs.project), cs.ws) == cur
                    else (st or {}).get("stations", [])
                )
                self.start_run(
                    ["run", "--project", cur[0], "--workspace", cur[1], "--resume", "--plain"],
                    cur[0], cur[1], stations,
                )
            else:
                cs = self.chip_screen()
                if cs and (str(cs.project), cs.ws) == cur:
                    cs.load()
                hs = self.home()
                if hs:
                    hs.load_all()

        run_bg(self, lambda: ecc.run_json(a), done)

    # ---------------------------------------------------------------- new chip --
    def action_new_chip(self) -> None:
        if isinstance(self.screen, NewChipWizard):
            return
        self.push_screen(NewChipWizard(str(self.root_dir)), self._do_init)

    def _do_init(self, v: dict | None) -> None:
        if not v:
            return

        def work_():
            log: list[str] = []
            parent = Path(v["parent"]).expanduser()
            rc_all = 0
            parent.mkdir(parents=True, exist_ok=True)

            def step(args, cwd=None):
                nonlocal rc_all
                rc, data, raw, cl, secs = ecc.run_json(args, cwd=cwd)
                log.append(f"$ {cl}   -> exit {rc} ({secs:.1f}s)\n{raw.strip()[:600]}\n")
                if rc != 0:
                    rc_all = rc
                return rc

            if step(["init", v["name"]], cwd=str(parent)) != 0:
                return rc_all, log, None
            proj = (parent / v["name"]).resolve()
            if v["rtl"]:
                rtl_args = ["rtl", "import"] + [x for f in v["rtl"] for x in ("--verilog", f)] + ["--project", str(proj)]
                step(rtl_args)
            if v["top"]:
                step(["project", "set", "design.top", v["top"], "--project", str(proj)])
            if v["clock"] or v["freq"]:
                clk = v["clock"] or "clk"
                freq = v["freq"] or "100"
                spec = f"{clk} MAX:{freq} WCL:{freq} MIN:{freq} ML:{freq} TYP:{freq}"
                rc_clk, _, raw_clk, cl_clk, secs_clk = ecc.run_json(
                    ["param", "set", "sdc.clock_list", spec, "--project", str(proj)]
                )
                log.append(f"$ {cl_clk}   -> exit {rc_clk} ({secs_clk:.1f}s)\n{raw_clk.strip()[:600]}\n")
                if rc_clk != 0:
                    for key, val in (("design.clock_port", clk), ("design.frequency_mhz", freq)):
                        step(["project", "set", key, val, "--project", str(proj)])
            return rc_all, log, proj

        def done(out):
            if isinstance(out, Exception):
                self.notify(str(out), severity="error")
                return
            rc, log, proj = out
            self.push_screen(OutputModal("✚ New chip", f"ecc init {v['name']}", rc, None, "\n".join(log)))
            hs = self.home()
            if hs:
                hs.load_all()

        run_bg(self, work_, done)
