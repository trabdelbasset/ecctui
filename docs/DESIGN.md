# ecctui: design spec ("ECOS Mission Control")

Architecture and layout for the terminal UI wrapping the `ecc` CLI. Reuse `ecctui/ecc.py` and `theme.py` colors across `app.py`, `screens.py`, `widgets.py`, and `styles.tcss`.

## Design principles

1. **Show the chip.** The centerpiece is a live, colorful die map built from the real placement and congestion grids ECC already writes. This is the "fun" part and the thing designers care about.
2. **The flow is a metro line.** The 21 steps are stations on a subway map grouped into 4 colored lines. Navigation is ↑/↓ plus Enter. Rerunning is Space on a station. No flags to remember.
3. **One primary action per screen.** The hint bar shows at most 5 contextual keys; everything else lives in the palette (`ctrl+p`), which lists every ecc command.
4. **Calm by default, alive when running.** Animations happen only during runs: a pulsing station, a moving train, a progress gradient, then confetti on success and a red flash with a "what to do" card on failure.
5. **Truth over vanity.** The Health panel shows ECC's QoR **and** the extra checks that ECC hides: an antenna VACUOUS warning, DRV counts from STA (`design_statistics.{tran,cap,fanout}` in `sta_ecc/feature/*/*/qor_summary.json`), and thin hold margin.
6. **Mouse works everywhere.** Cards, stations, buttons and map tabs are clickable.

## Palette (from the ECC README brand)

| Role | Color |
|---|---|
| Orange (brand) | `#EF6C00` |
| Background | `#121011` |
| Light orange | `#FF8F00` |
| Gold accent | `#FFB74D` |
| Surface | `#1B1716` |
| Panel | `#241E1B` |
| Text | `#F3ECE6` |
| Dim text | `#8C8079` |
| Ok | `#8BC34A` |
| Warn | `#FFB300` |
| Bad | `#EF5350` |

Heatmap gradient (low→high): `#121011 → #3A1F0F → #7A3306 → #C25500 → #EF6C00 → #FFB74D → #FFF3E0`. A cool alternative for the congestion overflow map: `#121011 → #1E3A5F → #3D7DCA → #FFB74D → #EF5350`.

Use truecolor half-blocks (`▀`, with fg = upper pixel and bg = lower pixel) so that each terminal cell holds 2 vertical pixels. That roughly doubles the vertical resolution.

## Screen 1: Launcher (home)

```
╭──────────────────────────────────────────────────────────────────────────────────────────╮
│  ███████╗ ██████╗ ██████╗ ███████╗     ECOS Chip Compiler                ecc v0.1.0a12   │
│  ██╔════╝██╔════╝██╔═══██╗██╔════╝     RTL → GDS, open source         ✓ doctor  ics55    │
│  █████╗  ██║     ██║   ██║███████╗                                                        │
│  ██╔══╝  ██║     ██║   ██║╚════██║     14 chips · 13 tapeout-clean · 1 running            │
│  ███████╗╚██████╗╚██████╔╝███████║                                                        │
│  ╚══════╝ ╚═════╝ ╚═════╝ ╚══════╝                                                        │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ ╭─ APU ───────────────╮ ╭─ BM64 ──────────────╮ ╭─ aes ───────────────╮ ╭─ + new chip ─╮ │
│ │ ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀ │ │ ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀ │ │ ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀ │ │              │ │
│ │ ▀▀▀ (density map) ▀ │ │ ▀▀▀ (density map) ▀ │ │ ▀▀▀ (density map) ▀ │ │      ✚       │ │
│ │ ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀ │ │ ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀ │ │ ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀ │ │  init a new  │ │
│ │ 89.7 ◆ YELLOW       │ │ 59.0 ◆ RED          │ │ 67.7 ◆ ORANGE       │ │   project    │ │
│ │ ✓ 21/21   ⏱ 51m     │ │ ✓ 21/21  ⚠ 7 DRV    │ │ ✓ 21/21  ⚠ 12 DRV   │ │              │ │
│ ╰─────────────────────╯ ╰─────────────────────╯ ╰─────────────────────╯ ╰──────────────╯ │
│  … more cards, wrapping into a grid; ←↑→↓ to move, ⏎ to open                             │
╰──────────────────────────────────────────────────────────────────────────────────────────╯
 ⏎ open   n new chip   / search   ctrl+p commands   ? help              💡 tip: Space on a station re-runs from there
```

