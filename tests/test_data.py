"""Unit tests for ecctui workspace parsing, maps, STA matrix, and signoff health."""
from __future__ import annotations

import json
from pathlib import Path

from ecctui import data as D

_SNAKE_DIRS = [
    ("synthesis", "01_synthesis_yosys"),
    ("pre_floorplan", "02_pre_floorplan_ecc"),
    ("macro_placement", "03_macro_placement_dreamplace"),
    ("post_floorplan", "04_post_floorplan_ecc"),
    ("preplace", "05_preplace_sizer"),
    ("placement", "06_placement_dreamplace"),
    ("diff_sizing", "07_diff_sizing_dreamplace"),
    ("cts", "08_cts_ecc"),
    ("legalization", "09_legalization_dreamplace"),
    ("timing_optimization", "10_timing_optimization_sizer"),
    ("routing", "11_routing_ecc"),
    ("filler", "12_filler_ecc"),
    ("lvs", "13_lvs_ecc"),
    ("drc", "14_drc_ecc"),
    ("antenna_check", "15_antenna_check_ecc"),
    ("post_route_lec", "16_post_route_lec_kepler_formal"),
    ("rcx", "17_rcx_ecc"),
    ("sta", "18_sta_ecc"),
    ("power_analysis", "19_power_analysis_ecc"),
    ("emir", "20_emir_ecc"),
    ("harden", "21_harden_ecc"),
]

_NAMED_DIRS = [
    ("synthesis", "Synthesis_yosys"),
    ("placement", "place_dreamplace"),
    ("legalization", "legalization_dreamplace"),
    ("routing", "route_ecc"),
    ("drc", "drc_ecc"),
    ("lvs", "lvs_ecc"),
    ("antenna_check", "antennaCheck_ecc"),
    ("post_route_lec", "postRouteLec_kepler_formal"),
    ("sta", "sta_ecc"),
    ("power_analysis", "powerAnalysis_ecc"),
    ("harden", "Harden_ecc"),
]


def _make_ws(tmp_path: Path, *, snake: bool, with_flow_json: bool = True) -> Path:
    ws = tmp_path / ("ws_snake" if snake else "ws_named") / "default"
    (ws / "home").mkdir(parents=True)
    dirs = _SNAKE_DIRS if snake else _NAMED_DIRS
    flow_steps = []
    for name, dname in dirs:
        sdir = ws / dname
        (sdir / "analysis").mkdir(parents=True)
        (sdir / "feature").mkdir(parents=True)
        (sdir / "report").mkdir(parents=True)
        flow_steps.append({"name": name, "directory": dname, "state": "Success"})
    if with_flow_json:
        (ws / "home" / "flow.json").write_text(json.dumps({"steps": flow_steps}))

    place_dir = ws / ("06_placement_dreamplace" if snake else "place_dreamplace")
    leg_dir = ws / ("09_legalization_dreamplace" if snake else "legalization_dreamplace")
    sta_dir = ws / ("18_sta_ecc" if snake else "sta_ecc")
    ant_dir = ws / ("15_antenna_check_ecc" if snake else "antennaCheck_ecc")
    pwr_dir = ws / ("19_power_analysis_ecc" if snake else "powerAnalysis_ecc")
    drc_dir = ws / ("14_drc_ecc" if snake else "drc_ecc")
    lvs_dir = ws / ("13_lvs_ecc" if snake else "lvs_ecc")

    (place_dir / "analysis" / "qor_metrics.json").write_text(json.dumps({
        "metrics": [
            {"id": "place_hpwl", "value": 1234.5, "unit": "um"},
            {"id": "die_width", "value": 100.0, "unit": "um"},
            {"id": "die_height", "value": 80.0, "unit": "um"},
            {"id": "core_utilization", "value": 0.45, "unit": "ratio"},
        ]
    }))
    (pwr_dir / "analysis" / "qor_metrics.json").write_text(json.dumps({
        "metrics": [
            {"id": "power_analysis_dynamic_uw", "value": 42.5, "unit": "uW"},
            {"id": "power_analysis_leakage_uw", "value": 1.5, "unit": "uW"},
        ]
    }))
    (drc_dir / "analysis" / "qor_metrics.json").write_text(json.dumps({
        "metrics": [{"id": "drc_count", "value": 0, "unit": "count"}]
    }))
    (lvs_dir / "analysis" / "qor_metrics.json").write_text(json.dumps({
        "metrics": [{"id": "lvs_count", "value": 0, "unit": "count"}]
    }))

    for d, fname in ((place_dir, "demo_allcell_density.csv"), (leg_dir, "demo_allcell_density.csv")):
        dm = d / "feature" / "density_map"
        dm.mkdir(parents=True)
        (dm / fname).write_text("0.1,0.2\n0.3,0.4\n")

    (sta_dir / "feature" / "MAX_125" / "Cworst").mkdir(parents=True)
    (sta_dir / "feature" / "MAX_125" / "Cworst" / "qor_summary.json").write_text(json.dumps({
        "summary": {
            "setup": {"wns": 1.25, "tns": 0.0, "nvp": 0},
            "hold": {"wns": 0.08, "tns": 0.0, "nvp": 0},
        },
        "design_statistics": {"tran": 0, "cap": 0, "fanout": 0},
        "path_groups": [{"group": "reg2reg", "wns": 1.25}],
    }))
    rpt = sta_dir / "report" / "MAX_125" / "Cworst" / "timing_max_reg2reg.rpt"
    rpt.parent.mkdir(parents=True)
    rpt.write_text("Startpoint: u_a/Q\nEndpoint: u_b/D\nslack (MET) 1.25\n")

    ant_name = "antenna_check.step.json" if snake else "antennaCheck.step.json"
    (ant_dir / "feature" / ant_name).write_text(json.dumps({
        "antenna_check": {
            "signal_net_count": 361,
            "pins_with_gate_area": 240,
            "violation_count": 0,
        }
    }))
    return ws


