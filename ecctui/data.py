"""Data loaders for ecctui: flow model, DreamPlace maps, STA matrix, health checks.

Everything here is plain Python (no Textual) and safe to call from worker
threads.  File reads are tolerant: a missing artifact yields None / empty data.
"""
from __future__ import annotations

import csv
import glob
import gzip
import json
import math
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import ecc

# ----------------------------------------------------------------- flow model --

# status token, flow id (for --from/--only), short label, phase
STATIONS: list[tuple[str, str, str, str]] = [
    ("synthesis", "Synthesis", "synthesis", "SYNTH & FLOORPLAN"),
    ("pre_floorplan", "preFloorplan", "floorplan", "SYNTH & FLOORPLAN"),
    ("macro_placement", "macroPlacement", "macros", "SYNTH & FLOORPLAN"),
    ("post_floorplan", "postFloorplan", "post-floorplan", "SYNTH & FLOORPLAN"),
    ("preplace", "preplace", "preplace", "PLACE & CLOCK"),
    ("placement", "place", "place", "PLACE & CLOCK"),
    ("diff_sizing", "diff_sizing", "diff sizing", "PLACE & CLOCK"),
    ("cts", "CTS", "cts", "PLACE & CLOCK"),
    ("legalization", "legalization", "legalize", "PLACE & CLOCK"),
    ("timing_optimization", "Timing optimization", "timing opt", "PLACE & CLOCK"),
    ("routing", "route", "route", "ROUTE & SIGNOFF"),
    ("filler", "filler", "filler", "ROUTE & SIGNOFF"),
    ("lvs", "lvs", "lvs", "ROUTE & SIGNOFF"),
    ("drc", "drc", "drc", "ROUTE & SIGNOFF"),
    ("antenna_check", "antennaCheck", "antenna", "ROUTE & SIGNOFF"),
    ("post_route_lec", "postRouteLec", "post-route LEC", "ROUTE & SIGNOFF"),
    ("rcx", "RCX", "rcx", "ANALYSIS"),
    ("sta", "sta", "sta", "ANALYSIS"),
    ("power_analysis", "powerAnalysis", "power", "ANALYSIS"),
    ("emir", "emir", "ir", "ANALYSIS"),
    ("harden", "Harden", "harden", "ANALYSIS"),
]
_BY_TOKEN = {s[0]: s for s in STATIONS}
PHASES = ["SYNTH & FLOORPLAN", "PLACE & CLOCK", "ROUTE & SIGNOFF", "ANALYSIS"]
# runs of tiny steps shown as one row while they are all successful
COLLAPSE = [("filler", "lvs", "drc"), ("rcx", "sta", "power_analysis", "emir")]
COLLAPSE_LABEL = {COLLAPSE[0]: "filler · lvs · drc", COLLAPSE[1]: "rcx · sta · power · ir"}


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


_BY_NORM: dict[str, tuple[str, str, str, str]] = {norm(s[0]): s for s in STATIONS}
_BY_NORM["place"] = _BY_TOKEN["placement"]
_BY_NORM["route"] = _BY_TOKEN["routing"]


def canonical_token(token: str) -> str:
    st = _BY_NORM.get(norm(token))
    return st[0] if st else token


def label_for(token: str) -> str:
    st = _BY_NORM.get(norm(token))
    return st[2] if st else token.replace("_", " ")


def phase_for(token: str) -> str:
    st = _BY_NORM.get(norm(token))
    return st[3] if st else "ANALYSIS"


def flow_id_for(token: str, flow_ids: list[str] | None = None) -> str:
    """Resolve the id accepted by `ecc run --from/--only` for a status token."""
    canon = canonical_token(token)
    st = _BY_TOKEN.get(canon)
    want_norms = {norm(token), norm(canon)}
    if st:
        want_norms.add(norm(st[1]))
    if canon == "placement":
        want_norms.update({"place", "placement"})
    elif canon == "routing":
        want_norms.update({"route", "routing"})
    for f in flow_ids or []:
        if norm(f) in want_norms:
            return f
    return canon