- The ASCII "ECOS" logo has an orange-to-gold vertical gradient (one color per row).
- Each card shows a **mini die thumbnail**: the cell-density map downsampled to about 19×3 cells × 2 half-block pixels.
- The focused card gets a heavy orange border and a subtle lift (brighter panel background).
- The sort selector (`s`) offers score / name / runtime / status.
- The `/` search filters the cards live.
- The tip line rotates random tips every 10 s.

## Screen 2: Chip (one project or workspace)

```
 ◆ ECOS ▸ APU ▸ default                                     89.7 YELLOW   ✓ gates 7/7   ⚠ antenna unverified   15:04
╭─ FLOW ──────────────────────╮╭─ DIE · 147 × 147 µm · cell density ───────────────────────────╮╭─ HEALTH ──────────────╮
│ ━━ SYNTH & FLOORPLAN        ││▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀││     ╭──────────╮      │
│  ●  synthesis        0:43   ││▀▀▀  heatmap of the real placement grid, truecolor half-blocks  ││     │  89.7    │      │
│  │                          ││▀▀▀  (fills the panel; preserves die aspect ratio)              ││     │ ◆ YELLOW │      │
│  ●  floorplan        0:01   ││▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀││     ╰──────────╯      │
│  ●  macros           0:03   ││▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀││ timing   ██████████ 100│
│ ━━ PLACE & CLOCK            ││                                                                 ││ wires    ███████▌   78│
│  ●  preplace         0:32   ││                                                                 ││ area     ████████▋  87│
│  ●  place           33:35 ◀ ││                                                                 ││ power    ─ no budget  │
│  ●  diff_sizing      8:10   ││                                                                 ││ robust   █████████▏ 92│
│  ●  cts              0:33   ││                                                                 ││──────────────────────│
│  ●  legalize         0:02   ││  low ░▒▓█ high   ◀ density · RUDY · overflow · margin ▶         ││ SIGNOFF              │
│  ●  timing opt       1:12   │╰─────────────────────────────────────────────────────────────────╯│ ● DRC 0   ● LVS 0    │
│ ━━ ROUTE & SIGNOFF          │╭─ STATION · place (dreamplace) ──────────────────────────────────╮│ ● setup +2.55 ns 15% │
│  ●  route            0:35   ││  ✓ success · 33m 35s · HPWL 3 143 µm · overflow 0.17 %          ││ ● hold  +31 ps  thin │
│  ●  filler · lvs · drc      ││  ⏵ run from here (space)   ⏵ run only (o)   📜 log (l)          ││ ⚠ DRV   0 tran 0 cap │
│  ◐  antenna        VACUOUS  ││  last log lines … (dim, 4 lines)                                 ││ ⚠ antenna  no rules  │
│  ●  post-route LEC   0:07   │╰─────────────────────────────────────────────────────────────────╯│ ● LEC   equivalent   │
│ ━━ ANALYSIS                 │                                                                   │──────────────────────│
│  ●  rcx · sta · power · ir  │                                                                   │ 💬 interconnect is the│
│  ●  harden           0:18   │                                                                   │ weakest: 0.17% GCells │
╰─────────────────────────────╯                                                                   │ overflowed            │
                                                                                                    ╰──────────────────────╯
 ↑↓ station   space run from here   ←→ map   t timing   ctrl+p commands   esc home
```

Flow metro line (left):
- A vertical line glyph `│` (or `┃` when focused) runs between stations. Each phase header `━━ NAME` has its own line color:
  - synth & floorplan: gold
  - place & clock: orange
  - route & signoff: deep orange
  - analysis: amber
