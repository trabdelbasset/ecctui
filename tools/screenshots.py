#!/usr/bin/env python3
"""Headless pilot for ecctui: walks the whole UI, runs a real short flow, saves PNG screenshots.

usage (cwd outside the NTFS tree):
    ECCTUI_SHOT_ROOT=<projects> ECCTUI_DEMO_SRC=<ecc-ci-designs>/xtea PYTHONPATH=<repo root> uv run --with textual python <repo root>/tools/screenshots.py [--skip-run] [--only-run]

Screenshots go to docs/screenshots/*.png (SVG -> rsvg-convert -b "#121011").
The real run is pinned with ECCTUI_TASKSET (default 16-19) because the host is shared.
"""
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))

# never touch the user's real onboarding flag
CFG = Path(tempfile.mkdtemp(prefix="ecctui_cfg_"))
os.environ["ECCTUI_CONFIG_DIR"] = str(CFG)
(CFG / "seen_onboarding").write_text("1\n")
os.environ.setdefault("ECCTUI_TASKSET", "16-19")

from ecctui.app import EccTui  # noqa: E402
from ecctui.modals import FailureScreen, OnboardingScreen  # noqa: E402
from ecctui.screens import ChipScreen, HomeScreen, TimingScreen  # noqa: E402
from ecctui.widgets import ChipCard, Confetti, DieMap, MetroLine  # noqa: E402

ROOT = Path(os.environ.get("ECCTUI_SHOT_ROOT", "work")).expanduser().resolve()  # finished ECC projects
DEMO_ROOT = Path(os.environ.get("ECCTUI_DEMO_ROOT", "~/ecctui_demo")).expanduser()  # scratch copy for the live run
DEMO_SRC = Path(os.environ.get("ECCTUI_DEMO_SRC", "~/ecc-ci-designs/xtea")).expanduser()  # a small CI design
OUT = REPO / "docs" / "screenshots"
SIZE = (170, 50)


def shot(app, name):
    OUT.mkdir(parents=True, exist_ok=True)
    svg = OUT / f"{name}.svg"
    # rsvg-convert drops leading no-break spaces of a <text> run unless xml:space is preserved
    svg.write_text(app.export_screenshot().replace("<text ", '<text xml:space="preserve" '))
    subprocess.run(["rsvg-convert", "-b", "#121011", str(svg), "-o", str(OUT / f"{name}.png")], check=False)
    svg.unlink(missing_ok=True)
    print("saved", name, flush=True)


async def until(pilot, cond, timeout=30.0, step=0.25):
    for _ in range(int(timeout / step)):
        try:
            if cond():
                return True
        except Exception:
            pass
        await pilot.pause(step)
    return False


def chip(app) -> ChipScreen:
    return app.chip_screen()


async def open_chip(app, pilot, proj, ws="default"):
    app.open_chip(str(proj), ws)
    await until(pilot, lambda: chip(app) and chip(app).data is not None, 20)
    await until(pilot, lambda: chip(app).query_one(DieMap).cache, 15)
    await pilot.pause(0.6)