def parse_runtime(s: Any) -> float:
    return ecc.hms_to_seconds(s)


def station_defs(steps: list[dict]) -> list[dict]:
    """Merge the (possibly empty) `ecc status` step list with the default flow."""
    by_canon = {canonical_token(str(s.get("step", ""))): s for s in steps}
    default = [t[0] for t in STATIONS]
    if not steps or all(canonical_token(str(s.get("step", ""))) in _BY_TOKEN for s in steps):
        ordered = [(by_canon[tok].get("step") if tok in by_canon else tok, tok) for tok in default]
    else:
        ordered = [(str(s.get("step")), canonical_token(str(s.get("step", "")))) for s in steps]
    out = []
    for raw_tok, canon in ordered:
        s = by_canon.get(canon, {})
        out.append({
            "token": raw_tok, "label": label_for(canon), "phase": phase_for(canon),
            "status": s.get("status") or "pending",
            "secs": parse_runtime(s.get("runtime")) if s.get("runtime") else 0.0,
            "checklist": s.get("checklist_status", ""),
            "tool": s.get("tool", ""),
        })
    return out


# ------------------------------------------------------------- workspace dirs --

_TOOLS = ("_kepler_formal", "_dreamplace", "_yosys", "_sizer", "_ecc")


def _strip_step_name(dirname: str) -> str | None:
    base = dirname
    for suf in _TOOLS:
        if base.endswith(suf):
            base = base[: -len(suf)]
            break
    else:
        return None
    return re.sub(r"^\d+_", "", base)


def step_dir(ws: Path, token: str) -> Path | None:
    canon = canonical_token(token)
    try:
        flow_file = ws / "home" / "flow.json"
        if flow_file.is_file():
            flow = json.loads(flow_file.read_text())
            for s in flow.get("steps", []) if isinstance(flow, dict) else []:
                if not isinstance(s, dict) or not s.get("directory"):
                    continue
                if canonical_token(str(s.get("name", ""))) == canon:
                    cand = ws / str(s["directory"])
                    if cand.is_dir():
                        return cand
    except Exception:
        pass
    try:
        want = {norm(token), norm(canon)}
        for d in sorted(ws.iterdir()):
            if not d.is_dir():
                continue
            base = _strip_step_name(d.name)
            if base is None:
                continue
            if canonical_token(base) == canon or norm(base) in want:
                return d
    except OSError:
        pass
    return None


