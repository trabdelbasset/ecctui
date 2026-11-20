"""ECOS orange brand theme and shared colour/glyph helpers."""
from __future__ import annotations

from textual.theme import Theme

ECOS_THEME = Theme(
    name="ecos",
    primary="#EF6C00",
    secondary="#FF8F00",
    accent="#FFB74D",
    background="#121011",
    surface="#1B1716",
    panel="#241E1B",
    foreground="#F3ECE6",
    success="#8BC34A",
    warning="#FFB300",
    error="#EF5350",
    dark=True,
)

ORANGE = "#EF6C00"
AMBER = "#FF8F00"
SOFT = "#FFB74D"
FG = "#F3ECE6"
DIM = "#8A7F78"
GREY = "#7A716B"

BAND_COLORS = {
    "GREEN": "#8BC34A",
    "YELLOW": "#FFD54F",
    "ORANGE": "#FF9800",
    "RED": "#EF5350",
    "FAIL": "#B71C1C",
    "NOT_RATED": "#9E9E9E",
}

# status text -> (glyph, colour)
STATUS_STYLE = {
    "success": ("✓", "#8BC34A"),
    "passed": ("✓", "#8BC34A"),
    "pass": ("✓", "#8BC34A"),
    "ok": ("✓", "#8BC34A"),
    "clean": ("✓", "#8BC34A"),
    "available": ("✓", "#8BC34A"),
    "running": ("▶", "#EF6C00"),
    "failed": ("✗", "#EF5350"),
    "fail": ("✗", "#EF5350"),
    "error": ("✗", "#EF5350"),
    "blocked": ("✗", "#EF5350"),
    "partial": ("◐", "#FFD54F"),
    "attention": ("⚠", "#FFB300"),
    "watch": ("⚠", "#FFB300"),
    "warn": ("⚠", "#FFB300"),
    "warning": ("⚠", "#FFB300"),
    "skip": ("○", "#7A716B"),
    "skipped": ("○", "#7A716B"),
    "pending": ("○", "#7A716B"),
    "not_started": ("○", "#7A716B"),
    "missing": ("○", "#7A716B"),
    "unknown": ("?", "#7A716B"),
    "unavailable": ("?", "#7A716B"),
}

BARS = "▁▂▃▄▅▆▇█"


def status_style(status: object) -> tuple[str, str]:
    return STATUS_STYLE.get(str(status).lower(), ("●", "#9E9E9E"))


def band_color(band: object) -> str:
    return BAND_COLORS.get(str(band).upper(), BAND_COLORS["NOT_RATED"])


# ---------------------------------------------------------------- gradients --
BG = "#121011"
SURFACE = "#1B1716"
PANEL = "#241E1B"
LINE = "#3A302B"          # dim borders
DEEP = "#D84315"          # deep orange (route & signoff line)
OK = "#8BC34A"
WARN = "#FFB300"
BAD = "#EF5350"

HEAT = ["#121011", "#3A1F0F", "#7A3306", "#C25500", "#EF6C00", "#FFB74D", "#FFF3E0"]
COOL = ["#121011", "#1E3A5F", "#3D7DCA", "#FFB74D", "#EF5350"]
LOGO_GRADIENT = ["#EF6C00", "#F57C00", "#FF8F00", "#FFA000", "#FFB74D", "#FFCC80"]

# phase -> line colour
PHASE_COLORS = {"SYNTH & FLOORPLAN": "#FFB74D", "PLACE & CLOCK": "#EF6C00",
                "ROUTE & SIGNOFF": "#D84315", "ANALYSIS": "#FFA000"}


def _hex(c: str) -> tuple[int, int, int]:
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)


def lerp_color(c1: str, c2: str, t: float) -> str:
    a, b = _hex(c1), _hex(c2)
    t = max(0.0, min(1.0, t))
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def gradient_at(stops: list[str], t: float) -> str:
    t = max(0.0, min(1.0, t))
    if len(stops) == 1:
        return stops[0]
    x = t * (len(stops) - 1)
    i = min(int(x), len(stops) - 2)
    return lerp_color(stops[i], stops[i + 1], x - i)


def build_lut(stops: list[str], n: int = 256) -> list[str]:
    return [gradient_at(stops, i / (n - 1)) for i in range(n)]
