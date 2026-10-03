"""Regenerate reported measurements without accessing the parent repository."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys

import yaml

from lacuna.analysis import report
from lacuna.analysis.figures import figure_gate_saturation, figure_levers, lever_rows
from lacuna.analysis.gold_context import (
    ContainmentResult, InstanceBaseline, JoinReport, aggregate,
)
from lacuna.analysis.manuscript import values_tex
from lacuna.analysis.stats import cluster_bootstrap
from lacuna.corpora.contextbench import ContextBench
from lacuna.corpora.multi_swe_bench import MultiSweBenchJava
from lacuna.experiment.conformance import ConformanceRecord, summarise
from lacuna.experiment.funnel import Funnel
from lacuna.experiment.runner import read_records

ROOT = Path(__file__).resolve().parents[1]


def read(path: Path):
    return json.loads(path.read_text())


def lines(path: Path):
    return [json.loads(s) for s in path.read_text().splitlines() if s.strip()]


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def finite(value):
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def macro_values(text):
    result = {}
    for match in re.finditer(r"\\(?:newcommand|renewcommand)\{\\(result\w+)\}\{", text):
        i = match.end()
        start, depth = i, 1
        while depth:
            depth += (text[i] == "{") - (text[i] == "}")
            i += 1
        result[match[1]] = text[start:i - 1]
    return result


def derived_values(summary, probe, records, funnel):
    """Additional scalars in the current monolithic manuscript."""
    base = report._base_cell(report._ok(records), report.configured_base_digest(ROOT / "evidence"))
    p1 = [r for r in base if r.property.value == "P1"]
    statuses = {e.instance_id: e.status.value for e in funnel.entries}
    resolved_callers = [r for r in p1 if r.crit_size and statuses[r.instance_id] == "OK"]
    sn, gate = summary["sanity"], summary["gate"]
    n = len(p1)
    values = {
        "resultPoolN": MultiSweBenchJava(ROOT / "data/corpora/multi-swe-bench").stats().total_rows,
        "resultPureAdditionN": sum(r.pure_addition for r in p1),
        "resultAttritionBuild": summary["funnel"]["by_status"].get("BUILD_FAILED", 0),
        "resultAttritionTarget": summary["funnel"]["by_status"].get("TARGET_UNMAPPABLE", 0),
        "resultAttritionNoJava": summary["funnel"]["by_status"].get("NO_JAVA_CHANGE", 0),
        "resultControlIterMedian": sn["iterations"]["median"],
        "resultControlIterMax": sn["iterations"]["max"],
        "resultControlsWithinEightK": round(sn["superset_within_budget"]["8000"] * n),
        "resultControlsWithinThirtyTwoK": round(sn["superset_within_budget"]["32000"] * n),
        "resultControlsWithinOneTwentyEightK": round(sn["superset_within_budget"]["128000"] * n),
        "resultResolvedCallersN": len(resolved_callers),
        "resultResolvedCallersBigN": sum(r.repo == "fasterxml/jackson-databind" for r in resolved_callers),
        "resultRepoMinMedian": min(v["tokens_median"] for v in gate["saturation"]["per_repo"].values() if v["n"] >= 3),
        "resultCappedMedian": gate["A3"]["median"],
        "resultCappedWithinOneTwentyEightKn": round(gate["A3"]["within_128000"] * n),
        "resultMainNoEntry": n - next(v["n_instances"] for v in summary["sensitivity"].values() if v["entry_points"] == "declared-mains"),
        "resultCfaRatio": next(v["closure_tokens_ratio_median"] for v in summary["sensitivity"].values() if v["cg"] == "0-CFA"),
        "resultHorizonMaxReduction": 100 * (1 - summary["levers"]["horizon_one_ratio_min"]),
        "resultCrosscheckElements": summary["crosscheck"]["sdg_elements_total"],
    }
    values["resultControlsOverOneTwentyEightK"] = n - values["resultControlsWithinOneTwentyEightK"]
    if probe.get("reportable"):
        values.update({
            "resultProbeThreeContained": probe["contained_total"],
            "resultProbeThreeUnconvertible": probe["unconvertible_blocks"],
            "resultProbeThreeRecallGain": 100 * (probe["recall_pooled"] - probe["random_baseline"]["pools"]["indexed"]["recall_pooled"]),
            "resultRandomExpectedRecall": 100 * probe["random_baseline"]["pools"]["indexed"]["recall_pooled_expected"],
            "resultChanceExpected": probe["n"] / (probe["random_baseline"]["draws_per_instance"] + 1),
            "resultPrecisionBound": 100 * probe["contained_total"] / (math.ceil(probe["n"] / 2) * probe["closure_elements_median"]),
        })
    return values


def format_values(values, expected):
    out = {}
    for key, value in values.items():
        reference = expected[key]
        if isinstance(value, str):
            out[key] = value
        elif "." in reference and reference.replace(",", "").replace(".", "", 1).isdigit():
            out[key] = f"{value:.{len(reference.split('.')[-1])}f}"
        else:
            out[key] = f"{value:,.0f}"
    return out


def interval_number(value):
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1000:
        return f"{value / 1000:.0f}K"
    return f"{value:.0f}"


def table_rows(records, funnel, builders, base_digest):
    base = report._base_cell(report._ok(records), base_digest)
    p1 = [r for r in base if r.property.value == "P1"]
    groups = [("edit frame", p1, "frame_tokens"), ("seed", p1, "seed")]
    groups += [(f"full $P_{p}$", [r for r in base if r.property.value == f"P{p}" and r.property_applicable], "full_closure_tokens") for p in (1, 2, 3)]
    gate = {}
    for label, rows, field in groups:
        costs = [r.level_tokens["A0"] if field == "seed" else getattr(r, field) for r in rows]
        if not costs:
            continue
        ci = cluster_bootstrap(costs, [r.repo for r in rows])
        fits = [sum(c <= b for c in costs) for b in (8000, 32000, 128000, 1000000)]
        gate[label] = [str(len(rows)), f"{statistics.median(costs):,.0f}", f"[{interval_number(ci.low)}, {interval_number(ci.high)}]", *[f"{count} ({100 * count / len(rows):.0f})" for count in fits]]
    caller_ids = {r.instance_id for r in p1 if r.crit_size}
    labels = [("target-only", "target member only"), ("lexical-bm25", "BM25 over the issue text"), ("dependence-1hop", "1-hop dependence expansion"), ("closure-first", r"closure-first$^\ddagger$")]
    cells = {(c["builder"], int(c["budget"])): c for c in builders}
    builder_rows = {}
    for builder, label in labels:
        selected = [cells[(builder, b)] for b in (8000, 32000, 128000)]
        builder_rows[label] = [f"{sum(r >= 1 for r in c['seed_recall'].values())} ({sum(r >= 1 for iid, r in c['seed_recall'].items() if iid in caller_ids)})" for c in selected] + [f"{100 * statistics.median(c['seed_recall'].values()):.0f}" for c in selected]
    return {"tab:gate": gate, "tab:builders": builder_rows}


def expected_table_rows(text):
    rows = {}
    for line in text.splitlines():
        if " & " in line and not line.lstrip().startswith(("\\", "&")) and line.rstrip().endswith(r"\\"):
            cells = line.rstrip()[:-2].strip().split(" & ")
            rows[cells[0]] = cells[1:]
    return rows


def build_report(results_dir: Path, out_dir: Path, *, retained=False, compare=False):
    out_dir.mkdir(parents=True, exist_ok=True)
    records = read_records(results_dir / "records.jsonl")
    funnel = Funnel.read(results_dir / "funnel.jsonl")
    digest = report.configured_base_digest(results_dir)
    budgets = read(results_dir / "config.resolved.json")["budgets"]
    horizons = read(results_dir / "horizons.json") if retained else read(results_dir / "report/levers.json")
    builders = read(results_dir / "builders.json") if retained else read(results_dir / "report/adequacy-budget.json")
    conformance = summarise([ConformanceRecord.model_validate(r) for r in lines(results_dir / "conformance.jsonl")])
    if retained:
        probe = aggregate([ContainmentResult.model_validate(r) for r in lines(results_dir / "containment.jsonl")], JoinReport.model_validate(read(results_dir / "annotation-join.json")), [InstanceBaseline.model_validate(r) for r in lines(results_dir / "annotation-draws.jsonl")])
    else:
        probe = read(results_dir / "report/probe3.json")
    summ = report.summary(records, funnel, budgets, digest, report.read_crosscheck(results_dir))
    summ["levers"] = report.lever_summary(horizons)
    summ["builders"] = report.builder_summary(builders, {r.instance_id: r.crit_size for r in report._base_cell(report._ok(records), digest) if r.property.value == "P1"})
    summ["graph"] = report.graph_summary(results_dir / "graph-stats.jsonl")
    summ["sanity"] = report.sanity_summary(results_dir / "sanity.jsonl")
    summ["conformance_d34"] = conformance
    write(out_dir / "summary.json", finite(summ))
    write(out_dir / "annotations.json", finite(probe))
    write(out_dir / "patch-footprints.json", conformance)
    rows = table_rows(records, funnel, builders, digest)
    write(out_dir / "table-data.json", rows)
    for name, table in rows.items():
        ncols = len(next(iter(table.values()))) + 1
        tex = "\\begin{tabular}{l" + "r" * (ncols - 1) + "}\n" + "\n".join(" & ".join([label, *cells]) + r" \\" for label, cells in table.items()) + "\n\\end{tabular}\n"
        (out_dir / (name.replace(":", "-") + ".tex")).write_text(tex)
    base_p1 = [r for r in report._base_cell(report._ok(records), digest) if r.property.value == "P1"]
    figure_gate_saturation(records, funnel, out_dir / "fig-gate-saturation.pdf", digest, budgets)
    figure_levers(records, out_dir / "fig-levers.pdf", digest, horizons, gate_factor=statistics.median(r.full_closure_tokens for r in base_p1) / 32000)
    write(out_dir / "figure-data.json", finite({"gate": [{"instance_id": r.instance_id, "repo": r.repo, "property": r.property.value, "frame_tokens": r.frame_tokens, "seed_tokens": r.level_tokens.get("A0", 0), "full_closure_tokens": r.full_closure_tokens, "full_closure_elements": r.full_closure_elements} for r in report._base_cell(report._ok(records), digest)], "levers": lever_rows(records, digest, horizons)}))
    if compare:
        expected = read(ROOT / "reference/manuscript-values.json")
        generated = macro_values(values_tex(summ, probe, horizons, builders))
        generated.update(format_values(derived_values(summ, probe, records, funnel), expected))
        # The manuscript joins these repository summaries with "and".
        generated["resultAttritionWhere"] = generated["resultAttritionWhere"].replace("; ", " and ")
        generated = {k: generated[k] for k in expected}
        differences = {k: {"expected": expected[k], "actual": generated[k]} for k in expected if expected[k] != generated[k]}
        tables = read(ROOT / "reference/tables.json")
        table_differences = {k: {"expected": expected_table_rows(tables[k]), "actual": rows[k]} for k in tables if expected_table_rows(tables[k]) != rows[k]}
        write(out_dir / "manuscript-values.json", generated)
        write(out_dir / "verification.json", {"ok": not differences and not table_differences, "scalars_checked": len(expected), "tables_checked": len(tables), "scalar_differences": differences, "table_differences": table_differences})
        if differences or table_differences:
            raise ValueError(f"manuscript comparison failed; inspect {out_dir.name}/verification.json")
    return summ


def cli(*args):
    subprocess.run([sys.executable, "-c", "from lacuna.cli import app; app()", *map(str, args)], cwd=ROOT, check=True)


def full(instance=None):
    subprocess.run(["bash", "scripts/build_analyzer.sh"], cwd=ROOT, check=True)
    config = read_config = yaml.safe_load((ROOT / "configs/full.yaml").read_text())
    if instance:
        config = {**read_config, "name": "sample", "instances": [instance]}
        config["profile"] = {**config["profile"], "parallel_slots": 1}
    path = ROOT / "outputs/fresh-config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    results = ROOT / config["results_dir"] / config["name"]
    cli("experiment", "run", path)
    cli("experiment", "conformance", path, "--cached-only")
    subprocess.run([sys.executable, "scripts/measure_horizons.py", str(results / "report"), str(path)], cwd=ROOT, check=True)
    cli("adequacy-budget", results)
    cli("experiment", "graph-stats", results)
    cli("experiment", "sanity", results)
    cli("annotations", "containment", results)
    build_report(results, results / "report")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="Rerun analysis using Docker and public images.")
    parser.add_argument("--instance", help="With --full, validate one retained instance instead of the sweep.")
    args = parser.parse_args()
    os.chdir(ROOT)
    if args.instance and not args.full:
        parser.error("--instance requires --full")
    if args.full:
        result = full(args.instance)
        print(f"Fresh analysis outputs: {result.relative_to(ROOT)}")
    else:
        build_report(ROOT / "evidence", ROOT / "outputs/retained", retained=True, compare=True)
        print("Reproduced retained results; all 126 manuscript scalars and both tables match.")


if __name__ == "__main__":
    main()