def log_tail(ws: Path, token: str, n: int = 4) -> list[str]:
    d = step_dir(ws, token)
    if not d:
        return []
    logs = sorted(glob.glob(str(d / "log" / "*.log")))
    if not logs:
        return []
    try:
        with open(logs[0], "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 6000))
            txt = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = [re.sub(r"^\[ECC \S+ \S+ \S+ ", "[", ecc.strip_ansi(x).rstrip()) for x in txt.splitlines() if x.strip()]
    return lines[-n:]


def _fmt_num(v: float, unit: str = "") -> str:
    if isinstance(v, float) and not v.is_integer():
        s = f"{v:,.2f}" if abs(v) < 1000 else f"{v:,.0f}"
    else:
        s = f"{int(v):,}"
    s = s.replace(",", " ")
    u = {"um": "µm", "um^2": "µm²", "ratio": "", "count": "", "s": "s", "MB": "MB"}.get(unit, unit)
    return f"{s} {u}".strip()


_PICKS: dict[str, list[tuple[str, str]]] = {
    "synthesis": [("synthesis_cell_count", "cells"), ("synthesis_cell_area", "area"), ("synthesis_port_count", "ports")],
    "placement": [("place_hpwl", "HPWL"), ("place_congestion_egr_overflow_total", "overflow"), ("std_cell_count", "cells")],
    "cts": [("cts_buffer_count", "clk buffers"), ("cts_clock_tree_max_level", "levels"), ("clock_count", "clock cells")],
    "power_analysis": [("power_analysis_dynamic_uw", "dynamic µW"), ("power_analysis_leakage_uw", "leakage µW")],
    "harden": [("harden_artifact_missing_count", "missing artifacts"), ("harden_gds_exists", "GDS")],
}
_GENERIC = [("instance_count", "instances"), ("core_utilization", "util"), ("die_area", "die")]


def step_metrics(ws: Path, token: str, n: int = 3) -> list[tuple[str, str]]:
    d = step_dir(ws, token)
    if not d:
        return []
    try:
        ms = json.loads((d / "analysis" / "qor_metrics.json").read_text()).get("metrics", [])
    except Exception:
        return []
    byid = {m.get("id"): m for m in ms}
    out: list[tuple[str, str]] = []
    for mid, lab in _PICKS.get(canonical_token(token), []) + _GENERIC:
        m = byid.get(mid)
        if m is None or m.get("value") is None:
            continue
        v = m["value"]
        if mid == "harden_gds_exists":
            txt = "yes" if v else "missing"
        elif mid == "core_utilization":
            txt = f"{float(v) * 100:.0f} %"
        else:
            txt = _fmt_num(float(v), m.get("unit", ""))
        out.append((lab, txt))
        if len(out) >= n:
            break
    return out


# ------------------------------------------------------------------ die + maps --

def die_size(ws: Path) -> tuple[float, float] | None:
    pref = [d for tok in ("placement", "legalization", "routing") if (d := step_dir(ws, tok)) is not None]
    cands = [d / "analysis" / "qor_metrics.json" for d in pref]
    cands += [Path(p) for p in sorted(glob.glob(str(ws / "*" / "analysis" / "qor_metrics.json")))]
    for f in cands:
        try:
            ms = {m["id"]: m["value"] for m in json.loads(f.read_text()).get("metrics", [])}
            if ms.get("die_width") and ms.get("die_height"):
                return float(ms["die_width"]), float(ms["die_height"])
        except Exception:
            continue
    for f in glob.glob(str(ws / "*" / "output" / "*.def*")):
        try:
            op = gzip.open if f.endswith(".gz") else open
            with op(f, "rt", errors="replace") as fh:
                units = 1000.0
                for ln in fh:
                    if ln.startswith("UNITS DISTANCE"):
                        units = float(ln.split()[3])
                    m = re.match(r"DIEAREA \( (-?\d+) (-?\d+) \) \( (-?\d+) (-?\d+) \)", ln)
                    if m:
                        a = [int(x) for x in m.groups()]
                        return (a[2] - a[0]) / units, (a[3] - a[1]) / units
        except Exception:
            continue
    return None


@dataclass
class MapInfo:
    key: str            # density | rudy | overflow | margin
    label: str
    path: Path
    step: str           # directory name, e.g. place_dreamplace
    title: str


_MAP_SRC = [
    ("density", "density", "density_map", "allcell_density", "cell density"),
    ("rudy", "RUDY", "RUDY_map", "rudy_union", "RUDY routing demand"),
    ("overflow", "overflow", "egr_congestion_map", "egr_union_overflow", "EGR congestion overflow"),
    ("margin", "margin", "margin_map", "union_margin", "timing margin"),
]


def find_maps(ws: Path) -> list[MapInfo]:
    """Placement maps first, then the same four maps from later steps."""
    found: list[MapInfo] = []
    seen_dirs = []
    for sd in sorted(ws.glob("*/feature")):
        if any((sd / sub).is_dir() for _, _, sub, _, _ in _MAP_SRC):
            seen_dirs.append(sd.parent)
    prim = [d for d in seen_dirs if canonical_token(_strip_step_name(d.name) or d.name) == "placement"]
    rest = sorted((d for d in seen_dirs if d not in prim), key=lambda d: d.name.lower())
    for di, d in enumerate(prim + rest):
        short = (_strip_step_name(d.name) or d.name.rsplit("_", 1)[0]) if di else ""
        for key, lab, sub, stem, title in _MAP_SRC:
            hits = sorted(glob.glob(str(d / "feature" / sub / f"*{stem}.csv")))
            if hits:
                found.append(MapInfo(key, lab if not short else f"{lab}·{short}", Path(hits[0]), d.name,
                                     title if not short else f"{title} @ {short}"))
    return found


def read_grid(path: Path) -> list[list[float]]:
    rows = []
    with open(path, newline="") as f:
        for r in csv.reader(f):
            if r:
                rows.append([float(x) if x.strip() else 0.0 for x in r])
    return rows


def prepare_grid(grid: list[list[float]], key: str) -> tuple[list[list[float]], float, float]:
    """Return (normalised 0..1 grid with -1 for 'empty', lo, hi) using robust p2-p98."""
    flat = sorted(v for r in grid for v in r)
    if not flat:
        return [[-1.0]], 0.0, 0.0
    vals = flat
    sentinel = None
    if flat[0] == flat[-1]:     # no spread at all (e.g. margin map without constraints)
        return [[0.22 if key != "overflow" else 0.0 for _ in r] for r in grid], flat[0], flat[0]
    if key == "margin":
        top = flat[-1]
        if top > 1e5 and sum(1 for v in flat if v == top) >= 1:
            sentinel = top
            vals = [v for v in flat if v < top] or flat
    def pct(p: float) -> float:
        i = min(len(vals) - 1, max(0, int(round(p * (len(vals) - 1)))))
        return vals[i]
    lo, hi = pct(0.02), pct(0.98)
    if key in ("overflow",):
        lo = 0.0
    if hi <= lo:
        hi = vals[-1] if vals[-1] > lo else lo + 1.0
    out = []
    for r in grid:
        row = []
        for v in r:
            if sentinel is not None and v >= sentinel:
                row.append(-1.0)
                continue
            t = (v - lo) / (hi - lo)
            t = max(0.0, min(1.0, t))
            if key == "overflow" and v > 0:
                t = max(t, 0.4)                               # keep tiny overflow visible
            row.append(1.0 - t if key == "margin" else t)   # low margin == hot
        out.append(row)
    return out, lo, hi


def resample(norm_grid: list[list[float]], w: int, h: int, smooth: bool = True) -> list[list[float]]:
    """Resample to h rows x w cols. -1 (empty) stays empty."""
    gh, gw = len(norm_grid), len(norm_grid[0])
    out = []
    for y in range(h):
        fy = (y + 0.5) / h * gh - 0.5
        row = []
        for x in range(w):
            fx = (x + 0.5) / w * gw - 0.5
            if not smooth:
                v = norm_grid[min(gh - 1, max(0, int(fy + 0.5)))][min(gw - 1, max(0, int(fx + 0.5)))]
                row.append(v)
                continue
            x0, y0 = math.floor(fx), math.floor(fy)
            tx, ty = fx - x0, fy - y0
            acc = wsum = 0.0
            empty = True
            for dy, wy in ((0, 1 - ty), (1, ty)):
                for dx, wx in ((0, 1 - tx), (1, tx)):
                    gx, gy = min(gw - 1, max(0, x0 + dx)), min(gh - 1, max(0, y0 + dy))
                    v = norm_grid[gy][gx]
                    if v < 0:
                        continue
                    empty = False
                    acc += v * wx * wy
                    wsum += wx * wy
            row.append(-1.0 if empty or wsum == 0 else acc / wsum)
        out.append(row)
    return out


# ---------------------------------------------------------------------- STA --

CORNER_ORDER = ["MAX_125", "MIN_m40", "TYP_25", "ML_125", "WCL_m40"]
RC_ORDER = ["Cworst", "Cbest", "RCworst", "RCbest", "TYPICAL"]


def sta_matrix(ws: Path) -> dict:
    cells: dict[tuple[str, str], dict] = {}
    sta_dir = step_dir(ws, "sta")
    files = glob.glob(str(sta_dir / "feature" / "*" / "*" / "qor_summary.json")) if sta_dir else []
    for f in files:
        p = Path(f)
        corner, rc = p.parent.parent.name, p.parent.name
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        s = d.get("summary", {})
        ds = d.get("design_statistics", {}) or {}
        cells[(corner, rc)] = {
            "setup": s.get("setup", {}).get("wns"), "hold": s.get("hold", {}).get("wns"),
            "setup_tns": s.get("setup", {}).get("tns"), "hold_tns": s.get("hold", {}).get("tns"),
            "setup_nvp": s.get("setup", {}).get("nvp"), "hold_nvp": s.get("hold", {}).get("nvp"),
            "groups": d.get("path_groups", []),
            "drv": {"tran": ds.get("tran") or 0, "cap": ds.get("cap") or 0, "fanout": ds.get("fanout") or 0},
        }
    corners = sorted({c for c, _ in cells}, key=lambda c: (CORNER_ORDER.index(c) if c in CORNER_ORDER else 99, c))
    rcs = sorted({r for _, r in cells}, key=lambda r: (RC_ORDER.index(r) if r in RC_ORDER else 99, r))
    return {"cells": cells, "corners": corners, "rcs": rcs}


def worst_paths(ws: Path, corner: str, rc: str, n: int = 5) -> tuple[list[dict], str]:
    """Parse timing_max_reg2reg.rpt best-effort; returns (paths, note)."""
    sta_dir = step_dir(ws, "sta")
    if not sta_dir:
        return [], "no report"
    f = sta_dir / "report" / corner / rc / "timing_max_reg2reg.rpt"
    try:
        txt = f.read_text(errors="replace")
    except OSError:
        return [], "no report"
    if "No constrained paths" in txt:
        return [], "no violating reg2reg paths"
    paths = []
    cur: dict = {}
    for ln in txt.splitlines():
        m = re.match(r"\s*Startpoint:\s*(\S+)", ln)
        if m:
            cur = {"start": m.group(1)}
        m = re.match(r"\s*Endpoint:\s*(\S+)", ln)
        if m and cur:
            cur["end"] = m.group(1)
        m = re.search(r"slack.*?(-?\d+\.\d+)", ln)
        if m and cur and "slack" not in cur:
            cur["slack"] = float(m.group(1))
            paths.append(cur)
            cur = {}
    return paths[:n], f"{len(paths)} paths"


# ------------------------------------------------------------------- health --

def _metric(ws: Path, step: str, mid: str) -> float | None:
    try:
        d = step_dir(ws, step)
        for m in json.loads((d / "analysis" / "qor_metrics.json").read_text()).get("metrics", []):
            if m.get("id") == mid:
                return m.get("value")
    except Exception:
        pass
    return None


def antenna_info(ws: Path) -> dict | None:
    d = step_dir(ws, "antenna_check")
    if d is None or not (d / "feature").is_dir():
        return None
    fdir = d / "feature"
    cands = [fdir / "antenna_check.step.json", fdir / "antennaCheck.step.json"]
    cands += [p for p in sorted(fdir.glob("*.step.json")) if p not in cands]
    for p in cands:
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text())
        except Exception:
            continue
        if isinstance(data, dict) and "antenna_check" in data:
            return data.get("antenna_check") or {}
    return None


