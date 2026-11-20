"""ecctui widgets: DieMap, MetroLine, HealthPanel, ChipCard, Confetti, HintBar, TopBar."""
from __future__ import annotations

import math
import random
import time
from typing import Any

from rich.color import Color
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.geometry import Region, Spacing
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import Static

from . import data as D
from . import ecc
from .theme import (BAD, BG, COOL, DEEP, DIM, FG, HEAT, LINE, LOGO_GRADIENT, OK, ORANGE, PANEL,
                    PHASE_COLORS, SOFT, SURFACE, WARN, band_color, build_lut, gradient_at, lerp_color)

LUT_N = 64
HEAT_LUT = build_lut(HEAT, LUT_N)
COOL_LUT = build_lut(COOL, LUT_N)
EMPTY_PIX = "#1E1917"
_style_cache: dict[tuple[str, str], Style] = {}


def px_style(fg: str, bg: str) -> Style:
    k = (fg, bg)
    s = _style_cache.get(k)
    if s is None:
        s = _style_cache[k] = Style(color=Color.parse(fg), bgcolor=Color.parse(bg))
    return s


def text_strip(t: Text, width: int, app: Any, bg: str = SURFACE) -> Strip:
    segs = list(t.render(app.console))
    return Strip(segs).adjust_cell_length(width, Style(bgcolor=Color.parse(bg)))


def lut_for(key: str) -> list[str]:
    return COOL_LUT if key == "overflow" else HEAT_LUT


def pixel_color(v: float, lut: list[str]) -> str:
    if v < 0:
        return EMPTY_PIX
    return lut[min(LUT_N - 1, int(v * (LUT_N - 1) + 0.5))]


def halfblock_rows(pix: list[list[float]], lut: list[str],
                   under: list[list[float]] | None = None) -> list[list[Segment]]:
    """pix has 2*rows rows; returns `rows` lists of segments.

    With `under` (e.g. the density map), pixels whose value is <= 0 show a dimmed copy of it,
    so sparse maps such as congestion overflow keep their spatial context.
    """
    def col(v: float, u: float | None) -> str:
        if under is not None and v <= 0.0:
            return lerp_color("#1B1716", pixel_color(u if u is not None else -1, HEAT_LUT), 0.30)
        return pixel_color(v, lut)

    out = []
    for y in range(0, len(pix) - 1, 2):
        up, lo = pix[y], pix[y + 1]
        if under is not None:
            uu, ul = under[y], under[y + 1]
            out.append([Segment("▀", px_style(col(up[x], uu[x]), col(lo[x], ul[x]))) for x in range(len(up))])
        else:
            out.append([Segment("▀", px_style(col(up[x], None), col(lo[x], None))) for x in range(len(up))])
    return out


def thumb_text(grid_norm: list[list[float]] | None, w: int, rows: int, key: str = "density") -> list[Text]:
    """Mini die thumbnail (rows x w cells) as Rich Text lines."""
    if not grid_norm:
        return [Text("░" * w, style="#2A2220") for _ in range(rows)]
    pix = D.resample(grid_norm, w, rows * 2, smooth=True)
    lut = lut_for(key)
    lines = []
    for y in range(0, rows * 2, 2):
        t = Text(no_wrap=True)
        for x in range(w):
            t.append("▀", style=px_style(pixel_color(pix[y][x], lut), pixel_color(pix[y + 1][x], lut)))
        lines.append(t)
    return lines


# --------------------------------------------------------------- hint + top bar --

TIPS = [
    "Space on a station re-runs from there",
    "ctrl+p lists every ecc command",
    "←/→ cycles density, RUDY, overflow and margin maps",
    "hover the die map to read the value under the cursor",
    "t opens the timing corner matrix",
    "o runs only the selected station",
    "l opens the full log of a station",
    "click a map tab or a station: the mouse works everywhere",
    "n creates a new chip from your RTL",
    "a hold margin under 50 ps is flagged as thin",
]


class HintBar(Widget):
    DEFAULT_CSS = "HintBar { height: 1; background: #1B1716; }"
    hints: reactive[tuple] = reactive((), layout=False)
    tip: reactive[str] = reactive("")

    def render(self) -> Text:
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append(" ")
        for k, label in self.hints[:5]:
            t.append(f" {k} ", style=f"bold {BG} on {ORANGE}")
            t.append(f" {label}   ", style=FG)
        if self.tip:
            tip = f"✦ tip: {self.tip} "
            pad = self.size.width - t.cell_len - len(tip)
            if pad > 2:
                t.append(" " * pad)
                t.append(tip, style=f"italic {DIM}")
        return t


def fmt_clock(sec: float) -> str:
    return ecc.fmt_secs(sec)


