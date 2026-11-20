"""End-to-end UI flow tests: new-chip wizard, log screen, stop, reconcile-and-resume."""
from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

pytest.importorskip("textual")

from textual.screen import ModalScreen  # noqa: E402
from textual.widgets import Button, Input, RichLog  # noqa: E402

from ecctui import ecc  # noqa: E402
from ecctui.app import EccTui  # noqa: E402
from ecctui.modals import ConfirmModal, FailureScreen, LogScreen, NewChipWizard, OutputModal, RunPopover  # noqa: E402
from ecctui.screens import ChipScreen  # noqa: E402


async def _wait_until(pilot, cond, timeout: float = 10.0, step: float = 0.05) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            if cond():
                return
        except Exception:
            pass
        await pilot.pause(step)
    raise AssertionError("timed out waiting for condition")


async def _dismiss_modals(pilot, app: EccTui, max_steps: int = 5) -> None:
    for _ in range(max_steps):
        if not isinstance(app.screen, ModalScreen):
            return
        await pilot.press("escape")
        await pilot.pause(0.05)


def _make_fake_project(root: Path, name: str = "demo") -> Path:
    proj = root / name
    ws = proj / "default"
    (ws / "home").mkdir(parents=True)
    (ws / "06_placement_dreamplace" / "analysis").mkdir(parents=True)
    (ws / "06_placement_dreamplace" / "analysis" / "qor_metrics.json").write_text(
        json.dumps({"metrics": [{"id": "place_hpwl", "value": 100.0, "unit": "um"}]})
    )
    (proj / "ecc.toml").write_text('[design]\nname = "demo"\ntop = "demo"\n')
    (proj / "project.json").write_text(
        json.dumps({
            "schema_version": 1,
            "name": name,
            "root_path": str(proj.resolve()),
            "workspaces": [{"workspace_id": "default", "workspace_path": str(ws.resolve()), "status": "success"}],
        })
    )
    (ws / "home" / "flow.json").write_text(
        json.dumps({"steps": [{"name": "placement", "tool": "dreamplace", "directory": "06_placement_dreamplace", "state": "Success"}]})
    )
    return proj