- Station glyphs: `●` success (green), `◉` running (pulsing orange: alternate `◉`/`○` every 0.5 s with a color fade), `○` pending (dim), `✗` failed (red), `◐` warn or vacuous (yellow), `⊘` skipped (dim strikethrough).
- Durations sit on the right. The slowest station gets a tiny `▲` hot marker.
- Short labels (map status ids to friendly names): `pre_floorplan`/`post_floorplan` → floorplan, `macro_placement` → macros, `timing_optimization` → timing opt, `legalization` → legalize. Collapse tiny steps that run back-to-back into one station row (`filler · lvs · drc`, `rcx · sta · power · ir`) **only** when they are all successful; expand them when any one is selected or failed.
- The selected station is highlighted with an orange background bar.
- Station drawer (center bottom): Enter or selection updates it. It shows status, duration, 2–3 key metrics for that step (from `ecc report step <step> --json`, or the `analysis/qor_metrics.json` of that step dir), action buttons, and a dim 4-line log tail.

Die map (center, the hero):
- Source CSV grids, under `<workspace>/place_dreamplace/feature/`:
  - `density_map/place_allcell_density.csv`
  - `RUDY_map/place_rudy_union.csv`
  - `egr_congestion_map/place_egr_union_overflow.csv`
  - `margin_map/place_union_margin.csv`
- Also look for maps in later steps (`*_ecc/feature/*map*`); `glob` for `**/*map*/*.csv` and offer every map found.
- Die size comes from `config/floorplan_ecc.json`, the DEF `DIEAREA`, or the `die_area` metric.
- Render: resample the grid to the panel size (nearest or bilinear), map values to the gradient with robust normalization (p2–p98), then draw half-blocks.
- Title: map name, die size, min/max. ←/→ (or click the map tabs) cycles maps.
- **Bonus fun:** on hover or mouse move, show the value at the cursor in the panel subtitle.
- A gradient legend bar sits at the bottom.

Health panel (right):
- **Score badge:** a big score drawn with a 3-row block-digit font, the band chip, and a ring-ish border colored by band.
- **Dimension bars:** smooth Unicode eighth-block bars (`▏▎▍▌▋▊▉█`) with gradient color by value; null shows "─ no budget" or "─ unknown".
- **SIGNOFF list:** traffic-light dots, including the extra checks:
  - DRV from STA `design_statistics`;
  - antenna vacuity from `antennaCheck_ecc/feature/antennaCheck.step.json` (`antenna_check.signal_net_count == 0` → VACUOUS);
  - thin hold margin if hold WS < 50 ps;
  - LEC from the `postRouteLec` step state.
- **"💬" insight line:** the single weakest dimension, or the worst signoff issue, in one plain sentence.

Top bar: breadcrumb `◆ ECOS ▸ APU ▸ default` plus score, gates, a warnings summary and a clock. Esc goes back home.

## Screen 3: Timing (`t` from the Chip screen)

- **Corner matrix heatmap.** Rows are delay corners (MAX_125, MIN_m40, TYP_25, ML_125, WCL_m40, …). Columns are RC corners (Cworst, Cbest, RCworst, RCbest, TYPICAL). Each cell is colored by setup WS (left half-cell) and hold WS (right half-cell), with the value printed. Source: `sta_ecc/feature/<corner>/<rc>/qor_summary.json` (`summary.setup.wns`, `summary.hold.wns`, `tns`, `nvp`).
- **Clock-group table** from `path_groups[]`.
- **DRV badges per corner:** tran, cap, fanout.
- **Worst-paths list:** parse the top of `sta_ecc/report/<corner>/<rc>/timing_max_reg2reg.rpt` if feasible; otherwise show path counts.
- Esc goes back.

## Run experience