class TopBar(Widget):
    DEFAULT_CSS = "TopBar { height: 1; background: #1B1716; }"
    crumbs: reactive[tuple] = reactive(("ECOS",))
    info: reactive[dict] = reactive({}, always_update=True)
    run: reactive[dict | None] = reactive(None, always_update=True)

    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh)

    def render(self) -> Text:
        w = self.size.width
        if self.run:
            return self._render_run(w)
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append(" ◆ ", style=f"bold {ORANGE}")
        for i, c in enumerate(self.crumbs):
            if i:
                t.append(" › ", style=DIM)
            last = i == len(self.crumbs) - 1
            t.append(str(c), style=f"bold {FG}" if last else (f"bold {ORANGE}" if i == 0 else SOFT))
        right = Text(no_wrap=True)
        i = self.info
        if i.get("score") is not None:
            col = band_color(i.get("band"))
            right.append(f"{i['score']:.1f} ", style=f"bold {col}")
            right.append(f"{str(i.get('band', '')).replace('_', ' ')}", style=f"bold {col}")
            right.append("   ")
        if i.get("gates"):
            ok = i["gates"][0] == i["gates"][1]
            right.append(f"{'✓' if ok else '✗'} gates {i['gates'][0]}/{i['gates'][1]}", style=OK if ok else BAD)
            right.append("   ")
        if i.get("warns"):
            right.append(f"▲ {i['warns']}", style=WARN)
            right.append("   ")
        right.append(time.strftime("%H:%M"), style=DIM)
        right.append(" ")
        pad = w - t.cell_len - right.cell_len
        if pad > 0:
            t.append(" " * pad)
        t.append_text(right)
        return t

    def _render_run(self, w: int) -> Text:
        r = self.run or {}
        done, total = r.get("done", 0), max(1, r.get("total", 1))
        el = time.time() - r.get("started", time.time())
        eta = r.get("eta")
        frac = min(1.0, (done + r.get("frac", 0.0)) / total)
        label = f" ▶ {r.get('name', '')} · step {min(done + 1, total)}/{total} · {r.get('step') or '…'} · {fmt_clock(el)}"
        label += f" · ETA {fmt_clock(eta)}" if eta is not None else " · ETA …"
        label = label.ljust(w)[:w]
        n = int(frac * w)
        t = Text(no_wrap=True)
        for x, ch in enumerate(label):
            if x < n:
                bg = gradient_at([ORANGE, "#FF8F00", "#FFB74D"], x / max(1, w - 1))
                t.append(ch, style=f"bold {BG} on {bg}")
            else:
                t.append(ch, style=f"{FG} on #2A211D")
        return t


# ------------------------------------------------------------------- DieMap --