def _write_fake_ecc(tmp_path: Path, proj: Path) -> tuple[Path, Path]:
    cmd_log = tmp_path / "ecc_invocations.jsonl"
    bin_path = tmp_path / "fake_ecc.py"
    script = textwrap.dedent(
        f"""\
        #!{sys.executable}
        import json, os, signal, sys, time
        from pathlib import Path

        LOG_FILE = Path({str(cmd_log)!r})
        argv = sys.argv[1:]
        with LOG_FILE.open("a") as fh:
            fh.write(json.dumps(argv) + "\\n")

        cmd = argv[0] if argv else ""
        sub = argv[1] if len(argv) > 1 and not argv[1].startswith("-") else ""

        def arg_val(flag, default=None):
            if flag in argv:
                i = argv.index(flag)
                if i + 1 < len(argv):
                    return argv[i + 1]
            return default

        proj = Path(arg_val("--project", {str(proj)!r}))
        pj_path = proj / "project.json"

        if cmd == "status":
            state_file = proj / "running_state.json"
            step_status = "running" if state_file.exists() else "success"
            print(json.dumps([
                {{"workspace_id": "default", "status": step_status}},
                {{"step": "synthesis", "tool": "yosys", "status": "success", "runtime": "0:00:02", "checklist_status": "passed"}},
                {{"step": "placement", "tool": "dreamplace", "status": step_status, "runtime": "0:00:03", "checklist_status": "passed"}},
            ]))
            sys.exit(0)

        if cmd == "report" and sub == "qor":
            print(json.dumps([{{"overall_score": 88.5, "qor_status": "GREEN", "dimensions": [], "qor": {{"feasibility": {{"gates": []}}}}}}]))
            sys.exit(0)

        if cmd == "flow" and sub == "list":
            print(json.dumps([{{"flow_id": "rtl2gds", "step_id": "synthesis"}}, {{"flow_id": "rtl2gds", "step_id": "placement"}}]))
            sys.exit(0)

        if cmd == "log":
            print("STORED LOG LINE 1")
            print("WARNING minor stored alert")
            sys.exit(0)

        if cmd == "process" and sub == "cancel":
            pj = json.loads(pj_path.read_text()) if pj_path.exists() else {{}}
            ent = (pj.get("runtime_processes") or {{}}).get("default") or {{}}
            pid = ent.get("pid")
            if pid:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except OSError:
                    pass
                pj.pop("runtime_processes", None)
                pj_path.write_text(json.dumps(pj))
                print(json.dumps([{{"status": "cancel_requested", "pid": pid}}]))
                sys.exit(0)
            sys.exit(22)

        if cmd == "process" and sub == "reconcile":
            (proj / "lock.flag").unlink(missing_ok=True)
            if pj_path.exists():
                pj = json.loads(pj_path.read_text())
                pj.pop("runtime_processes", None)
                for w in pj.get("workspaces", []):
                    w["status"] = "failed"
                pj_path.write_text(json.dumps(pj))
            print("reconciled workspace=default")
            sys.exit(0)

        if cmd == "run":
            if (proj / "lock.flag").exists():
                print("kind=error error=workspace_running reason=active run exists", flush=True)
                sys.exit(20)
            if (proj / "write_pid.flag").exists() and pj_path.exists():
                pj = json.loads(pj_path.read_text())
                pj["runtime_processes"] = {{"default": {{"run_id": "run_e2e_1", "pid": os.getpid()}}}}
                pj_path.write_text(json.dumps(pj))
            (proj / "running_state.json").write_text("1")
            try:
                if (proj / "hold_until.flag").exists():
                    for idx in range(1, 200):
                        print(f"STREAM LINE {{idx}}", flush=True)
                        if not (proj / "hold_until.flag").exists():
                            break
                        time.sleep(0.05)
                elif (proj / "sleep.flag").exists():
                    print("SLEEPING RUN STARTED", flush=True)
                    time.sleep(30)
                else:
                    print("RUN COMPLETED CLEANLY", flush=True)
            finally:
                (proj / "running_state.json").unlink(missing_ok=True)
            sys.exit(0)

        print(json.dumps([]))
        """
    )
    bin_path.write_text(script)
    bin_path.chmod(bin_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_path, cmd_log


@pytest.fixture(autouse=True)
def _isolated_tui_env(monkeypatch, tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir(parents=True)
    (cfg / "seen_onboarding").write_text("1\n")
    monkeypatch.setenv("ECCTUI_CONFIG_DIR", str(cfg))
    monkeypatch.delenv("ECCTUI_TASKSET", raising=False)
    ecc.clear_cache()
    orig_ecc = ecc.ecc_path()
    yield
    ecc.set_ecc(orig_ecc)
    ecc.clear_cache()


def test_new_chip_wizard_end_to_end_real_ecc(tmp_path):
    repo_root = Path(__file__).resolve().parents[2] / "repos" / "ecc"
    env_py = os.environ.get("ECCTUI_TEST_ECC_PY")
    ecc_py = Path(env_py) if env_py else (Path(__file__).resolve().parents[3] / "ecc" / ".venv" / "bin" / "python")
    if not (repo_root / "chipcompiler" / "cli" / "main.py").is_file() or not ecc_py.is_file():
        pytest.skip("real ecc source checkout or interpreter not present")

    wrapper = tmp_path / "real_ecc"
    wrapper.write_text(
        f"#!/bin/sh\n"
        f"PYTHONPATH={repo_root} exec {ecc_py} -c "
        f"'import sys; from chipcompiler.cli.main import main; sys.argv=[\"ecc\"]+sys.argv[1:]; main()' \"$@\"\n"
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)

    rtl_a = tmp_path / "a.v"
    rtl_b = tmp_path / "b.v"
    rtl_a.write_text("module a(input clk_sys, output y); wire w; b u(.x(clk_sys), .y(w)); assign y = w; endmodule\n")
    rtl_b.write_text("module b(input x, output y); assign y = x; endmodule\n")

    root = tmp_path / "projects"
    root.mkdir()

    async def _run():
        app = EccTui(root, ecc_bin=str(wrapper))
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("n")
            await _wait_until(pilot, lambda: isinstance(app.screen, NewChipWizard) and app.screen.is_mounted)

            wiz = app.screen
            wiz.query_one("#name", Input).value = "demo_chip"
            wiz.query_one("#parent", Input).value = str(root)
            await _wait_until(pilot, lambda: "demo_chip" in str(wiz.query_one("#preview").render()))
            await pilot.pause(0.1)
            await pilot.click("#next")
            await _wait_until(pilot, lambda: wiz.step == 1)

            wiz.query_one("#rtl", Input).value = f"{rtl_a}, {rtl_b}"
            wiz.query_one("#top", Input).value = "a"
            await _wait_until(pilot, lambda: str(rtl_b) in str(wiz.query_one("#preview").render()))
            await pilot.pause(0.1)
            await pilot.click("#next")
            await _wait_until(pilot, lambda: wiz.step == 2)

            wiz.query_one("#clock", Input).value = "clk_sys"
            wiz.query_one("#freq", Input).value = "50"
            await _wait_until(pilot, lambda: "clk_sys MAX:50" in str(wiz.query_one("#preview").render()))
            await pilot.pause(0.1)
            preview_txt = wiz._preview_text()
            assert f"--verilog {rtl_a}" in preview_txt and f"--verilog {rtl_b}" in preview_txt
            assert "param set sdc.clock_list" in preview_txt

            await pilot.click("#ok")
            await _wait_until(pilot, lambda: isinstance(app.screen, OutputModal) and app.screen.is_mounted, timeout=15.0)
            out = app.screen
            assert out.rc == 0, out.raw

    asyncio.run(_run())

    toml_path = root / "demo_chip" / "ecc.toml"
    assert toml_path.is_file()
    parsed = tomllib.loads(toml_path.read_text())
    assert parsed["design"]["name"] == "demo_chip"
    assert parsed["design"]["top"] == "a"
    assert parsed["design"]["rtl"] == ["rtl/a.v", "rtl/b.v"]
    assert "clk_sys MAX:50 WCL:50 MIN:50 ML:50 TYP:50" in parsed["params"]["sdc"]["clock_list"]


def test_log_screen_stored_and_live_streaming(tmp_path):
    root = tmp_path / "work"
    proj = _make_fake_project(root)
    fake_ecc, _ = _write_fake_ecc(tmp_path, proj)

    async def _run():
        app = EccTui(root, ecc_bin=str(fake_ecc))
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.1)
            app.open_chip(str(proj), "default")
            await _wait_until(pilot, lambda: isinstance(app.screen, ChipScreen) and app.screen.data is not None)

            # 1) Stored station log
            await pilot.press("l")
            await _wait_until(pilot, lambda: isinstance(app.screen, LogScreen) and app.screen.is_mounted)
            assert app.screen.live is False
            stored_text = "\n".join(str(ln.text) for ln in app.screen.query_one("#biglog", RichLog).lines)
            assert "STORED LOG LINE 1" in stored_text
            await pilot.press("escape")
            await _wait_until(pilot, lambda: isinstance(app.screen, ChipScreen))

            # 2) Live streaming log
            hold_flag = proj / "hold_until.flag"
            hold_flag.write_text("1")
            await pilot.press("o")
            await _wait_until(pilot, lambda: isinstance(app.screen, RunPopover) and app.screen.is_mounted)
            await pilot.press("enter")
            await _wait_until(pilot, lambda: app.run_active and len(app.run_lines) >= 2)

            await pilot.press("l")
            await _wait_until(pilot, lambda: isinstance(app.screen, LogScreen) and app.screen.is_mounted)
            assert app.screen.live is True
            assert app.log_screen is app.screen
            await _wait_until(
                pilot,
                lambda: any("STREAM LINE" in str(ln.text) for ln in app.screen.query_one("#biglog", RichLog).lines),
            )
            hold_flag.unlink(missing_ok=True)
            await pilot.press("escape")
            await _wait_until(pilot, lambda: isinstance(app.screen, ChipScreen))
            assert app.log_screen is None
            await _wait_until(pilot, lambda: not app.run_active)

    asyncio.run(_run())


def test_stop_active_run_via_cancel_and_local_fallback(tmp_path):
    root = tmp_path / "work"
    proj = _make_fake_project(root)
    fake_ecc, cmd_log = _write_fake_ecc(tmp_path, proj)

    async def _run():
        app = EccTui(root, ecc_bin=str(fake_ecc))
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.1)
            app.open_chip(str(proj), "default")
            await _wait_until(pilot, lambda: isinstance(app.screen, ChipScreen) and app.screen.data is not None)

            # Case A: run_id registered in project.json -> ConfirmModal sends `process cancel`
            (proj / "sleep.flag").write_text("1")
            (proj / "write_pid.flag").write_text("1")
            app.start_run(["run", "--project", str(proj), "--workspace", "default", "--plain"], str(proj), "default", [])
            await _wait_until(pilot, lambda: app.run_active and ecc.runtime_run_id(proj, "default") == "run_e2e_1")

            await pilot.press("x")
            await _wait_until(pilot, lambda: isinstance(app.screen, ConfirmModal) and app.screen.is_mounted)
            assert "process cancel" in (app.screen._cmd or "")
            await pilot.click("#ok")
            await _wait_until(pilot, lambda: not app.run_active, timeout=8.0)

            await _dismiss_modals(pilot, app)

            # Case B: no run_id in project.json -> falls back cleanly to local RunHandle.stop()
            (proj / "write_pid.flag").unlink(missing_ok=True)
            app.start_run(["run", "--project", str(proj), "--workspace", "default", "--plain"], str(proj), "default", [])
            await _wait_until(pilot, lambda: app.run_active and app.run_handle.proc is not None)

            await pilot.press("x")
            await _wait_until(pilot, lambda: isinstance(app.screen, ConfirmModal) and app.screen.is_mounted)
            await pilot.click("#ok")
            await _wait_until(pilot, lambda: not app.run_active, timeout=8.0)

    asyncio.run(_run())
    cmds = [json.loads(ln) for ln in cmd_log.read_text().splitlines()]
    assert any(c[:2] == ["process", "cancel"] and "--run-id" in c for c in cmds)