def _antenna_vacuous_reason(ant: dict | None) -> str | None:
    if ant is None:
        return None
    if int(ant.get("signal_net_count", 0) or 0) == 0:
        return "no nets were checked"
    if "pins_with_gate_area" in ant and int(ant.get("pins_with_gate_area") or 0) == 0:
        return "no pins had gate area"
    return None


def health(ws: Path, steps: list[dict], matrix: dict, qor: dict | None) -> dict:
    """Signoff rows incl. the truth checks ECC hides.

    Each row: (key, label, level ok|warn|bad|na, value text, detail)
    """
    rows: list[tuple[str, str, str, str, str]] = []
    gates = {}
    if qor:
        for g in (qor.get("qor", {}).get("feasibility", {}) or {}).get("gates", []):
            gates[g.get("id")] = g.get("state")

    def gate_row(key, label, gid, step, mid):
        n = _metric(ws, step, mid)
        st = gates.get(gid)
        if st not in ("passed", "failed") and n is None:
            st = None
        if n is None and st is None:
            rows.append((key, label, "na", "no data", ""))
            return
        n_i = int(n) if n is not None else (0 if st == "passed" else None)
        lvl = "ok" if (n_i == 0 or (n_i is None and st == "passed")) else "bad"
        rows.append((key, label, lvl, str(n_i if n_i is not None else st), ""))

    gate_row("drc", "DRC", "GATE_DRC", "drc", "drc_count")
    gate_row("lvs", "LVS", "GATE_LVS", "lvs", "lvs_count")

    cells = matrix.get("cells", {})
    setups = [c["setup"] for c in cells.values() if c["setup"] is not None]
    holds = [c["hold"] for c in cells.values() if c["hold"] is not None]
    if setups:
        ws_ = min(setups)
        rows.append(("setup", "setup", "bad" if ws_ < 0 else "ok", f"{ws_:+.2f} ns", ""))
    else:
        rows.append(("setup", "setup", "na", "no STA", ""))
    if holds:
        h_ps = min(holds) * 1000
        lvl = "bad" if h_ps < 0 else ("warn" if h_ps < 50 else "ok")
        rows.append(("hold", "hold", lvl, f"{h_ps:+.0f} ps", "thin" if lvl == "warn" else ""))
    else:
        rows.append(("hold", "hold", "na", "no STA", ""))
    if cells:
        drv = {k: max(c["drv"][k] for c in cells.values()) for k in ("tran", "cap", "fanout")}
        tot = sum(drv.values())
        rows.append(("drv", "DRV", "ok" if tot == 0 else "bad",
                     f"{drv['tran']}t {drv['cap']}c {drv['fanout']}f", f"{tot} violations" if tot else ""))
    else:
        rows.append(("drv", "DRV", "na", "no STA", ""))
    ant = antenna_info(ws)
    vac_reason = _antenna_vacuous_reason(ant)
    if ant is None:
        rows.append(("antenna", "antenna", "na", "not run", ""))
    elif vac_reason is not None:
        rows.append(("antenna", "antenna", "warn", "VACUOUS", vac_reason))
    elif ant.get("violation_count", 0):
        rows.append(("antenna", "antenna", "bad", f"{ant['violation_count']} viol", ""))
    else:
        rows.append(("antenna", "antenna", "ok", "clean", ""))
    lec = next((s for s in steps if canonical_token(str(s.get("token", ""))) == "post_route_lec"), None)
    if lec and lec["status"] == "success":
        rows.append(("lec", "LEC", "ok" if lec.get("checklist") in ("passed", "") else "warn", "equivalent", ""))
    elif lec and lec["status"] in ("failed", "error"):
        rows.append(("lec", "LEC", "bad", "mismatch", ""))
    else:
        rows.append(("lec", "LEC", "na", "not run", ""))
    return {"rows": rows}


