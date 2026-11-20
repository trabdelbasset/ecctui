"""ecctui screens: Home launcher, Chip (metro line + die map + health), Timing matrix."""
from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any, Callable

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.geometry import Spacing
from textual.screen import Screen
from textual.widgets import Button, Input, Static

from . import data as D
from . import ecc
from .modals import FailureScreen, LogScreen, RunModal, RunPopover, colorize_log_line
from .theme import (BAD, BG, DIM, FG, LINE, OK, ORANGE, PHASE_COLORS, SOFT, WARN, band_color, gradient_at,
                    lerp_color)
from .widgets import (ChipCard, Confetti, DieMap, HealthPanel, HintBar, MetroLine, TIPS, TopBar,
                      logo_text, thumb_text)


def run_bg(node, fn: Callable[[], Any], cb: Callable[[Any], None], group: str = "bg") -> None:
    """Run fn in a worker thread and deliver the result to cb on the UI thread."""
    def job() -> None:
        try:
            res = fn()
        except Exception as e:      # never crash the UI
            res = e
        try:
            node.app.call_from_thread(cb, res)
        except Exception:
            pass
    node.run_worker(job, thread=True, exit_on_error=False, group=group)


# =========================================================================== home

class HomeScreen(Screen):
    BINDINGS = [
        ("n", "new", "New chip"), ("s", "sort", "Sort"),
        ("slash", "search", "Search"), ("escape", "clear_search", "Clear"),
        ("left", "move(-1,0)", "left"), ("right", "move(1,0)", "right"),
        ("up", "move(0,-1)", "up"), ("down", "move(0,1)", "down"),
    ]
    SORTS = ["score", "name", "runtime", "status"]

    def __init__(self) -> None:
        super().__init__()
        self.cards: dict[str, ChipCard] = {}
        self.sort_mode = "score"
        self.query_text = ""
        self.version: dict = {}
        self.doctor: str | None = None
        self.loaded = 0
        self._focused_once = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="hero"):
            yield Static(logo_text(), id="logo")
            yield Static("", id="hero-info")
        yield Input(placeholder="search chips…", id="search")
        with VerticalScroll(id="cards-scroll"):
            yield Grid(id="cards")
        yield HintBar(id="hints")

    def on_mount(self) -> None:
        hb = self.query_one(HintBar)
        hb.hints = (("⏎", "open"), ("n", "new chip"), ("/", "search"), ("ctrl+p", "commands"), ("?", "help"))
        hb.tip = random.choice(TIPS)
        self.set_interval(10, self._rotate_tip)
        self.query_one("#search").display = False
        self.query_one("#hero").border_title = "ECOS MISSION CONTROL"
        self.load_all()
        self.call_after_refresh(self.focus_first)
        run_bg(self, lambda: (ecc.cached_json(["version"], echo=False), ecc.cached_json(["doctor"], echo=False)),
               self._got_env)
        self.call_after_refresh(self._after_first_layout)

    def _after_first_layout(self) -> None:
        self._relayout()
        self.app.maybe_onboard()

    def _rotate_tip(self) -> None:
        self.query_one(HintBar).tip = random.choice(TIPS)

    def on_screen_resume(self) -> None:
        for p in list(self.app.dirty):
            self.reload_card(p)
        self.app.dirty.clear()

    # ---- loading
    def load_all(self) -> None:
        grid = self.query_one("#cards", Grid)
        grid.remove_children()
        self.cards.clear()
        self.loaded = 0
        self._focused_once = False
        projects = ecc.find_projects(self.app.root_dir)
        for p in projects:
            c = ChipCard(p.name, str(p))
            self.cards[str(p)] = c
        new = ChipCard(new=True, id="card-new")
        grid.mount(*self.cards.values(), new)
        for p in projects:
            self.reload_card(str(p))
        self._update_hero()
        self.call_after_refresh(self._relayout)

    def reload_card(self, proj: str) -> None:
        card = self.cards.get(proj)
        if not card:
            return
        pj = Path(proj)

        def work():
            wsid = D.project_workspace_id(pj)
            if not wsid:
                return None
            d = D.load_chip(pj, wsid)
            grid = None
            for m in d["maps"]:
                if m.key == "density":
                    g = D.read_grid(m.path)
                    grid = D.prepare_grid(g, "density")[0]
                    break
            return d, grid

        def done(res):
            if isinstance(res, Exception) or res is None:
                card.data = {"status": "not_started", "stations": [], "matrix": {}, "total_secs": 0,
                             "score": None, "band": None, "gates": [], "name": card.chip_name}
                card.summary = {"done": 0, "total": 0, "drv": 0, "secs": 0}
                card.refresh()
            else:
                card.set_data(res[0], res[1])
            self.loaded += 1
            self._update_hero()
            self._resort()
            if self.loaded == len(self.cards) and not self._focused_once:
                self._focused_once = True
                self.focus_first()

        run_bg(self, work, done, group=f"card-{proj}")

    def _got_env(self, res) -> None:
        if isinstance(res, Exception):
            return
        v, d = res
        ver = v[1] if isinstance(v[1], dict) else {}
        self.version = ver
        recs = d[1] if isinstance(d[1], list) else []
        if recs and isinstance(recs[0], dict):
            self.doctor = recs[0].get("status")
        self._update_hero()

    def _update_hero(self) -> None:
        w = max(40, (self.query_one("#hero-info").size.width or 70))
        datas = [c.data for c in self.cards.values() if c.data]
        n = len(self.cards)
        # "truth over vanity": ECC's 7 gates are not enough; DRV from STA counts too
        gates_ok = [c for c in self.cards.values()
                    if c.data and c.data.get("gates") and c.data.get("gates_pass") == len(c.data["gates"])]
        drv_bad = sum(1 for c in gates_ok if (getattr(c, "summary", None) or {}).get("drv", 0) > 0)
        clean = len(gates_ok) - drv_bad
        running = sum(1 for d in datas if d.get("status") == "running") + (1 if self.app.run_active else 0) * 0
        compact = self.size.height < 44
        t = Text(no_wrap=True)
        ver = f"ecc v{self.version.get('ecc', '…')}"
        l1 = Text(no_wrap=True)
        if compact:
            l1.append("◆ ECOS Chip Compiler", style=f"bold {ORANGE}")
        else:
            l1.append("ECOS Chip Compiler", style=f"bold {ORANGE}")
        l1.append(" " * max(2, w - 18 - len(ver) - 1 - (2 if compact else 0)))
        l1.append(ver, style=DIM)
        t.append_text(l1)
        t.append("\n")
        l2 = Text(no_wrap=True)
        l2.append("RTL → GDS, open source", style=SOFT)
        doc = "✓ doctor" if self.doctor == "ok" else ("✗ doctor" if self.doctor else "… doctor")
        pdk = next((c.data.get("cfg", {}).get("pdk", {}).get("name") for c in self.cards.values()
                    if c.data and c.data.get("cfg")), "") or ""
        right = f"{doc}  {pdk}".strip()
        l2.append(" " * max(2, w - 22 - len(right) - 1))
        l2.append(f"{doc}", style=OK if self.doctor == "ok" else (BAD if self.doctor else DIM))
        l2.append(f"  {pdk}", style=FG)
        t.append_text(l2)
        t.append("\n" if compact else "\n\n")
        t.append(f"{n} chips", style=f"bold {FG}")
        t.append(" · ", style=DIM)
        t.append(f"{clean} clean", style=OK)
        if drv_bad:
            t.append(" · ", style=DIM)
            t.append(f"{drv_bad} with DRV", style=BAD)
        vac = sum(1 for c in self.cards.values() if (getattr(c, "summary", None) or {}).get("antenna_vacuous"))
        if vac:
            t.append(" · ", style=DIM)
            t.append(f"antenna unverified ({vac})", style=WARN)
        t.append(" · ", style=DIM)
        t.append(f"{running} running", style=ORANGE if running else DIM)
        if self.loaded < n:
            t.append(f"   loading {self.loaded}/{n} ░▒▓", style=DIM)
        self.query_one("#hero-info", Static).update(t)

    def on_resize(self, _e) -> None:
        self.call_after_refresh(self._relayout)

    def _relayout(self) -> None:
        grid = self.query_one("#cards", Grid)
        compact = self.size.height < 44
        hero = self.query_one("#hero")
        want = 5 if compact else 8
        if hero.styles.height is None or int(hero.styles.height.value) != want:
            hero.styles.height = want
            self.call_after_refresh(self._relayout)      # re-measure the card area with the new hero
        self.query_one("#logo").display = not compact
        self.query_one("#hero-info").styles.height = 3 if compact else 6
        self.query_one("#hero-info").styles.padding = (0, 0, 0, 0) if compact else (0, 0, 0, 3)
        W = self.query_one("#cards-scroll").size.width or self.size.width
        cols = max(1, (W - 1) // 26)
        grid.styles.grid_size_columns = cols
        n = len([c for c in self.cards.values() if c.display]) + 1
        rows = max(1, -(-n // cols))
        avail = self.query_one("#cards-scroll").size.height or 20
        h = max(7, min(13, (avail - 1) // rows))
        grid.set_styles(f"grid-rows: {h};")
        for c in list(self.cards.values()) + [self.query_one("#card-new")]:
            c.styles.height = h
        self._update_hero()

    # ---- sorting / filter
    def _ordered(self) -> list[ChipCard]:
        cs = list(self.cards.values())
        m = self.sort_mode
        if m == "name":
            cs.sort(key=lambda c: c.sort_keys["name"])
        elif m == "score":
            cs.sort(key=lambda c: -c.sort_keys["score"])
        elif m == "runtime":
            cs.sort(key=lambda c: -c.sort_keys["runtime"])
        else:
            cs.sort(key=lambda c: (c.sort_keys["status"], c.sort_keys["name"]))
        return cs

    def _resort(self) -> None:
        grid = self.query_one("#cards", Grid)
        prev = None
        q = self.query_text.lower()
        for c in self._ordered():
            c.display = (not q) or q in c.chip_name.lower()
            try:
                if prev is None:
                    grid.move_child(c, before=0)
                else:
                    grid.move_child(c, after=prev)
            except Exception:
                pass
            prev = c
        self.query_one("#cards-scroll").border_subtitle = f"sort: {self.sort_mode}"
        self.query_one("#hero").border_subtitle = f"sort ▸ {self.sort_mode} (s)"

    def action_sort(self) -> None:
        self.sort_mode = self.SORTS[(self.SORTS.index(self.sort_mode) + 1) % len(self.SORTS)]
        self._resort()
        self.notify(f"sorted by {self.sort_mode}", timeout=1.5)

    def action_search(self) -> None:
        s = self.query_one("#search", Input)
        s.display = True
        s.focus()

    @on(Input.Changed, "#search")
    def _search(self, ev: Input.Changed) -> None:
        self.query_text = ev.value
        self._resort()

    @on(Input.Submitted, "#search")
    def _search_done(self) -> None:
        vis = [c for c in self._ordered() if c.display]
        if vis:
            vis[0].focus()

    def action_clear_search(self) -> None:
        s = self.query_one("#search", Input)
        if s.display:
            s.value = ""
            s.display = False
            self.query_text = ""
            self._resort()
            self.focus_first()

    def focus_first(self) -> None:
        try:
            vis = self._visible()
        except Exception:
            vis = [c for c in self._ordered() if c.display]
        if vis:
            vis[0].focus()

    # ---- navigation / actions
    def _visible(self) -> list[ChipCard]:
        out = [c for c in self._ordered() if c.display]
        new = self.query_one("#card-new", ChipCard)
        return out + [new]

    def action_move(self, dx: int, dy: int) -> None:
        vis = self._visible()
        cur = self.focused if isinstance(self.focused, ChipCard) else None
        if cur is None:
            vis[0].focus()
            return
        cols = int(self.query_one("#cards", Grid).styles.grid_size_columns or 1)
        i = vis.index(cur) if cur in vis else 0
        j = i + dx + dy * cols
        if dy and not (0 <= j < len(vis)):
            j = len(vis) - 1 if dy > 0 and i < len(vis) - 1 else i
        j = max(0, min(len(vis) - 1, j))
        vis[j].focus()
        self.query_one("#cards-scroll").scroll_to_widget(vis[j], animate=False)

    def action_new(self) -> None:
        self.app.action_new_chip()

    @on(ChipCard.Opened)
    def _opened(self, ev: ChipCard.Opened) -> None:
        if ev.card.new:
            self.app.action_new_chip()
        elif ev.card.project:
            wsid = (ev.card.data or {}).get("ws") or D.project_workspace_id(Path(ev.card.project)) or "default"
            self.app.open_chip(ev.card.project, wsid)

    def on_app_tick(self) -> None:
        run = self.app.run_state
        for p, c in self.cards.items():
            c.running = bool(run and run.get("project") == p and self.app.run_active)
            if c.data is None or c.running:
                c.tick += 1
                c.refresh()

    def run_finished(self, ok: bool, state: dict) -> None:
        self.reload_card(state["project"])
        if ok:
            self.app.push_screen(Confetti())


# =========================================================================== chip

class ChipScreen(Screen):
    BINDINGS = [
        ("escape", "home", "Home"), ("space", "run_from", "Run from here"), ("o", "run_only", "Run only"),
        ("l", "log", "Log"), ("t", "timing", "Timing"), ("left", "cycle(-1)", "Prev map"),
        ("right", "cycle(1)", "Next map"), ("w", "next_ws", "Next workspace"), ("x", "stop", "Stop run"),
    ]

    def __init__(self, project: str, ws: str) -> None:
        super().__init__()
        self.project, self.ws = Path(project), ws
        self.data: dict | None = None
        self.prev_status: dict[str, str] = {}
        self.sel_token = ""
        self.flow_ids: list[str] = []
        self.follow = True
        self._auto = False

    @property
    def wsdir(self) -> Path:
        return self.project / self.ws

    def compose(self) -> ComposeResult:
        yield TopBar(id="top")
        with Horizontal(id="chip-body"):
            with Vertical(id="flow-panel"):
                with VerticalScroll(id="flow-scroll"):
                    yield MetroLine(id="metro")
                yield Static("", id="flow-foot")
            with Vertical(id="center"):
                yield DieMap(id="die")
                with Vertical(id="drawer"):
                    yield Static("", id="d-head")
                    with Horizontal(id="d-actions"):
                        yield Button("⏵ run from here", id="b-from", compact=True, variant="primary")
                        yield Button("⏵ only", id="b-only", compact=True)
                        yield Button("▤ log", id="b-log", compact=True)
                    yield Static("", id="d-log")
            with VerticalScroll(id="health-panel"):
                yield HealthPanel(id="health")
        yield HintBar(id="hints")

    def on_mount(self) -> None:
        self.query_one("#flow-panel").border_title = "FLOW"
        self.query_one("#drawer").border_title = "STATION"
        self.query_one("#health-panel").border_title = "HEALTH"
        top = self.query_one(TopBar)
        top.crumbs = ("ECOS", self.project.name, self.ws)
        hb = self.query_one(HintBar)
        hb.hints = (("↑↓", "station"), ("space", "run from here"), ("←→", "map"), ("t", "timing"), ("esc", "home"))
        hb.tip = random.choice(TIPS)
        self.set_interval(10, lambda: setattr(hb, "tip", random.choice(TIPS)))
        self.query_one(MetroLine).focus()
        self.query_one(DieMap).show_loading()
        self.set_interval(0.25, self._anim)
        self.app.current = (str(self.project), self.ws)
        self.load()

    def on_screen_resume(self) -> None:
        self.app.current = (str(self.project), self.ws)

    # ---- loading
    def load(self) -> None:
        run_bg(self, lambda: (D.load_chip(self.project, self.ws), ecc.cached_json(["flow", "list"], echo=False)),
               self._loaded, group="chip-load")

    def _loaded(self, res) -> None:
        if isinstance(res, Exception):
            self.notify(f"load failed: {res}", severity="error")
            return
        d, fl = res
        self.data = d
        if getattr(self, "_confetti_after_load", False):
            self._confetti_after_load = False
            self.app.push_screen(Confetti())
        if isinstance(fl[1], list):
            self.flow_ids = sorted({x["flow_id"] for x in fl[1] if isinstance(x, dict) and x.get("flow_id")})
            self.step_ids = [x["step_id"] for x in fl[1] if isinstance(x, dict) and x.get("flow_id") == "rtl2gds"]
        self._apply(d)

    def _apply(self, d: dict) -> None:
        badges = {}
        for key, _label, lvl, val, _detail in d["health"]:
            if key == "antenna" and lvl == "warn":
                ant_toks = [s["token"] for s in d["stations"] if D.canonical_token(s["token"]) == "antenna_check"]
                for tok in ant_toks or ["antenna_check"]:
                    badges[tok] = val
        self.badges = badges
        self.query_one(MetroLine).set_stations(d["stations"], badges)
        self.prev_status = {s["token"]: s["status"] for s in d["stations"]}
        warns = sum(1 for r in d["health"] if r[2] in ("warn", "bad"))
        self.query_one(TopBar).info = {"score": d["score"], "band": d["band"],
                                       "gates": (d["gates_pass"], len(d["gates"])) if any(g.get("state") in ("passed", "failed") for g in d["gates"]) else None,
                                       "warns": f"{warns} warning{'s' if warns != 1 else ''}" if warns else ""}
        self.query_one(HealthPanel).show(d)
        self._update_foot(d["stations"], d["total_secs"])
        self.query_one(DieMap).set_data(d["maps"], d["die"])
        self._update_drawer(self.query_one(MetroLine).sel)

    def _update_foot(self, stations: list[dict], total: float | None = None) -> None:
        ok = sum(1 for x in stations if x["status"] == "success")
        total = sum(x["secs"] for x in stations) if total is None else total
        slow = max(stations, key=lambda x: x["secs"], default=None)
        ft = Text(no_wrap=True, overflow="ellipsis")
        ft.append(f"{ok}/{len(stations)} done", style=OK if ok == len(stations) else WARN)
        if total:
            ft.append(f"  ·  total {ecc.fmt_secs(total)}", style=SOFT)
        if slow and slow["secs"] > 120:
            ft.append(f"\n▲ slowest: {slow['label']} {ecc.fmt_secs(slow['secs'])}", style=WARN)
        self.query_one("#flow-foot", Static).update(ft)

    # ---- animation / run sync
    def _anim(self) -> None:
        app = self.app
        metro = self.query_one(MetroLine)
        top = self.query_one(TopBar)
        mine = app.run_active and app.run_state and app.run_state.get("project") == str(self.project) \
            and app.run_state.get("ws") == self.ws
        if mine:
            top.run = dict(app.run_state)
            metro.advance()
            if metro.sel == app.run_state.get("step_token"):
                self._update_drawer(metro.sel, live=True)
        elif top.run:
            top.run = None
        if metro.flash:
            metro.advance()

    def apply_status(self, stations: list[dict]) -> None:
        """Live status from the run poller."""
        metro = self.query_one(MetroLine)
        st = self.app.run_state or {}
        if self.app.run_active and self.follow and st.get("running") and st["step_token"] != metro.sel \
                and any(x["token"] == st["step_token"] for x in stations):
            self._auto = True
            metro.sel = st["step_token"]
        metro.set_stations(stations, getattr(self, "badges", {}))
        self._update_foot(stations)
        if self._auto:
            self._auto = False
            self._update_drawer(metro.sel, live=True)
        new = {s["token"]: s["status"] for s in stations}
        if self.prev_status.get("placement") != "success" and new.get("placement") == "success":
            self._reload_maps()
        self.prev_status = new

    def _reload_maps(self) -> None:
        def work():
            return D.find_maps(self.wsdir), D.die_size(self.wsdir)
        run_bg(self, work, lambda r: None if isinstance(r, Exception) else self.query_one(DieMap).set_data(*r))

    def run_finished(self, ok: bool, state: dict) -> None:
        self.query_one(TopBar).run = None
        if not ok and state.get("seen_running"):
            tok = state.get("step_token") or ""
            m = self.query_one(MetroLine)
            m.flash, m.flash_token = 6, tok
        self._confetti_after_load = ok      # celebrate once the final state is on screen
        self.load()

    # ---- station drawer
    @on(MetroLine.Selected)
    def _station(self, ev: MetroLine.Selected) -> None:
        if not self._auto:
            self.follow = False
        self.sel_token = ev.token
        self._update_drawer(ev.token)

    def _update_drawer(self, token: str, live: bool = False) -> None:
        if not token or not self.data:
            return
        st = next((s for s in self.data["stations"] if s["token"] == token), None)
        metro = self.query_one(MetroLine)
        cur = next((i for i in metro.items if i["token"] == token), st) or st
        if cur is None:
            return
        app = self.app
        running_here = bool(app.run_active and app.run_state and app.run_state.get("step_token") == token
                            and app.run_state.get("project") == str(self.project))
        status = "running" if running_here else cur["status"]
        glyph, col = {"success": ("✓", OK), "running": ("▶", ORANGE), "failed": ("✗", BAD),
                      "pending": ("○", DIM)}.get(status, ("●", WARN))
        drawer = self.query_one("#drawer")
        drawer.border_title = f"STATION · {cur['label']}" + (f" ({cur['tool']})" if cur.get("tool") else "")
        head = Text(no_wrap=True, overflow="ellipsis")
        head.append(f"{glyph} {status}", style=f"bold {col}")
        if cur["secs"]:
            head.append(f" · {ecc.fmt_secs(cur['secs'])}", style=FG)

        def work():
            return D.step_metrics(self.wsdir, token), D.log_tail(self.wsdir, token, 4)

        def done(res):
            if isinstance(res, Exception) or token != self.query_one(MetroLine).sel:
                return
            metrics, tail = res
            h = head.copy()
            for lab, val in metrics:
                h.append(" · ", style=DIM)
                h.append(f"{lab} ", style=DIM)
                h.append(val, style=SOFT)
            if running_here or live:
                tail = [x for x in app.run_lines if "frozen builds" not in x and x.strip()][-4:]
            self.query_one("#d-head", Static).update(h)
            lg = Text(no_wrap=True, overflow="ellipsis")
            wlim = max(20, self.query_one("#d-log").size.width - 1)
            for i, ln in enumerate(tail or ["(no log yet)"]):
                if i:
                    lg.append("\n")
                lg.append(ln[:wlim], style=DIM if "rror" not in ln else BAD)
            self.query_one("#d-log", Static).update(lg)

        if live:
            done((D.step_metrics(self.wsdir, token), []))
        else:
            self.query_one("#d-head", Static).update(head)
            run_bg(self, work, done, group="drawer")

    # ---- actions
    def action_home(self) -> None:
        self.app.dirty.add(str(self.project))
        self.app.pop_screen()

    def action_cycle(self, d: int) -> None:
        self.query_one(DieMap).cycle(d)

    def action_timing(self) -> None:
        if self.data:
            self.app.push_screen(TimingScreen(self.data))

    def action_next_ws(self) -> None:
        ids = D.workspace_ids(self.project)
        if len(ids) < 2:
            self.notify("only one workspace in this chip", timeout=2)
            return
        nxt = ids[(ids.index(self.ws) + 1) % len(ids)] if self.ws in ids else ids[0]
        self.app.pop_screen()
        self.app.open_chip(str(self.project), nxt)

    def _token_flow_id(self, token: str) -> str:
        return D.flow_id_for(token, getattr(self, "step_ids", None) or self.flow_ids)

    def _base_args(self) -> list[str]:
        return ["run", "--project", str(self.project), "--workspace", self.ws]

    def action_run_from(self) -> None:
        if not self.data:
            return
        tok = self.query_one(MetroLine).sel
        fid = self._token_flow_id(tok)
        label = D.label_for(tok)
        if not self.data["has_steps"]:
            a_from = self._base_args() + ["--plain"]
            label = "the start"
        else:
            a_from = self._base_args() + ["--from", fid, "--plain"]
        a_only = self._base_args() + ["--only", fid, "--force", "--plain"]
        prefix = " ".join(ecc.taskset_prefix())
        self.app.push_screen(RunPopover(label, a_from, a_only, prefix), lambda r: self._popover(r, tok))

    def action_run_only(self) -> None:
        if not self.data:
            return
        tok = self.query_one(MetroLine).sel
        fid = self._token_flow_id(tok)
        a = self._base_args() + ["--only", fid, "--force", "--plain"]
        self.app.push_screen(RunPopover(D.label_for(tok), a, a, " ".join(ecc.taskset_prefix())),
                             lambda r: self._popover(r, tok))

    def _popover(self, r, tok: str) -> None:
        if not r:
            return
        kind, args = r
        if kind == "run":
            self.app.start_run(args, str(self.project), self.ws, self.data["stations"] if self.data else [])
        else:
            steps = getattr(self, "step_ids", None) or self.flow_ids
            self.app.push_screen(RunModal(str(self.project), self.ws, self.flow_ids, steps, "from",
                                          self._token_flow_id(tok)),
                                 lambda a: a and self.app.start_run(a, str(self.project), self.ws,
                                                                    self.data["stations"] if self.data else []))

    def action_stop(self) -> None:
        self.app.action_cancel_run()

    def action_log(self) -> None:
        tok = self.query_one(MetroLine).sel
        app = self.app
        st = app.run_state
        is_live = bool(
            app.run_active and st
            and st.get("project") == str(self.project)
            and st.get("ws") == self.ws
            and D.canonical_token(str(st.get("step_token") or "")) == D.canonical_token(tok)
        )
        if is_live:
            ls = LogScreen(f"live run · {D.label_for(tok)}", list(app.run_lines), live=True)
            app.log_screen = ls
            app.push_screen(ls)
            return

        def work():
            rc, out, cl, secs = ecc.run_text(["log", tok, "--project", str(self.project), "--workspace", self.ws],
                                             echo=False)
            return out

        def done(out):
            if isinstance(out, Exception):
                out = str(out)
            lines = [ecc.strip_ansi(x) for x in out.splitlines()] or ["(no log output)"]
            self.app.push_screen(LogScreen(f"log · {D.label_for(tok)}", lines))

        run_bg(self, work, done, group="log")

    @on(Button.Pressed, "#b-from")
    def _bf(self) -> None:
        self.action_run_from()

    @on(Button.Pressed, "#b-only")
    def _bo(self) -> None:
        self.action_run_only()

    @on(Button.Pressed, "#b-log")
    def _bl(self) -> None:
        self.action_log()


# ======================================================================== timing

def fmt_hold(h: float) -> str:
    return f"{h * 1000:+.0f}ps" if abs(h) < 1 else f"{h:+.2f}ns"


def slack_bg(kind: str, v: float | None, selected: bool = False) -> str:
    if v is None:
        return "#1E1917"
    if kind == "setup":
        if v < 0:
            c = "#8A2A2A"
        else:
            c = lerp_color("#6B4F10", "#2F5A1E", min(1.0, v / 1.0))
    else:
        ps = v * 1000
        if ps < 0:
            c = "#8A2A2A"
        elif ps < 50:
            c = "#7A560A"
        else:
            c = lerp_color("#4A5A1E", "#2F5A1E", min(1.0, (ps - 50) / 500))
    return lerp_color(c, "#FFFFFF", 0.18) if selected else c


class TimingScreen(Screen):
    BINDINGS = [("escape", "back", "Back"), ("left", "move(-1,0)", ""), ("right", "move(1,0)", ""),
                ("up", "move(0,-1)", ""), ("down", "move(0,1)", "")]
    CW = 13
    RH = 3

    def __init__(self, data: dict) -> None:
        super().__init__()
        self.d = data
        self.m = data["matrix"]
        self.cx = 0
        self.cy = 0
        # start on the tightest cell
        best = None
        for yi, c in enumerate(self.m["corners"]):
            for xi, r in enumerate(self.m["rcs"]):
                cell = self.m["cells"].get((c, r))
                if cell and cell["hold"] is not None and (best is None or cell["hold"] < best[0]):
                    best = (cell["hold"], xi, yi)
        if best:
            self.cx, self.cy = best[1], best[2]

    def compose(self) -> ComposeResult:
        yield TopBar(id="top")
        with Horizontal(id="t-body"):
            with Vertical(id="t-left"):
                yield Static("", id="matrix")
                yield Static("", id="t-legend")
            with VerticalScroll(id="t-right"):
                yield Static("", id="t-groups")
                yield Static("", id="t-drv")
                yield Static("", id="t-paths")
        yield HintBar(id="hints")

    def on_mount(self) -> None:
        self.query_one(TopBar).crumbs = ("ECOS", self.d["name"], self.d["ws"], "timing")
        self.query_one(TopBar).info = {"score": self.d["score"], "band": self.d["band"]}
        self.query_one(HintBar).hints = (("←↑→↓", "pick corner"), ("click", "select"), ("esc", "back"))
        self.query_one("#t-left").border_title = "CORNER MATRIX · setup | hold"
        self.query_one("#t-left").border_subtitle = "slack in ns (setup) and ps (hold)"
        self.render_all()

    def on_resize(self, _e) -> None:
        self.call_after_refresh(self.render_all)

    def render_all(self) -> None:
        corners, rcs = self.m["corners"], self.m["rcs"]
        left = self.query_one("#t-left")
        iw, ih = max(40, left.size.width - 4), max(12, left.size.height - 4)
        if corners and rcs:
            self.CW = max(13, min(30, (iw - 10) // len(rcs) - 1))
            self.RH = max(3, min(8, (ih - 12) // len(corners) - 1))
        cw, rh = self.CW, self.RH
        t = Text(no_wrap=True)
        t.append(" " * 10)
        for r in rcs:
            t.append(r.center(cw), style=f"bold {ORANGE}")
            t.append(" ")
        t.append("\n\n")
        for yi, c in enumerate(corners):
            mid = rh // 2
            for sub in range(rh):
                t.append((c if sub == mid else "").ljust(10), style=f"bold {SOFT}")
                for xi, r in enumerate(rcs):
                    cell = self.m["cells"].get((c, r))
                    sel = (xi == self.cx and yi == self.cy)
                    if cell is None:
                        t.append("·".center(cw) if sub == mid else " " * cw, style=f"{DIM} on #1A1514")
                    else:
                        s, h = cell["setup"], cell["hold"]
                        hw = cw // 2
                        sbg, hbg = slack_bg("setup", s, sel), slack_bg("hold", h, sel)
                        sv = f"{s:+.2f}" if s is not None else "—"
                        hv = fmt_hold(h) if h is not None else "—"
                        st = "bold " + FG if sel else FG
                        if sub == mid:
                            t.append(sv.center(hw), style=f"{st} on {sbg}")
                            t.append(hv.center(cw - hw), style=f"{st} on {hbg}")
                        else:
                            mark = "▔" if sub == 0 else "▁"
                            if sel and sub in (0, rh - 1):
                                t.append(mark * hw, style=f"{ORANGE} on {sbg}")
                                t.append(mark * (cw - hw), style=f"{ORANGE} on {hbg}")
                            else:
                                t.append(" " * hw, style=f"on {sbg}")
                                t.append(" " * (cw - hw), style=f"on {hbg}")
                    t.append(" ")
                t.append("\n")
            t.append("\n" if yi < len(corners) - 1 else "")
        self.query_one("#matrix", Static).update(t)
        lg = Text(no_wrap=True)
        lg.append("\nsetup ", style=DIM)
        for v in (-0.1, 0.1, 0.4, 0.7, 1.0):
            lg.append("   ", style=f"on {slack_bg('setup', v)}")
        lg.append(" <0 → ≥1ns     hold ", style=DIM)
        for v, lab in ((-0.01, " <0 "), (0.03, " thin <50ps "), (0.3, " ok")):
            lg.append("   ", style=f"on {slack_bg('hold', v)}")
            lg.append(lab, style=DIM)
        cells = self.m["cells"]
        tight_h = min(((c["hold"], k) for k, c in cells.items() if c["hold"] is not None), default=None)
        tight_s = min(((c["setup"], k) for k, c in cells.items() if c["setup"] is not None), default=None)
        lg.append("\n\n")
        if tight_s:
            lg.append("tightest setup  ", style=DIM)
            lg.append(f"{tight_s[0]:+.2f} ns", style=f"bold {OK if tight_s[0] >= 0 else BAD}")
            lg.append(f"  {tight_s[1][0]} / {tight_s[1][1]}\n", style=SOFT)
        if tight_h:
            thin = tight_h[0] * 1000 < 50
            lg.append("tightest hold   ", style=DIM)
            lg.append(f"{fmt_hold(tight_h[0])}", style=f"bold {WARN if thin else OK}")
            lg.append(f"  {tight_h[1][0]} / {tight_h[1][1]}", style=SOFT)
            if thin:
                lg.append("   thin margin", style=WARN)
        lg.append(f"\n{len(cells)} STA corners analysed", style=DIM)
        self.query_one("#t-legend", Static).update(lg)
        self._details()

    def _sel(self) -> tuple[str, str] | None:
        try:
            return self.m["corners"][self.cy], self.m["rcs"][self.cx]
        except IndexError:
            return None

    def _details(self) -> None:
        sel = self._sel()
        if not sel:
            self.query_one("#t-groups", Static).update(Text("no STA corners found (run sta first)", style=DIM))
            return
        c, r = sel
        cell = self.m["cells"].get((c, r))
        right = self.query_one("#t-right")
        right.border_title = f"{c} / {r}"
        g = Text(no_wrap=True)
        g.append("CLOCK GROUPS\n", style=f"bold {ORANGE}")
        if not cell:
            g.append("no data for this corner", style=DIM)
        else:
            g.append("group".ljust(12) + "setup".rjust(8) + "hold".rjust(9) + "\n", style=DIM)
            for pg in cell["groups"]:
                sw, hw_ = pg.get("setup", {}).get("wns"), pg.get("hold", {}).get("wns")
                g.append(pg.get("name", "?")[:12].ljust(12), style=FG)
                g.append(f"{sw:+.2f}".rjust(8) if sw is not None else "—".rjust(8),
                         style=BAD if (sw or 0) < 0 else OK)
                g.append(fmt_hold(hw_).rjust(9) if hw_ is not None else "—".rjust(9),
                         style=BAD if (hw_ or 0) < 0 else (WARN if (hw_ or 1) * 1000 < 50 else OK))
                g.append("\n")
            g.append(f"\nviolations  setup {cell['setup_nvp']} · hold {cell['hold_nvp']}", style=DIM)
            g.append(f"\nTNS         setup {cell['setup_tns']} · hold {cell['hold_tns']}", style=DIM)
        self.query_one("#t-groups", Static).update(g)
        d = Text(no_wrap=True)
        d.append("DRV PER CORNER\n", style=f"bold {ORANGE}")
        for cc in self.m["corners"]:
            cells = [self.m["cells"][(cc, rr)] for rr in self.m["rcs"] if (cc, rr) in self.m["cells"]]
            drv = {k: max((x["drv"][k] for x in cells), default=0) for k in ("tran", "cap", "fanout")}
            d.append(cc.ljust(9), style=SOFT)
            for k, lab in (("tran", "tran"), ("cap", "cap"), ("fanout", "fo")):
                bad = drv[k] > 0
                d.append(f" {lab} {drv[k]} ", style=f"bold {BG} on {BAD}" if bad else f"{OK} on #232A1C")
                d.append(" ")
            d.append("\n")
        self.query_one("#t-drv", Static).update(d)
        p = Text(no_wrap=True)
        p.append("WORST PATHS (reg2reg)\n", style=f"bold {ORANGE}")
        paths, note = D.worst_paths(self.d_ws(), c, r)
        if paths:
            for x in paths:
                p.append(f"{x.get('slack', 0):+.3f} ", style=BAD if x.get("slack", 0) < 0 else OK)
                p.append(f"{x.get('start', '?')} → {x.get('end', '?')}\n"[:60], style=FG)
        else:
            p.append(note + "\n", style=DIM)
            if cell:
                p.append(f"{len(cell['groups'])} path groups · all met", style=DIM)
        self.query_one("#t-paths", Static).update(p)

    def d_ws(self) -> Path:
        return Path(self.d["project"]) / self.d["ws"]

    def action_move(self, dx: int, dy: int) -> None:
        """Step to the next populated tile in that direction (empty tiles are skipped)."""
        x, y = self.cx, self.cy
        nx, ny = len(self.m["rcs"]), len(self.m["corners"])
        while True:
            x, y = x + dx, y + dy
            if not (0 <= x < nx and 0 <= y < ny):
                return
            if (self.m["corners"][y], self.m["rcs"][x]) in self.m["cells"]:
                self.cx, self.cy = x, y
                self.render_all()
                return

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_click(self, event) -> None:
        w = self.query_one("#matrix")
        off = event.get_content_offset(w) if hasattr(event, "get_content_offset") else None
        if off is None:
            return
        x, y = off.x - 10, off.y - 2
        if x < 0 or y < 0:
            return
        xi, yi = x // (self.CW + 1), y // (self.RH + 1)
        if xi < len(self.m["rcs"]) and yi < len(self.m["corners"]) and y % (self.RH + 1) < self.RH \
                and (self.m["corners"][yi], self.m["rcs"][xi]) in self.m["cells"]:
            self.cx, self.cy = xi, yi
            self.render_all()
