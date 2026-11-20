# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - 2026-11-16

First standalone release: "ECOS Mission Control", a terminal UI for the `ecc` CLI.

### Added
- Home screen: one card per ECC project with density-map thumbnail, score and band,
  step progress, DRV marker and runtime; sort, live filter, and a new-chip wizard.
- Chip screen: 21-step `rtl2gds` metro line with station status, die maps (density,
  RUDY, congestion overflow, timing margin, later-step maps), health panel and station drawer.
- Timing screen: corner matrix of setup and hold WNS with per-corner DRV badges and worst reg2reg paths.
- Run experience: run popover ("from here", "only this", full run dialog), live progress bar with ETA,
  success confetti, failure card with classified causes.
- Command palette (`ctrl+p`) for app actions and every `ecc` subcommand, with JSON result viewer.
- Truth checks recomputed from ECC output: DRV, vacuous antenna check, thin hold, setup, LEC, DRC/LVS.
- Onboarding, key help, and a layout that works from 120x36.
- `--taskset` / `ECCTUI_TASKSET` to pin `ecc run` launches to CPUs on shared hosts.
- Headless screenshot pilot (`tools/screenshots.py`) and design notes (`docs/DESIGN.md`).
- Unit and end-to-end tests for workspace discovery, heat maps, STA parsing, signoff health checks, stop, reconcile-and-resume, log viewer, and the new-chip wizard.