def insight(qor: dict | None, hrows: list[tuple[str, str, str, str, str]]) -> str:
    if (qor is None or qor.get("overall_score") is None) and not any(r[2] in ("bad", "warn") for r in hrows):
        return "Nothing to judge yet: run the flow to see QoR and signoff."
    texts = {
        "drc": "DRC violations remain: the layout is not manufacturable yet.",
        "lvs": "LVS mismatches: layout and netlist disagree.",
        "setup": "Setup timing fails in at least one corner.",
        "hold": "Hold timing fails in at least one corner.",
        "drv": "Design-rule violations (slew/cap/fanout) were reported by STA.",
    }
    for r in hrows:
        if r[2] == "bad":
            return texts.get(r[0], f"{r[1]} is failing.")
    if qor:
        dims = [d for d in qor.get("dimensions", []) if d.get("score") is not None]
        if dims:
            w = min(dims, key=lambda d: d["score"])
            if w["score"] < 85:
                extra = ""
                for dg in qor.get("qor", {}).get("diagnoses", []) or []:
                    if w["dimension"] in (dg.get("affected_dimensions") or []):
                        extra = ""
                        break
                return f"{w['dimension'].capitalize()} is the weakest dimension at {w['score']:.0f}/100.{extra}"
    for r in hrows:
        if r[2] == "warn":
            if r[0] == "antenna":
                reason = r[4] or "no nets were checked"
                return f"Antenna check was VACUOUS: {reason}, so a clean result proves nothing."
            if r[0] == "hold":
                return "Hold margin is thin (< 50 ps): small variation could break it."
    return "All signoff checks look healthy."