async def part_tour():
    app = EccTui(ROOT)
    async with app.run_test(size=SIZE) as pilot:
        home = lambda: app.home()
        await until(pilot, lambda: home() and all(c.data is not None for c in home().cards.values()), 60)
        await pilot.pause(1.0)
        shot(app, "01_home")

        await open_chip(app, pilot, ROOT / "APU")
        shot(app, "02_chip_APU")
        dm = chip(app).query_one(DieMap)
        labels = [m.label for m in dm.maps]
        # cycle through every map: RUDY and overflow are the required shots
        for i in range(1, len(dm.maps)):
            await pilot.press("right")
            await until(pilot, lambda: dm.idx == i and i in dm.cache, 8)
            await pilot.pause(0.5)
            if dm.maps[i].label == "RUDY":
                shot(app, "03_map_rudy")
            elif dm.maps[i].label == "overflow":
                shot(app, "04_map_overflow")
            elif dm.maps[i].label == "margin":
                shot(app, "04b_map_margin")
        print("maps cycled:", labels)
        await pilot.press("right")          # wraps to density
        await pilot.pause(0.5)
        metro = chip(app).query_one(MetroLine)
        metro.focus()
        metro.select("placement")
        await pilot.press("down", "down")   # cts
        await pilot.pause(1.0)
        shot(app, "05_station_drawer")
        await pilot.press("t")
        await until(pilot, lambda: isinstance(app.screen, TimingScreen), 5)
        await pilot.press("down", "right")
        await pilot.pause(0.8)
        shot(app, "06_timing")
        await pilot.press("escape")
        await pilot.pause(0.4)
        await pilot.press("space")
        await pilot.pause(0.8)
        shot(app, "07a_run_popover")
        await pilot.press("escape")
        await pilot.pause(0.3)
        await pilot.press("ctrl+p")
        await pilot.pause(0.6)
        await pilot.press(*"ecc rep")
        await pilot.pause(0.9)
        shot(app, "10_palette")
        await pilot.press("escape")
        await pilot.pause(0.3)
        app.action_help()
        await pilot.pause(0.5)
        shot(app, "10b_help")
        await pilot.press("escape")
        app.action_tour()
        await pilot.pause(0.6)
        assert isinstance(app.screen, OnboardingScreen)
        shot(app, "11_onboarding")
        await pilot.press("escape")
        await pilot.pause(0.4)
        await pilot.press("escape")          # back home
        await pilot.pause(0.5)
        await open_chip(app, pilot, ROOT / "BM64")
        shot(app, "12_chip_BM64")

    # small terminal
    app = EccTui(ROOT)
    async with app.run_test(size=(120, 36)) as pilot:
        await until(pilot, lambda: app.home() and all(c.data is not None for c in app.home().cards.values()), 60)
        await pilot.pause(0.8)
        shot(app, "13a_small_home_120x36")
        await open_chip(app, pilot, ROOT / "APU")
        shot(app, "13_small_terminal_120x36")


async def part_run():
    # fresh copy of xtea
    DEMO_ROOT.mkdir(parents=True, exist_ok=True)
    dst = DEMO_ROOT / "xtea"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(DEMO_SRC, dst)
    app = EccTui(DEMO_ROOT)
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.home() and all(c.data is not None for c in app.home().cards.values()), 30)
        await pilot.pause(0.5)
        app.home().query_one("#cards").children[0].focus()
        await pilot.press("enter")
        await until(pilot, lambda: chip(app) is not None, 10)
        await until(pilot, lambda: chip(app).data is not None, 20)
        await pilot.pause(0.8)
        shot(app, "07b_fresh_chip")
        args = ["run", "--project", str(dst.resolve()), "--workspace", "default",
                "--from", "synthesis", "--to", "place", "--plain"]
        app.start_run(args, str(dst.resolve()), "default", chip(app).data["stations"])
        print("CMD", app.run_handle.display, flush=True)
        ok = await until(pilot, lambda: (app.run_state or {}).get("done", 0) >= 3 and app.run_active, 240, 0.5)
        print("mid-run reached:", ok, app.run_state and {k: app.run_state[k] for k in ("done", "total", "step", "eta")}, flush=True)
        await pilot.pause(1.0)
        shot(app, "07_running")
        await pilot.pause(0.5)
        shot(app, "07c_running_pulse")
        await until(pilot, lambda: not app.run_active, 900, 1.0)
        print("run rc:", app.run_handle.rc, flush=True)
        await until(pilot, lambda: isinstance(app.screen, Confetti), 30, 0.1)   # pushed once the final state is loaded
        await pilot.pause(0.6)
        shot(app, "08_success_confetti")
        await pilot.pause(3.0)
        shot(app, "08b_after_success")
        # provoke a failure with a bad --from
        bad = ["run", "--project", str(dst.resolve()), "--workspace", "default", "--from", "no_such_step", "--plain"]
        app.start_run(bad, str(dst.resolve()), "default", chip(app).data["stations"])
        await until(pilot, lambda: isinstance(app.screen, FailureScreen), 90, 0.5)
        await pilot.pause(0.8)
        shot(app, "09_failure_card")


async def main():
    only_run = "--only-run" in sys.argv
    if not only_run:
        await part_tour()
    if "--skip-run" not in sys.argv:
        await part_run()


if __name__ == "__main__":
    asyncio.run(main())
