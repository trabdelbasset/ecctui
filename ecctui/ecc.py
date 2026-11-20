"""Thin, defensive wrapper around the `ecc` CLI.

Every action in the TUI shells out through this module; nothing here knows EDA
logic.  Arguments are always passed as a list (never through a shell) because
the work tree contains spaces.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_ESC_OTHER = re.compile(r"\x1b[()][A-Za-z0-9]|\x1b[=>]|\r")

# ecc commands that do not accept --json (learned at runtime as well).
NO_JSON: set[str] = {
    "check",
    "init",
    "rtl import",
    "project set",
    "param set",
    "process inspect",
    "process cancel",
    "process reconcile",
}
_CMD_GROUPS = {"param", "flow", "macro", "pdk", "project", "process", "workspace",
               "signoff", "report", "compare", "reproducible", "sweep", "explore",
               "diagnose", "rtl"}

_sem = threading.BoundedSemaphore(3)  # at most 3 concurrent background ecc calls
_cache: dict[tuple, Any] = {}
_cache_lock = threading.Lock()

# set by the app: callback(cmdline, rc, seconds) used by the echo bar
on_command: Callable[[str, int, float], None] | None = None

DEFAULT_ECC = shutil.which("ecc") or str(Path.home() / ".local/bin/ecc")
_ecc_path = DEFAULT_ECC


def set_ecc(path: str | None) -> None:
    global _ecc_path
    if path:
        _ecc_path = path


def ecc_path() -> str:
    return _ecc_path


def strip_ansi(s: str) -> str:
    return _ESC_OTHER.sub("", ANSI_RE.sub("", s))


def cmdline(args: list[str]) -> str:
    return "ecc " + shlex.join(args)


def find_json(text: str) -> Any | None:
    """Find a JSON document in text that may be prefixed by warnings.

    Prefers a candidate that runs to the end of the output; otherwise the first
    candidate that parses.
    """
    dec = json.JSONDecoder()
    first: Any | None = None
    have_first = False
    for m in re.finditer(r"[\[{]", text):
        try:
            obj, end = dec.raw_decode(text, m.start())
        except ValueError:
            continue
        if not text[end:].strip():
            return obj
        if not have_first:
            first, have_first = obj, True
    return first if have_first else None


def _cmd_key(args: list[str]) -> str:
    if not args:
        return ""
    if args[0] in _CMD_GROUPS and len(args) > 1 and not args[1].startswith("-"):
        return f"{args[0]} {args[1]}"
    return args[0]


def _exec(args: list[str], timeout: float, cwd: str | None) -> tuple[int, str, float]:
    t0 = time.time()
    try:
        p = subprocess.run([_ecc_path, *args], capture_output=True, text=True,
                           timeout=timeout, cwd=cwd, errors="replace",
                           stdin=subprocess.DEVNULL)
        out = (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
        rc = p.returncode
    except subprocess.TimeoutExpired as e:
        out = f"[timeout after {timeout:.0f}s] {e.stdout or ''}"
        rc = 124
    except FileNotFoundError:
        out = f"ecc binary not found: {_ecc_path}"
        rc = 127
    except Exception as e:  # never crash the UI
        out = f"[{type(e).__name__}] {e}"
        rc = 1
    return rc, strip_ansi(out), time.time() - t0


def run_json(args: list[str], timeout: float = 600, cwd: str | None = None,
             echo: bool = True) -> tuple[int, Any | None, str, str, float]:
    """Run `ecc args --json`; returns (rc, data|None, raw_text, cmdline, seconds)."""
    args = list(args)
    key = _cmd_key(args)
    use_json = "--json" not in args and key not in NO_JSON
    full = args + (["--json"] if use_json else [])
    with _sem:
        rc, out, secs = _exec(full, timeout, cwd)
        if use_json and "No such option: --json" in out:
            NO_JSON.add(key)
            full = args
            rc, out, secs = _exec(full, timeout, cwd)
    data = find_json(out)
    cl = cmdline(full)
    if echo and on_command:
        try:
            on_command(cl, rc, secs)
        except Exception:
            pass
    return rc, data, out, cl, secs


def run_text(args: list[str], timeout: float = 600, cwd: str | None = None,
             echo: bool = True) -> tuple[int, str, str, float]:
    """Run without --json; returns (rc, text, cmdline, seconds)."""
    with _sem:
        rc, out, secs = _exec(list(args), timeout, cwd)
    cl = cmdline(list(args))
    if echo and on_command:
        try:
            on_command(cl, rc, secs)
        except Exception:
            pass
    return rc, out, cl, secs


def cached_json(args: list[str], force: bool = False, **kw):
    key = tuple(args)
    with _cache_lock:
        if not force and key in _cache:
            return _cache[key]
    res = run_json(args, **kw)
    with _cache_lock:
        _cache[key] = res
    return res


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def records(data: Any) -> tuple[dict, list[dict]]:
    """Split a `[header, item, ...]` list into (header, items). Tolerates dicts."""
    if isinstance(data, dict):
        return data, []
    if isinstance(data, list) and data:
        head = data[0] if isinstance(data[0], dict) else {}
        return head, [r for r in data[1:] if isinstance(r, dict)]
    return {}, []


def is_error(rc: int, data: Any) -> bool:
    if rc != 0:
        return True
    return isinstance(data, dict) and "error" in data


# ---------------------------------------------------------------- projects --

def find_projects(root: Path) -> list[Path]:
    """Dirs (depth <= 2) with ecc.toml, skipping fi_* scratch dirs.

    project.json is normally required, but a freshly copied project that has
    never run has no project.json yet, so only ecc.toml is mandatory.
    """
    out: list[Path] = []

    def ok(d: Path) -> bool:
        return d.is_dir() and not d.name.startswith("fi_") and (d / "ecc.toml").is_file()

    try:
        lvl1 = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return out
    for d in lvl1:
        if d.name.startswith("fi_"):
            continue
        if ok(d):
            out.append(d.resolve())
            continue
        try:
            for d2 in sorted(p for p in d.iterdir() if p.is_dir()):
                if ok(d2):
                    out.append(d2.resolve())
        except OSError:
            pass
    return out


def read_project_json(project: Path | str) -> dict:
    try:
        return json.loads((Path(project) / "project.json").read_text())
    except Exception:
        return {}


def project_workspaces(project: Path | str) -> list[dict]:
    """[{workspace_id, flow_status, qor}] from `report project`, else project.json."""
    rc, data, raw, cl, secs = run_json(["report", "project", "--project", str(project)])
    head, _ = records(data)
    ws = head.get("workspaces") if isinstance(head, dict) else None
    if isinstance(ws, list) and ws:
        return [w for w in ws if isinstance(w, dict) and w.get("workspace_id")]
    pj = read_project_json(project)
    res = [{"workspace_id": w.get("workspace_id"), "flow_status": w.get("status")}
           for w in pj.get("workspaces", []) if isinstance(w, dict)]
    return res


def runtime_run_id(project: Path | str, workspace: str) -> str | None:
    rp = read_project_json(project).get("runtime_processes") or {}
    ent = rp.get(workspace) or {}
    rid = ent.get("run_id") if isinstance(ent, dict) else None
    return rid or None


def hms_to_seconds(s: Any) -> float:
    try:
        parts = [int(x) for x in str(s).split(":")]
        while len(parts) < 3:
            parts.insert(0, 0)
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    except Exception:
        return 0.0


def fmt_secs(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


# ------------------------------------------------------------------- runs --

def taskset_prefix() -> list[str]:
    """CPU affinity for heavy `ecc run` launches: env ECCTUI_TASKSET="16-19" (or --taskset)."""
    cpus = os.environ.get("ECCTUI_TASKSET", "").strip()
    if cpus and shutil.which("taskset"):
        return ["taskset", "-c", cpus]
    return []


class RunHandle:
    """A streaming `ecc run` subprocess."""

    def __init__(self, args: list[str], cwd: str | None, prefix: list[str] | None = None):
        self.args = args
        self.cwd = cwd
        self.prefix = list(prefix) if prefix is not None else taskset_prefix()
        self.cmdline = cmdline(args)
        self.display = (" ".join(self.prefix) + " " if self.prefix else "") + self.cmdline
        self.proc: subprocess.Popen | None = None
        self.rc: int | None = None
        self.started = time.time()
        self.output: list[str] = []

    @property
    def active(self) -> bool:
        return self.rc is None

    def stop(self, force: bool = False) -> bool:
        """Send SIGTERM (or SIGKILL when force=True) to the subprocess process group."""
        if self.proc is None or self.proc.poll() is not None:
            return False
        sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            os.killpg(self.proc.pid, sig)
        except ProcessLookupError:
            return False
        except OSError:
            try:
                if force:
                    self.proc.kill()
                else:
                    self.proc.terminate()
            except OSError:
                return False
        return True

    def stream(self, on_line: Callable[[str], None]) -> int:
        try:
            self.proc = subprocess.Popen(
                [*self.prefix, _ecc_path, *self.args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, text=True, errors="replace", bufsize=1,
                cwd=self.cwd, start_new_session=True,
                env={**os.environ, "PYTHONUNBUFFERED": "1", "FORCE_COLOR": "0"})
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                line = strip_ansi(line.rstrip("\n"))
                self.output.append(line)
                on_line(line)
            self.rc = self.proc.wait()
        except Exception as e:
            on_line(f"[ecctui] failed to run: {e}")
            self.rc = 1
        return self.rc

    @property
    def seconds(self) -> float:
        return time.time() - self.started