- **Space on a station** opens a compact confirm popover: `Run from place? ⏎ go · o only this · esc cancel`. It shows the command preview in dim mono. Advanced options (preset, `--set`, overwrite, from/to range) sit behind a `⋯ more` toggle (`RunModal`).
- **While running:**
  - The running station pulses.
  - A small `▶` train animates down the line segment toward the next station.
  - The top bar becomes a gradient progress bar with `step k/n · current · elapsed · ETA`. ETA uses the durations from this workspace's previous run when available.
  - The log drawer streams output (ANSI stripped; ERROR, Traceback and FAIL lines red; WARNING lines amber).
  - The die map refreshes when placement finishes.
- **Success:** a 2-second confetti overlay (falling `✦ ✧ • ◆` in orange and gold), then a toast: `✓ APU finished · 89.7 YELLOW · 51m`.
- **Failure:** the station flashes red 3 times, and a "What happened" card opens with:
  - the last error lines;
  - a classified cause: `workspace_running` → stale lock; `env_not_ready`; `flow_range_requires_pair`; generic;
  - one-click fixes: **Reconcile & resume** (reconcile lock and run `--resume`), Open log, Retry.

## Interaction model

| Key | Action |
|---|---|
| ←↑→↓ / mouse | Move |
| ⏎ | Open or select |
| Space | Run from here |
| o | Run only this station |
| l | Log |
| t | Timing |
| ←/→ on the map | Cycle maps |
| esc | Back |
| ctrl+p | Command palette (all ecc commands) |
| ? | Help overlay |
| `n` | New chip (wizard modal with 3 steps: name → RTL file(s) → clock & frequency) |

- **First launch:** a 3-card onboarding overlay ("This is your flow…", "This is your chip…", "Press space to run…"). Store a `~/.config/ecctui/seen_onboarding` flag.
- **Hint bar:** at most 5 contextual keys, styled as key chips (`⏎` in an orange box, then the label).

## Engineering

- Textual (latest), Python ≥ 3.11, no extra dependencies. Rich is allowed; Pillow is not.
- Screens: `HomeScreen`, `ChipScreen`, `TimingScreen`, plus modal screens.
- Widgets: `DieMap`, `MetroLine`, `Station`, `HealthPanel`, `ScoreBadge`, `BarRow`, `ChipCard`, `Confetti`, `HintBar`, `TopBar`.
- Use `render_line` / `Strip` for `DieMap`. Do **not** use one widget per pixel.
- All `ecc` calls and file I/O run off the UI thread, behind workers with a loading shimmer (a dim animated `░▒▓` placeholder).
- Projects root: `--root` (`ecc.toml`, skip `fi_*`, resolve symlinks).
- Preserve the CLI contract: which commands reject `--json`/`--project`, the `process cancel --run-id` requirement, and the step-name mapping `flow list` ids ↔ `status` tokens.
- Must work at 120×36 minimum, and look great at 200×55.

## Done means

1. A headless pilot (`screenshots.py`, update it) visits Home, opens APU, moves stations, cycles all maps, opens Timing, opens the palette and the help overlay, and starts a real short run on a fresh copy of `<ecc-ci-designs>/xtea` in `a scratch demo dir` (`--from synthesis --to place`). It captures the run mid-way (pulsing station plus progress) and at success (confetti frame).
2. Screenshots go to `docs/screenshots/*.png` via `rsvg-convert -b "#121011"`:
   - `01_home`
   - `02_chip_APU`
   - `03_map_rudy`
   - `04_map_overflow`
   - `05_station_drawer`
   - `06_timing`
   - `07_running`
   - `08_success_confetti`
   - `09_failure_card` (provoke one with a bad `--from` value)
   - `10_palette`
   - `11_onboarding`
   - `12_chip_BM64`
   - `13_small_terminal_120x36`
3. **Look at every PNG yourself and iterate until it looks genuinely beautiful:**
   - aligned borders;
   - no clipping or overflow;
   - the die map fills its panel;
   - balanced whitespace;
   - consistent colors;
   - no orphan text.
4. Update `ecctui/README.md`.