def test_step_dir_and_tokens_across_layouts(tmp_path):
    for snake in (False, True):
        for with_flow_json in (True, False):
            ws = _make_ws(tmp_path / f"{snake}_{with_flow_json}", snake=snake, with_flow_json=with_flow_json)
            assert D.step_dir(ws, "placement") == ws / ("06_placement_dreamplace" if snake else "place_dreamplace")
            assert D.step_dir(ws, "routing") == ws / ("11_routing_ecc" if snake else "route_ecc")
            assert D.step_dir(ws, "antenna_check") == ws / ("15_antenna_check_ecc" if snake else "antennaCheck_ecc")
            assert D.step_dir(ws, "antennacheck") == ws / ("15_antenna_check_ecc" if snake else "antennaCheck_ecc")
            assert D.step_dir(ws, "post_route_lec") == ws / ("16_post_route_lec_kepler_formal" if snake else "postRouteLec_kepler_formal")
            assert D.step_dir(ws, "sta") == ws / ("18_sta_ecc" if snake else "sta_ecc")
            assert D.step_dir(ws, "power_analysis") == ws / ("19_power_analysis_ecc" if snake else "powerAnalysis_ecc")
            assert D.step_dir(ws, "poweranalysis") == ws / ("19_power_analysis_ecc" if snake else "powerAnalysis_ecc")

    assert D.canonical_token("antennacheck") == "antenna_check"
    assert D.canonical_token("postroutelec") == "post_route_lec"
    assert D.canonical_token("poweranalysis") == "power_analysis"
    assert D.canonical_token("place") == "placement"
    assert D.canonical_token("route") == "routing"
    assert D.flow_id_for("placement", ["synthesis", "placement", "routing"]) == "placement"
    assert D.flow_id_for("placement", ["Synthesis", "place", "route"]) == "place"
    assert D.flow_id_for("power_analysis", ["powerAnalysis"]) == "powerAnalysis"
    assert D.label_for("antenna_check") == "antenna"
    assert D.phase_for("post_route_lec") == "ROUTE & SIGNOFF"


def test_station_defs_preserves_raw_tokens_and_collapses(tmp_path):
    steps_snake = [{"step": s[0], "status": "success", "runtime": "0:00:05"} for s in D.STATIONS]
    st_snake = D.station_defs(steps_snake)
    assert len(st_snake) == len(D.STATIONS)
    assert {s["token"] for s in st_snake} >= {"antenna_check", "post_route_lec", "power_analysis"}

    steps_legacy = [{"step": "antennacheck", "status": "success"}, {"step": "postroutelec", "status": "success"}]
    st_legacy = D.station_defs(steps_legacy)
    by_label = {s["label"]: s for s in st_legacy}
    assert by_label["antenna"]["token"] == "antennacheck"
    assert by_label["post-route LEC"]["token"] == "postroutelec"
    assert by_label["antenna"]["status"] == "success"
    assert by_label["sta"]["status"] == "pending"