class DieMap(Widget, can_focus=False):
    """Truecolor half-block die map built from DreamPlace grids."""

    DEFAULT_CSS = "DieMap { height: 1fr; }"
    STRETCH = 1.3

    class Changed(Message):
        def __init__(self, idx: int) -> None:
            super().__init__()
            self.idx = idx

    idx: reactive[int] = reactive(0)

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.maps: list[D.MapInfo] = []
        self.die: tuple[float, float] | None = None
        self.cache: dict[int, tuple] = {}
        self.pending: set[int] = set()
        self._busy = True
        self.phase = 0
        self._tabs_x: list[tuple[int, int, int]] = []
        self._render_cache: dict[tuple, list[list[Segment]]] = {}
        self._geom: tuple[int, int, int, int] | None = None   # x0, y0 (content rows), w, h of die area
        self._hover = ""

    def on_mount(self) -> None:
        self.set_interval(0.12, self._tick)

    def _tick(self) -> None:
        if self._busy or (self.maps and self.idx not in self.cache) or (not self.maps and self.app.run_active):
            self.phase += 1
            self.refresh()

    # -- data --
    def set_data(self, maps: list[D.MapInfo], die: tuple[float, float] | None) -> None:
        self.maps, self.die = maps, die
        self.cache.clear()
        self._render_cache.clear()
        self.pending.clear()
        self._busy = False
        self.idx = min(self.idx, max(0, len(maps) - 1))
        self._load(self.idx)
        self._update_title()
        self.refresh()

    def show_loading(self) -> None:
        self._busy = True
        self.refresh()

    def _load(self, i: int) -> None:
        if not self.maps or i in self.cache or i in self.pending:
            return
        self.pending.add(i)
        mi = self.maps[i]

        def work() -> None:
            try:
                g = D.read_grid(mi.path)
                nrm, lo, hi = D.prepare_grid(g, mi.key)
                res = (g, nrm, lo, hi)
            except Exception:
                res = None
            self.app.call_from_thread(self._loaded, i, res)

        self.run_worker(work, thread=True, exit_on_error=False)

    def _loaded(self, i: int, res: tuple | None) -> None:
        self.pending.discard(i)
        self.cache[i] = res if res else ([[0.0]], [[-1.0]], 0.0, 0.0)
        self._update_title()
        self.refresh()

    def watch_idx(self, _old: int, _new: int) -> None:
        self._load(self.idx)
        self._update_title()
        self.refresh()
        self.post_message(self.Changed(self.idx))

    def cycle(self, d: int) -> None:
        if self.maps:
            self.idx = (self.idx + d) % len(self.maps)

    def _update_title(self) -> None:
        if not self.maps:
            self.border_title = "DIE · waiting for placement"
            self.border_subtitle = ""
            return
        mi = self.maps[self.idx]
        t = f"DIE · {self.die[0]:.0f} × {self.die[1]:.0f} µm · " if self.die else "DIE · "
        self.border_title = t + mi.title
        c = self.cache.get(self.idx)
        if self._hover:
            self.border_subtitle = self._hover
        elif c:
            _, _, lo, hi = c
            self.border_subtitle = "uniform map: no spread in this workspace" if lo == hi else "hover the map for values"
        else:
            self.border_subtitle = "loading…"

    # -- layout --
    def _die_box(self, W: int, H: int) -> tuple[int, int, int, int]:
        """(x0, y0, w, h) of the die inside the map area (W x H cells)."""
        ar = 1.0
        if self.die and self.die[1] > 0:
            ar = self.die[0] / self.die[1]
        h = H
        w_ideal = ar * 2 * h
        if w_ideal <= W:
            w = round(min(W, w_ideal * self.STRETCH))   # near-square dies may stretch a little to fill
        else:
            w = W
            h = max(1, min(H, round(w / (2 * ar))))
        return (W - w) // 2, (H - h) // 2, max(1, w), max(1, h)

    def _under_idx(self) -> int | None:
        if not self.maps:
            return None
        cur = self.maps[self.idx]
        for i, m in enumerate(self.maps):
            if m.key == "density" and m.step == cur.step:
                return i
        return None

    def _pix_rows(self, w: int, h: int) -> list[list[Segment]] | None:
        c = self.cache.get(self.idx)
        if not c:
            return None
        mi = self.maps[self.idx]
        under = None
        if mi.key == "overflow":
            ui = self._under_idx()
            if ui is not None:
                self._load(ui)
                uc = self.cache.get(ui)
                if uc and uc[1]:
                    under = D.resample(uc[1], w, h * 2, smooth=True)
        key = (self.idx, w, h, under is not None)
        r = self._render_cache.get(key)
        if r is None:
            _, nrm, _, _ = c
            smooth = mi.key != "overflow"
            pix = D.resample(nrm, w, h * 2, smooth=smooth)
            r = self._render_cache[key] = halfblock_rows(pix, lut_for(mi.key), under)
            if len(self._render_cache) > 12:
                self._render_cache.pop(next(iter(self._render_cache)))
        return r

    def render_line(self, y: int) -> Strip:
        W, H = self.size.width, self.size.height
        if y == 0:
            return self._tabs_strip(W)
        if y == H - 1 and H > 3:
            return self._legend_strip(W) if self.maps else self._margin_strip(W, y)
        MH = max(1, H - 2)
        my = y - 1
        x0, y0, dw, dh = self._die_box(W, MH)
        self._geom = (x0, y0, dw, dh)
        blank = Segment(" ", Style(bgcolor=Color.parse(SURFACE)))
        if not self.maps:
            return self._empty_strip(W, MH, my, x0, y0, dw, dh)
        rows = self._pix_rows(dw, dh)
        if rows is None or my < y0 or my >= y0 + dh:
            if rows is None and y0 <= my < y0 + dh:
                return self._shimmer_strip(W, x0, dw, my - y0)
            return self._margin_strip(W, my)
        segs = [self._margin_cell(x, my) for x in range(x0)]
        line = rows[my - y0]
        c = self.cache.get(self.idx)
        if c and c[2] == c[3] and self.maps[self.idx].key != "overflow" and my - y0 == dh // 2:
            msg = " no spread in this workspace "
            if dw > len(msg) + 2:
                a = (dw - len(msg)) // 2
                line = list(line)
                for i, ch in enumerate(msg):
                    base = line[a + i].style
                    line[a + i] = Segment(ch, Style(color=Color.parse("#FFB74D"), bgcolor=base.bgcolor if base else None))
        segs += line
        segs += [self._margin_cell(x, my) for x in range(x0 + dw, W)]
        return Strip(segs, W)

    def _empty_strip(self, W: int, MH: int, my: int, x0: int, y0: int, dw: int, dh: int) -> Strip:
        """Placeholder die: dim animated shimmer and a hint while there is no map yet."""
        if my < y0 or my >= y0 + dh:
            return self._margin_strip(W, my)
        segs = [self._margin_cell(x, my) for x in range(x0)]
        ry = my - y0
        if self._busy:
            msg = " loading… "
        elif self.app.run_active:
            msg = " the die map appears when placement finishes "
        else:
            msg = " no placement map grids in this workspace yet "
        mid = dh // 2
        for x in range(dw):
            edge = x == 0 or x == dw - 1 or ry == 0 or ry == dh - 1
            wave = (math.sin((x + ry * 2 - self.phase * 0.7) / 4.0) + 1) / 2
            col = lerp_color("#241C18", "#4A2A14", wave)
            ch = "░▒"[(x + ry + self.phase // 3) % 2] if not edge else "▒"
            if ry == mid and (dw - len(msg)) // 2 <= x < (dw - len(msg)) // 2 + len(msg):
                segs.append(Segment(msg[x - (dw - len(msg)) // 2], Style(color=Color.parse(SOFT), bgcolor=Color.parse("#1B1716"))))
            else:
                segs.append(Segment(ch, Style(color=Color.parse(col if not edge else "#5A3A20"),
                                              bgcolor=Color.parse(SURFACE))))
        segs += [self._margin_cell(x, my) for x in range(x0 + dw, W)]
        return Strip(segs, W)

    def _margin_cell(self, x: int, y: int) -> Segment:
        if (x + y) % 4 == 0 and x % 2 == 0:
            return Segment("·", Style(color=Color.parse("#3D3029"), bgcolor=Color.parse(SURFACE)))
        return Segment(" ", Style(bgcolor=Color.parse(SURFACE)))

    def _margin_strip(self, W: int, y: int) -> Strip:
        return Strip([self._margin_cell(x, y) for x in range(W)], W)

    def _shimmer_strip(self, W: int, x0: int, dw: int, ry: int) -> Strip:
        segs = [self._margin_cell(x, ry) for x in range(x0)]
        chars = "░▒▓▒"
        for x in range(dw):
            v = (x + ry * 2 - self.phase) % 12
            ch = chars[v % 4] if v < 8 else "░"
            col = lerp_color("#2A211D", "#7A3306", (math.sin((x - self.phase) / 3.0 + ry) + 1) / 2)
            segs.append(Segment(ch, Style(color=Color.parse(col), bgcolor=Color.parse(SURFACE))))
        segs += [self._margin_cell(x, ry) for x in range(x0 + dw, W)]
        return Strip(segs, W)

    def _tabs_strip(self, W: int) -> Strip:
        if not self.maps:
            return Strip([Segment(" " * W, Style(bgcolor=Color.parse(SURFACE)))], W)
        t = Text(no_wrap=True, style=f"on {SURFACE}")
        self._tabs_x = []
        x = 0
        t.append(" ")
        x = 1
        for i, mi in enumerate(self.maps):
            lab = f" {mi.label} "
            if x + len(lab) + 8 > W and i != self.idx:
                if x + 2 < W:
                    t.append(" …", style=DIM)
                break
            st = f"bold {BG} on {ORANGE}" if i == self.idx else f"{DIM} on #241E1B"
            t.append(lab, style=st)
            t.append(" ")
            self._tabs_x.append((x, x + len(lab), i))
            x += len(lab) + 1
        if len(self.maps) > 1:
            right = "◀ ▶ "
            pad = W - t.cell_len - len(right)
            if pad > 0:
                t.append(" " * pad)
                t.append(right, style=f"bold {ORANGE}")
        return text_strip(t, W, self.app)

    def _legend_strip(self, W: int) -> Strip:
        mi = self.maps[self.idx] if self.maps else None
        lut = lut_for(mi.key) if mi else HEAT_LUT
        c = self.cache.get(self.idx)
        t = Text(no_wrap=True, style=f"on {SURFACE}")
        lo_s = hi_s = ""
        if c and mi:
            _, _, lo, hi = c
            lo_s, hi_s = (_num(lo), _num(hi)) if lo != hi else ("uniform", _num(lo))
        t.append(f" {lo_s or 'low'} ", style=SOFT)
        n = max(8, min(30, W - 24))
        for i in range(n):
            col = lut[int(i / (n - 1) * (LUT_N - 1))]
            t.append(" ", style=f"on {col}")
        t.append(f" {hi_s or 'high'}", style=SOFT)
        if mi and mi.key == "margin" and c and c[2] != c[3]:
            t.append("  hot = tight", style=DIM)
        return text_strip(t, W, self.app)

    # -- mouse --
    def on_click(self, event: events.Click) -> None:
        off = event.get_content_offset(self)
        if off is None:
            return
        if off.y == 0:
            for a, b, i in self._tabs_x:
                if a <= off.x < b:
                    self.idx = i
                    return

    def on_mouse_move(self, event: events.MouseMove) -> None:
        off = event.get_content_offset(self)
        old = self._hover
        self._hover = ""
        c = self.cache.get(self.idx)
        if off and c and self._geom and 1 <= off.y < self.size.height - 1:
            x0, y0, dw, dh = self._geom
            px, py = off.x - x0, off.y - 1 - y0
            if 0 <= px < dw and 0 <= py < dh:
                g = c[0]
                gx = min(len(g[0]) - 1, int(px / dw * len(g[0])))
                gy = min(len(g) - 1, int(py / dh * len(g)))
                v = g[gy][gx]
                um = ""
                if self.die:
                    um = f" @ ({(px + .5) / dw * self.die[0]:.0f}, {(1 - (py + .5) / dh) * self.die[1]:.0f}) µm"
                self._hover = f"{self.maps[self.idx].label} = {_num(v)}{um}"
        if self._hover != old:
            self._update_title()

    def on_leave(self, _event: events.Leave) -> None:
        if self._hover:
            self._hover = ""
            self._update_title()


def _num(v: float) -> str:
    if v == 0:
        return "0"
    if abs(v) >= 1e5:
        return f"{v:,.0f}".replace(",", " ")
    if abs(v) >= 100:
        return f"{v:.0f}"
    if abs(v) >= 1:
        return f"{v:.2f}"
    return f"{v:.4f}".rstrip("0").rstrip(".") if abs(v) >= 1e-4 else f"{v:.1e}"


# ---------------------------------------------------------------- MetroLine --

GLYPH = {"success": ("●", OK), "running": ("◉", ORANGE), "pending": ("○", "#6F645E"),
         "failed": ("✗", BAD), "warn": ("◐", WARN), "skipped": ("⊘", "#6F645E")}


def _agg(sts: list[dict]) -> str:
    ss = [s["status"] for s in sts]
    if any(x in ("failed", "error", "blocked") for x in ss):
        return "failed"
    if any(x == "running" for x in ss):
        return "running"
    if all(x == "success" for x in ss):
        return "warn" if any(s.get("warn") for s in sts) else "success"
    if all(x in ("pending", "not_started") for x in ss):
        return "pending"
    if all(x in ("skipped", "skip") for x in ss):
        return "skipped"
    return "pending"


class Station:
    """A visible row of the metro line (one station or a collapsed group)."""

    def __init__(self, items: list[dict], label: str | None = None) -> None:
        self.items = items
        self.tokens = [i["token"] for i in items]
        self.label = label or items[0]["label"]
        self.status = _agg(items)
        self.secs = sum(i["secs"] for i in items)
        self.phase = items[0]["phase"]
        self.badge = next((i["badge"] for i in items if i.get("badge")), "")

    @property
    def collapsed(self) -> bool:
        return len(self.items) > 1


class MetroLine(Static, can_focus=True):
    """The flow as a vertical metro line; ↑/↓ select, Enter open, click selects."""

    DEFAULT_CSS = "MetroLine { height: auto; }"

    class Selected(Message):
        def __init__(self, token: str, flow_open: bool = False) -> None:
            super().__init__()
            self.token = token
            self.open = flow_open

    def __init__(self, **kw) -> None:
        super().__init__("", **kw)
        self.items: list[dict] = []
        self.sel = ""
        self.tick = 0
        self.flash = 0
        self.flash_token = ""
        self._stn: list[Station] = []
        self._rows: list[tuple[str, int]] = []     # ("hdr", phase idx) | ("st", node idx)

    def set_stations(self, items: list[dict], badges: dict[str, str] | None = None) -> None:
        badges = badges or {}
        self.items = [dict(i, badge=badges.get(i["token"], ""),
                           warn=(i["status"] == "success" and (i.get("checklist") == "attention"
                                                               or bool(badges.get(i["token"]))))) for i in items]
        if not self.sel or self.sel not in [i["token"] for i in self.items]:
            self.sel = self._first_interesting()
        self.redraw()

    def _first_interesting(self) -> str:
        for i in self.items:
            if i["status"] in ("failed", "running"):
                return i["token"]
        for i in self.items:
            if D.canonical_token(i["token"]) == "placement":
                return i["token"]
        return self.items[0]["token"] if self.items else ""

    # -- model --
    def _build(self) -> None:
        by_canon = {D.canonical_token(i["token"]): i for i in self.items}
        sel_canon = D.canonical_token(self.sel)
        nodes: list[Station] = []
        skip: set[str] = set()
        for it in self.items:
            if it["token"] in skip:
                continue
            grp = next((g for g in D.COLLAPSE if D.canonical_token(it["token"]) == g[0] and all(t in by_canon for t in g)), None)
            if grp:
                members = [by_canon[t] for t in grp]
                if all(m["status"] == "success" for m in members) and sel_canon not in grp \
                        and not any(m.get("warn") for m in members):
                    nodes.append(Station(members, D.COLLAPSE_LABEL[grp]))
                    skip.update(m["token"] for m in members)
                    continue
            nodes.append(Station([it]))
        self._stn = nodes
        rows: list[tuple[str, int]] = []
        last_phase = None
        for ni, n in enumerate(nodes):
            if n.phase != last_phase:
                rows.append(("hdr", D.PHASES.index(n.phase) if n.phase in D.PHASES else 0))
                last_phase = n.phase
            rows.append(("st", ni))
        self._rows = rows

    def sel_node(self) -> int:
        for i, n in enumerate(self._stn):
            if self.sel in n.tokens:
                return i
        return 0

    def redraw(self) -> None:
        self._build()
        w = max(20, self.size.width or 30)
        slowest = max((n.secs for n in self._stn), default=0)
        lines = Text(no_wrap=True, overflow="crop")
        sel_row = 0
        running_row = -1
        for ri, (kind, v) in enumerate(self._rows):
            if kind == "st" and self._stn[v].status == "running":
                running_row = ri
        first_phase = True
        for ri, (kind, v) in enumerate(self._rows):
            if ri:
                lines.append("\n")
            if kind == "hdr":
                name = D.PHASES[v]
                col = PHASE_COLORS.get(name, ORANGE)
                lines.append("┏━ " if first_phase else "┣━ ", style=f"bold {col}")
                lines.append(name, style=f"bold {col}")
                first_phase = False
                continue
            n = self._stn[v]
            col = PHASE_COLORS.get(n.phase, ORANGE)
            selected = self.sel in n.tokens
            if selected:
                sel_row = ri
            status = n.status
            glyph, gcol = GLYPH.get(status, GLYPH["pending"])
            if status == "running":
                on = (self.tick // 2) % 2 == 0
                glyph = "◉" if on else "○"
                gcol = ORANGE if on else lerp_color(ORANGE, "#5A3A20", 0.6)
            if status == "failed" and self.flash and self.flash_token in n.tokens:
                gcol = "#FFFFFF" if self.flash % 2 else BAD
            bg = ""
            if selected:
                bg = " on #5A2E0C" if not self.has_focus else f" on {ORANGE}"
            if status == "failed" and self.flash and self.flash % 2 and self.flash_token in n.tokens:
                bg = f" on {BAD}"
            fg_sel = BG if (selected and self.has_focus) else None
            line_ch = "┃"
            line_col = col
            if running_row >= 0 and ri == running_row + 1 and (self.tick // 2) % 2 == 0:
                line_ch, line_col = "▼", "#FFF3E0"
            if ri == len(self._rows) - 1:
                line_ch = "╹" if line_ch == "┃" else line_ch
            lines.append(line_ch + " ", style=f"bold {line_col}")
            lines.append(glyph, style=f"bold {fg_sel or gcol}{bg}")
            lines.append("  ", style=bg.strip())
            lab_style = f"bold {fg_sel or FG}" if selected else (DIM if status == "pending" else FG)
            if status == "skipped":
                lab_style = f"strike {DIM}"
            right = ""
            right_style = DIM
            if n.badge:
                right, right_style = n.badge, WARN
            elif n.secs:
                right = ecc.fmt_secs(n.secs)
                if status == "running":
                    right_style = ORANGE
            hot = " ▲" if (slowest > 120 and n.secs == slowest) else "  "
            avail = w - 2 - 1 - 2 - 2   # gutter, glyph, gap, hot marker
            lab = n.label
            if len(lab) + len(right) + 1 > avail:
                lab = lab[: max(3, avail - len(right) - 2)] + "…"
            pad = max(1, avail - len(lab) - len(right))
            base = bg.strip()
            lines.append(lab, style=(lab_style + " " + base).strip())
            lines.append(" " * pad, style=base)
            lines.append(right, style=((f"bold {BG}" if (selected and self.has_focus) else right_style) + " " + base).strip())
            lines.append(hot, style=(f"bold {WARN}" if hot.strip() else "") + " " + base)
        self.update(lines)
        self._sel_row = sel_row
        if self.parent is not None and hasattr(self.parent, "scroll_to_region"):
            self.call_after_refresh(self._scroll_sel)

    def _scroll_sel(self) -> None:
        try:
            self.parent.scroll_to_region(Region(0, self._sel_row, 1, 1), animate=False, spacing=Spacing(2, 0, 2, 0))
        except Exception:
            pass

    def on_resize(self, _e: events.Resize) -> None:
        if self.items:
            self.redraw()

    def watch_has_focus(self, _v: bool) -> None:
        if self.items:
            self.redraw()

    # -- interaction --
    def _move(self, d: int) -> None:
        if not self._stn:
            return
        i = self.sel_node() + d
        i = max(0, min(len(self._stn) - 1, i))
        n = self._stn[i]
        tok = n.tokens[-1] if d < 0 else n.tokens[0]
        self.select(tok)

    def select(self, token: str, open_: bool = False) -> None:
        if token != self.sel:
            self.sel = token
            self.redraw()
        self.post_message(self.Selected(token, open_))

    def key_up(self) -> None:
        self._move(-1)

    def key_down(self) -> None:
        self._move(1)

    def key_enter(self) -> None:
        self.post_message(self.Selected(self.sel, True))

    def key_home(self) -> None:
        self.select(self.items[0]["token"])

    def key_end(self) -> None:
        self.select(self.items[-1]["token"])

    def on_click(self, event: events.Click) -> None:
        self.focus()
        y = event.y
        if 0 <= y < len(self._rows):
            kind, v = self._rows[y]
            if kind == "st":
                n = self._stn[v]
                self.select(n.tokens[0], open_=event.chain >= 2)

    def advance(self) -> None:
        self.tick += 1
        if self.flash:
            self.flash -= 1
        self.redraw()


# -------------------------------------------------------------- health panel --

DIGITS = {
    "0": ("█▀█", "█ █", "▀▀▀"), "1": ("▄█ ", " █ ", "▄█▄"), "2": ("▀▀█", "█▀▀", "▀▀▀"),
    "3": ("▀▀█", " ▀█", "▀▀▀"), "4": ("█ █", "▀▀█", "  ▀"), "5": ("█▀▀", "▀▀█", "▀▀▀"),
    "6": ("█▀▀", "█▀█", "▀▀▀"), "7": ("▀▀█", "  █", "  ▀"), "8": ("█▀█", "█▀█", "▀▀▀"),
    "9": ("█▀█", "▀▀█", "▀▀▀"), ".": (" ", " ", "▄"), "-": ("   ", "▀▀▀", "   "),
}


def big_number(s: str) -> list[str]:
    rows = ["", "", ""]
    for ch in s:
        g = DIGITS.get(ch, DIGITS["-"])
        for i in range(3):
            rows[i] += g[i] + (" " if ch != "." else "")
    return [r.rstrip() for r in rows]


class ScoreBadge(Static):
    DEFAULT_CSS = "ScoreBadge { height: 7; }"

    def show(self, score: float | None, band: str | None, width: int) -> None:
        col = band_color(band)
        inner = max(12, width - 2)
        t = Text(no_wrap=True)
        t.append("╭" + "─" * inner + "╮\n", style=col)
        digits = big_number(f"{score:.1f}" if score is not None else "--")
        t.append("│" + " " * inner + "│\n", style=col)
        for r in digits:
            t.append("│", style=col)
            t.append(r.center(inner), style=f"bold {col}")
            t.append("│\n", style=col)
        t.append("│", style=col)
        t.append(f"◆ {(band or 'NOT RATED').replace('_', ' ')}".center(inner), style=f"bold {col}")
        t.append("│\n", style=col)
        t.append("╰" + "─" * inner + "╯", style=col)
        self.update(t)


class BarRow(Static):
    DEFAULT_CSS = "BarRow { height: 1; }"
    EIGHTHS = " ▏▎▍▌▋▊▉█"

    def show(self, name: str, value: float | None, width: int, state: str = "") -> None:
        t = Text(no_wrap=True)
        t.append(name.ljust(13), style=SOFT)
        bw = max(6, width - 13 - 5)
        if value is None:
            t.append("─ no budget" if name.startswith("power") else "─ unknown", style=DIM)
        else:
            col = gradient_at([BAD, WARN, OK], (value - 40) / 55)
            cells = value / 100 * bw
            full = int(cells)
            part = int((cells - full) * 8)
            t.append("█" * full, style=col)
            used = full
            if part and full < bw:
                t.append(self.EIGHTHS[part], style=f"{col} on #2E2623")
                used += 1
            t.append(" " * (bw - used), style="on #2E2623")
            t.append(f"{value:4.0f}", style=f"bold {col}")
        self.update(t)


LEVEL = {"ok": ("●", OK), "warn": ("●", WARN), "bad": ("●", BAD), "na": ("○", DIM)}


class HealthPanel(Vertical):
    DEFAULT_CSS = """
    HealthPanel { height: auto; }
    HealthPanel #sign { height: auto; }
    HealthPanel #insight { height: auto; margin-top: 1; }
    HealthPanel .sec { height: 1; margin-top: 1; color: #8C8079; }
    """
    DIMS = ["timing", "interconnect", "area", "power", "robustness"]

    def compose(self) -> ComposeResult:
        yield ScoreBadge(id="badge")
        yield Static("", classes="sec", id="sec-dims")
        for d in self.DIMS:
            yield BarRow(id=f"bar-{d}")
        yield Static("", classes="sec", id="sec-sign")
        yield Static("", id="sign")
        yield Static("", id="insight")
        yield Static("", classes="sec", id="sec-why")
        yield Static("", id="why")

    def show(self, data: dict | None) -> None:
        w = max(20, self.size.width or 28)
        if not data:
            return
        qor = data.get("qor") or {}
        self.query_one("#badge", ScoreBadge).show(data.get("score"), data.get("band"), w)
        sec = lambda txt: Text(txt + " " + "─" * max(0, w - len(txt) - 1), style=f"bold {LINE}")
        self.query_one("#sec-dims", Static).update(sec("QOR"))
        dims = {d["dimension"]: d for d in qor.get("dimensions", [])}
        for d in self.DIMS:
            self.query_one(f"#bar-{d}", BarRow).show(d, dims.get(d, {}).get("score"), w)
        self.query_one("#sec-sign", Static).update(sec("SIGNOFF"))
        t = Text(no_wrap=True)
        for i, (key, label, lvl, val, detail) in enumerate(data.get("health", [])):
            g, c = LEVEL[lvl]
            if i:
                t.append("\n")
            t.append(f"{g} ", style=c)
            t.append(label.ljust(8), style=FG)
            t.append(val, style=f"bold {c}" if lvl != "ok" else FG)
            if detail and len(label) + len(val) + len(detail) + 5 < w:
                t.append(f" {detail}", style=c)
        self.query_one("#sign", Static).update(t)
        ins = Text()
        ins.append("› ", style=f"bold {ORANGE}")
        ins.append(data.get("insight", ""), style=f"italic {SOFT}")
        self.query_one("#insight", Static).update(ins)
        tall = self.app.size.height >= 44 and bool(qor)
        self.query_one("#sec-why").display = tall
        self.query_one("#why").display = tall
        if tall:
            self.query_one("#sec-why", Static).update(sec("DIAGNOSES"))
            wt = Text()
            for dg in (qor.get("qor", {}).get("diagnoses") or [])[:3]:
                sev = dg.get("severity") or 0
                wt.append("▌", style=gradient_at([OK, WARN, BAD], min(1.0, sev * 2.5)))
                wt.append(" " + dg.get("diagnosis_id", "").replace("diag.", "") + "\n", style=f"bold {SOFT}")
                wt.append("  " + (dg.get("interpretation") or "")[:90] + "\n", style=DIM)
            self.query_one("#why", Static).update(wt if wt.plain else Text("no diagnoses raised", style=DIM))


# ----------------------------------------------------------------- chip card --

class ChipCard(Widget, can_focus=True):
    """Home screen card: mini die thumbnail + score + status."""

    DEFAULT_CSS = "ChipCard { height: 8; }"

    class Opened(Message):
        def __init__(self, card: "ChipCard") -> None:
            super().__init__()
            self.card = card

    def __init__(self, name: str = "", project: str | None = None, new: bool = False, **kw) -> None:
        super().__init__(**kw)
        self.chip_name = name
        self.project = project
        self.new = new
        self.data: dict | None = None
        self.summary: dict | None = None
        self.grid: list[list[float]] | None = None
        self.running = False
        self.tick = 0
        self.border_title = "+ new chip" if new else name
        self._thumb_cache: tuple | None = None

    def set_data(self, data: dict, grid: list[list[float]] | None) -> None:
        self.data, self.grid = data, grid
        self.summary = D.card_summary(data)
        self._thumb_cache = None
        self.refresh()

    @property
    def sort_keys(self) -> dict:
        d = self.data or {}
        return {"score": d.get("score") if d.get("score") is not None else -1,
                "name": self.chip_name.lower(), "runtime": d.get("total_secs", 0),
                "status": {"running": 0, "failed": 1}.get(d.get("status", ""), 2)}

    def render_line(self, y: int) -> Strip:
        W = self.size.width
        lines = self._lines(W)
        bgc = "#2E241F" if self.has_focus else SURFACE
        bg = Style(bgcolor=Color.parse(bgc))
        if y < len(lines):
            return text_strip(lines[y], W, self.app, bgc).apply_style(bg)
        return Strip.blank(W, bg)

    def _lines(self, W: int) -> list[Text]:
        H = max(1, self.size.height)
        if self.new:
            out = [Text("") for _ in range(H)]
            mid = max(0, H // 2 - 2)
            for i, (txt, st) in enumerate((("✚", f"bold {ORANGE}"), ("", ""), ("init a new", DIM), ("project", DIM))):
                if mid + i < H:
                    out[mid + i] = Text(txt.center(W), style=st)
            return out
        if self.data is None:
            ph = "░▒▓▒"
            out = [Text("".join(ph[(x + self.tick + r) % 4] for x in range(W)), style="#2A2220") for r in range(3)]
            out.append(Text("loading…", style=DIM))
            return out
        d = self.data
        w = max(8, W)
        rows = max(2, H - 2)
        key = (w, rows, id(self.grid))
        if self._thumb_cache is None or self._thumb_cache[0] != key:
            self._thumb_cache = (key, thumb_text(self.grid, w, rows))
        lines = list(self._thumb_cache[1])
        score, band = d.get("score"), d.get("band")
        l1 = Text(no_wrap=True)
        if score is not None:
            col = band_color(band)
            l1.append(f"{score:.1f}", style=f"bold {col}")
            l1.append(f" ◆ {str(band).replace('_', ' ')}", style=f"bold {col}")
        else:
            l1.append("— not rated", style=DIM)
        lines.append(l1)
        s = self.summary or {}
        l2 = Text(no_wrap=True, overflow="ellipsis")
        status = d.get("status", "")
        if self.running or status == "running":
            ch = "▶" if self.tick % 2 == 0 else "▷"
            l2.append(f"{ch} running {s.get('done', 0)}/{s.get('total', 0)}", style=f"bold {ORANGE}")
        elif status == "failed":
            l2.append(f"✗ failed {s.get('done', 0)}/{s.get('total', 0)}", style=f"bold {BAD}")
        elif s.get("done") == s.get("total") and s.get("total"):
            l2.append(f"✓ {s['done']}/{s['total']}", style=OK)
        else:
            l2.append(f"◐ {s.get('done', 0)}/{s.get('total', 0)}", style=WARN)
        if s.get("drv"):
            l2.append(f" !{s['drv']} DRV", style=WARN)
        if s.get("secs"):
            dur = f" · {_short_dur(s['secs'])}"
            if l2.cell_len + len(dur) <= w:
                l2.append(dur, style=DIM)
        lines.append(l2)
        return lines

    def on_focus(self) -> None:
        self.refresh()

    def on_click(self, _e: events.Click) -> None:
        self.focus()
        self.post_message(self.Opened(self))

    def key_enter(self) -> None:
        self.post_message(self.Opened(self))


def _short_dur(sec: float) -> str:
    sec = int(sec)
    if sec >= 3600:
        return f"{sec // 3600}h{sec % 3600 // 60:02d}"
    if sec >= 60:
        return f"{sec // 60}m"
    return f"{sec}s"


# ---------------------------------------------------------------------- logo --

LOGO = [
    "███████╗ ██████╗ ██████╗ ███████╗",
    "██╔════╝██╔════╝██╔═══██╗██╔════╝",
    "█████╗  ██║     ██║   ██║███████╗",
    "██╔══╝  ██║     ██║   ██║╚════██║",
    "███████╗╚██████╗╚██████╔╝███████║",
    "╚══════╝ ╚═════╝ ╚═════╝ ╚══════╝",
]


def logo_text() -> Text:
    t = Text(no_wrap=True)
    for i, ln in enumerate(LOGO):
        if i:
            t.append("\n")
        t.append(ln, style=f"bold {LOGO_GRADIENT[i % len(LOGO_GRADIENT)]}")
    return t


# ------------------------------------------------------------------ confetti --

class Confetti(ModalScreen):
    """Falling ✦ ✧ • ◆ particles over everything for ~2 s.

    A transparent modal screen: the screen below stays visible, each particle is a
    1x1 widget (a custom render_line overlay cannot be transparent in Textual).
    """

    DEFAULT_CSS = """
    Confetti { background: transparent; align: left top; }
    Confetti > .spark { width: 1; height: 1; background: transparent; }
    """
    CHARS = "✦✧•◆✦"
    COLORS = ["#EF6C00", "#FF8F00", "#FFB74D", "#FFF3E0", "#FFCC80", "#D84315"]

    def __init__(self, secs: float = 2.0) -> None:
        super().__init__()
        self.secs = secs
        self.parts: list[list] = []
        self.widgets: list[Static] = []

    def compose(self) -> ComposeResult:
        W, H = self.app.size.width, self.app.size.height
        n = max(40, min(90, W // 2))
        for _ in range(n):
            ch, col = random.choice(self.CHARS), random.choice(self.COLORS)
            self.parts.append([random.uniform(0, W - 1), random.uniform(-H * 0.8, 0), random.uniform(9, 22),
                               random.uniform(-3, 3)])
            yield Static(Text(ch, style=f"bold {col}"), classes="spark")

    def on_mount(self) -> None:
        self.widgets = list(self.query(".spark"))
        self.t0 = self._last = time.time()
        self.set_interval(1 / 20, self._step)
        self.set_timer(self.secs, self._done)
        self._step()

    def _step(self) -> None:
        now = time.time()
        dt, self._last = now - self._last, now
        W, H = self.size.width, self.size.height
        for w, p in zip(self.widgets, self.parts):
            p[1] += p[2] * dt
            p[0] += p[3] * dt
            x, y = int(p[0]), int(p[1])
            if 0 <= x < W and 0 <= y < H:
                w.display = True
                w.styles.offset = (x, y)
            else:
                w.display = False

    def _done(self) -> None:
        if self.is_attached and self.app.screen is self:
            self.dismiss()

    def on_key(self, _e) -> None:
        self._done()

    def on_click(self, _e) -> None:
        self._done()