def test_reconcile_and_resume_end_to_end(tmp_path):
    root = tmp_path / "work"
    proj = _make_fake_project(root)
    fake_ecc, cmd_log = _write_fake_ecc(tmp_path, proj)

    async def _run():
        app = EccTui(root, ecc_bin=str(fake_ecc))
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.1)
            app.open_chip(str(proj), "default")
            await _wait_until(pilot, lambda: isinstance(app.screen, ChipScreen) and app.screen.data is not None)

            # 1) Stale lock failure -> FailureScreen -> Reconcile & resume -> clean resumed run
            pj = json.loads((proj / "project.json").read_text())
            pj["runtime_processes"] = {"default": {"run_id": "stale_99", "pid": 999999}}
            (proj / "project.json").write_text(json.dumps(pj))
            (proj / "lock.flag").write_text("1")

            app.start_run(["run", "--project", str(proj), "--workspace", "default", "--plain"], str(proj), "default", [])
            await _wait_until(pilot, lambda: isinstance(app.screen, FailureScreen) and app.screen.is_mounted)
            fail = app.screen
            assert fail.code == "workspace_running"
            assert fail.query_one("#reconcile", Button).disabled is False

            await pilot.click("#reconcile")
            await _wait_until(pilot, lambda: not app.run_active and app.run_state and app.run_state.get("finished") and app.run_handle.rc == 0 and "--resume" in (app.run_state.get("args") or []))
            assert not (proj / "lock.flag").exists()

            # 2) Direct reconcile without runtime_processes.run_id in project.json
            await _dismiss_modals(pilot, app)
            (proj / "lock.flag").write_text("1")
            app.action_reconcile(resume=False)
            await _wait_until(pilot, lambda: not (proj / "lock.flag").exists())

    asyncio.run(_run())
    cmds = [json.loads(ln) for ln in cmd_log.read_text().splitlines()]
    assert any(c[:2] == ["process", "reconcile"] and "--run-id" in c for c in cmds)
    assert any(c[:2] == ["process", "reconcile"] and "--run-id" not in c for c in cmds)
    assert any(c[0] == "run" and "--resume" in c for c in cmds)