def test_metrics_die_maps_and_sta_across_layouts(tmp_path):
    for snake in (False, True):
        ws = _make_ws(tmp_path / f"m_{snake}", snake=snake)
        assert D.die_size(ws) == (100.0, 80.0)
        pm = dict(D.step_metrics(ws, "placement"))
        assert pm["HPWL"] == "1 234 µm"
        pw = dict(D.step_metrics(ws, "power_analysis"))
        assert "dynamic µW" in pw
        maps = D.find_maps(ws)
        assert [m.step for m in maps] == (
            ["06_placement_dreamplace", "09_legalization_dreamplace"]
            if snake else ["place_dreamplace", "legalization_dreamplace"]
        )
        assert maps[0].label == "density"
        assert "legalization" in maps[1].label
        mat = D.sta_matrix(ws)
        assert ("MAX_125", "Cworst") in mat["cells"]
        assert mat["cells"][("MAX_125", "Cworst")]["setup"] == 1.25
        paths, note = D.worst_paths(ws, "MAX_125", "Cworst")
        assert len(paths) == 1 and paths[0]["slack"] == 1.25 and "1 paths" in note


def test_health_insight_and_vacuous_antenna_modes(tmp_path):
    ws = _make_ws(tmp_path, snake=True)
    steps = D.station_defs([
        {"step": "post_route_lec", "status": "success", "checklist_status": "passed"}
    ])
    mat = D.sta_matrix(ws)
    qor = {
        "overall_score": 92.0,
        "qor_status": "GREEN",
        "dimensions": [{"dimension": "timing", "score": 92.0}],
        "qor": {"feasibility": {"gates": [
            {"id": "GATE_DRC", "state": "passed"},
            {"id": "GATE_LVS", "state": "passed"},
        ]}},
    }
    hl = {r[0]: r for r in D.health(ws, steps, mat, qor)["rows"]}
    assert hl["antenna"][2] == "ok" and hl["antenna"][3] == "clean"
    assert hl["lec"][2] == "ok" and hl["lec"][3] == "equivalent"
    assert D.insight(qor, list(hl.values())) == "All signoff checks look healthy."

    # 0 nets -> VACUOUS warning
    ant_path = ws / "15_antenna_check_ecc" / "feature" / "antenna_check.step.json"
    ant_path.write_text(json.dumps({"antenna_check": {"signal_net_count": 0, "pins_with_gate_area": 0, "violation_count": 0}}))
    hl0 = {r[0]: r for r in D.health(ws, steps, mat, qor)["rows"]}
    assert hl0["antenna"][2] == "warn" and hl0["antenna"][3] == "VACUOUS"
    assert "VACUOUS" in D.insight(qor, list(hl0.values()))
    assert D.card_summary({"project": str(ws.parent), "ws": "default", "stations": steps, "matrix": mat, "total_secs": 10})["antenna_vacuous"] is True

    # Non-zero nets with 0 gate-area pins -> VACUOUS warning
    ant_path.write_text(json.dumps({"antenna_check": {"signal_net_count": 361, "pins_with_gate_area": 0, "violation_count": 0}}))
    hl_nogate = {r[0]: r for r in D.health(ws, steps, mat, qor)["rows"]}
    assert hl_nogate["antenna"][2] == "warn" and "gate area" in hl_nogate["antenna"][4]
    assert "no pins had gate area" in D.insight(qor, list(hl_nogate.values()))
    assert D.card_summary({"project": str(ws.parent), "ws": "default", "stations": steps, "matrix": mat, "total_secs": 10})["antenna_vacuous"] is True

    # Real violations -> bad
    ant_path.write_text(json.dumps({"antenna_check": {"signal_net_count": 361, "pins_with_gate_area": 240, "violation_count": 4}}))
    hl_viol = {r[0]: r for r in D.health(ws, steps, mat, qor)["rows"]}
    assert hl_viol["antenna"][2] == "bad" and hl_viol["antenna"][3] == "4 viol"
