"""Entry point: python -m ecctui --root <dir-with-projects> [--ecc PATH] [--taskset CPUS]"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ecctui", description="ECOS Mission Control: terminal UI for the ECC CLI")
    ap.add_argument("--root", default=".", help="directory containing ECC projects (ecc.toml)")
    ap.add_argument("--ecc", default=None, help="path to the ecc binary (default: which ecc)")
    ap.add_argument("--taskset", default=None, metavar="CPUS",
                    help="pin `ecc run` launches to these CPUs, e.g. 16-19 (also env ECCTUI_TASKSET)")
    ns = ap.parse_args(argv)
    if ns.taskset:
        os.environ["ECCTUI_TASKSET"] = ns.taskset
    from .app import EccTui
    EccTui(Path(ns.root).expanduser(), ns.ecc).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