# --------------------------------------------------------------- chip loading --

def project_workspace_id(project: Path) -> str | None:
    pj = ecc.read_project_json(project)
    ids = [w.get("workspace_id") for w in pj.get("workspaces", []) if isinstance(w, dict) and w.get("workspace_id")]
    if "default" in ids or (project / "default").is_dir():
        return "default"
    if ids:
        return ids[0]
    for d in sorted(project.iterdir()) if project.is_dir() else []:
        if d.is_dir() and (d / "home").is_dir():
            return d.name
    return None


def workspace_ids(project: Path) -> list[str]:
    pj = ecc.read_project_json(project)
    ids = [w.get("workspace_id") for w in pj.get("workspaces", []) if isinstance(w, dict) and w.get("workspace_id")]
    return ids or ["default"]


def load_chip(project: Path, wsid: str) -> dict:
    """All data a chip screen / card needs (blocking: call from a worker)."""
    ws = project / wsid
    rc, sdata, sraw, scl, _ = ecc.run_json(["status", "--project", str(project), "--workspace", wsid], echo=False)
    head, steps = ecc.records(sdata)
    steps = [s for s in steps if s.get("step")]
    qor = None
    if ws.is_dir():
        rc2, qdata, *_ = ecc.run_json(["report", "qor", "--project", str(project), "--workspace", wsid], echo=False)
        if isinstance(qdata, list) and qdata and isinstance(qdata[0], dict) and "overall_score" in qdata[0]:
            qor = qdata[0]
    stations = station_defs(steps)
    matrix = sta_matrix(ws) if ws.is_dir() else {"cells": {}, "corners": [], "rcs": []}
    hl = health(ws, stations, matrix, qor) if ws.is_dir() else {"rows": []}
    gates = []
    if qor:
        gates = (qor.get("qor", {}).get("feasibility", {}) or {}).get("gates", [])
    cfg = {}
    try:
        cfg = tomllib.loads((project / "ecc.toml").read_text())
    except Exception:
        pass
    return {
        "project": str(project), "name": project.name, "ws": wsid,
        "status": head.get("status") or ("not_started" if not steps else "unknown"),
        "stations": stations, "has_steps": bool(steps),
        "qor": qor, "gates": gates,
        "gates_pass": sum(1 for g in gates if g.get("state") == "passed"),
        "score": qor.get("overall_score") if qor else None,
        "band": qor.get("qor_status") if qor else None,
        "matrix": matrix, "health": hl["rows"],
        "insight": insight(qor, hl["rows"]),
        "die": die_size(ws) if ws.is_dir() else None,
        "maps": find_maps(ws) if ws.is_dir() else [],
        "total_secs": sum(s["secs"] for s in stations),
        "cfg": cfg,
        "raw_error": None if steps else sraw[:300],
    }


def card_summary(data: dict) -> dict:
    st = data["stations"]
    done = sum(1 for s in st if s["status"] == "success")
    tot = len(st)
    drv = 0
    for c in data["matrix"].get("cells", {}).values():
        drv = max(drv, sum(c["drv"].values()))
    vac = False
    try:
        wsdir = Path(data["project"]) / data.get("ws", "default")
        vac = _antenna_vacuous_reason(antenna_info(wsdir)) is not None
    except Exception:
        pass
    return {"done": done, "total": tot, "drv": drv, "secs": data["total_secs"], "antenna_vacuous": vac}
