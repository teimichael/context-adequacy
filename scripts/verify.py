"""Check package integrity, independence, size, and reported results."""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import pkgutil
import re
import subprocess
import sys

import lacuna

ROOT = Path(__file__).resolve().parents[1]
TOTAL_LIMIT = 50 * 1024 ** 2
FILE_LIMIT = 20 * 1024 ** 2


def ignored(path):
    parts = path.parts
    return (parts[0] in {".git", ".venv", "outputs", "dist"}
            or any(p in {"__pycache__", ".gradle", "build"} for p in parts)
            or path.suffix == ".pyc"
            or (parts[0] == "data" and len(parts) > 1 and
                (parts[1].startswith(("instances", "analysis", ".lacuna")))))


def inventory():
    files = []
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT)
        if ignored(rel):
            continue
        if p.is_symlink():
            raise ValueError(f"Submission contains a symlink: {rel}")
        if p.is_file():
            files.append(p)
    return sorted(files)


def audit(files):
    forbidden = ["mi" + "chael", "context" + "-adequacy", "by" + "def"]
    # Third-party snapshots and the pinned vocabulary retain their public contents.
    public_inputs = {"resources"}
    leaks = []
    for p in files:
        rel = p.relative_to(ROOT)
        if any(part in public_inputs for part in rel.parts):
            continue
        if p.suffix in {".parquet", ".pdf"}:
            continue
        data = p.read_text()
        if any(re.search(r"(?<![A-Za-z0-9])" + re.escape(word) + r"(?![A-Za-z0-9])", data.lower()) for word in forbidden):
            leaks.append(str(rel))
        if re.search(r"/(?:home|Users)/[A-Za-z0-9_.-]+/", data):
            leaks.append(str(rel))
    if leaks:
        raise ValueError(f"Identity or host-path leaks: {sorted(set(leaks))}")
    unwanted = {"ag" + "ent", "localise", "prompts", "logs", "pilot"}
    if any(any(part in unwanted for part in p.relative_to(ROOT).parts) for p in files):
        raise ValueError("Excluded artifacts are present")


def check_package():
    files = inventory()
    expected = {}
    for line in (ROOT / "CHECKSUMS.sha256").read_text().splitlines():
        digest, name = line.split("  ", 1)
        rel = Path(name)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("Manifest paths must stay within the package")
        expected[name] = digest
    actual_names = {str(p.relative_to(ROOT)) for p in files} - {"CHECKSUMS.sha256"}
    if actual_names != set(expected):
        raise ValueError(f"Manifest inventory mismatch: {sorted(actual_names ^ set(expected))}")
    for name, digest in expected.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Checksum mismatch: {name}")
    sizes = [(p.stat().st_size, str(p.relative_to(ROOT))) for p in files]
    total = sum(n for n, _ in sizes)
    if total > TOTAL_LIMIT or any(n > FILE_LIMIT for n, _ in sizes):
        raise ValueError("Submission exceeds the GitHub package size budget")
    audit(files)
    for module in pkgutil.walk_packages(lacuna.__path__, lacuna.__name__ + "."):
        importlib.import_module(module.name)
    from lacuna.experiment.config import ExperimentConfig
    from lacuna.corpora.multi_swe_bench import MultiSweBenchJava
    from lacuna.corpora.contextbench import ContextBench
    from lacuna.tokens import tokenizer
    ExperimentConfig.load(ROOT / "configs/full.yaml")
    corpus = MultiSweBenchJava(ROOT / "data/corpora/multi-swe-bench").stats()
    if (corpus.total_rows, corpus.valid) != (128, 108):
        raise ValueError("Benchmark snapshot counts changed")
    if len(list(ContextBench(ROOT / "data/corpora/contextbench").java())) != 57:
        raise ValueError("Annotation snapshot counts changed")
    tokenizer()
    print(f"Submission: {total:,} bytes ({total / 1024 ** 2:.2f} MiB), {len(files)} files")
    for size, name in sorted(sizes, reverse=True)[:5]:
        print(f"  {size:,} bytes  {name}")
    print("Checksums, imports, configuration, snapshots, tokenizer, size, and identity scan: passed")
    return total


def main():
    check_package()
    subprocess.run([sys.executable, "examples/run.py"], cwd=ROOT, check=True)
    subprocess.run([sys.executable, "scripts/reproduce.py"], cwd=ROOT, check=True)
    result = json.loads((ROOT / "outputs/retained/verification.json").read_text())
    if not result["ok"]:
        raise ValueError("Retained result verification failed")
    figure_data = ROOT / "outputs/retained/figure-data.json"
    expected = (ROOT / "reference/figure-data.sha256").read_text().strip()
    if hashlib.sha256(figure_data.read_bytes()).hexdigest() != expected:
        raise ValueError("Figure input data differs from the retained manuscript inputs")
    # Generated PDFs should contain anonymous, deliberately minimal metadata.
    for p in (ROOT / "outputs/retained").glob("*.pdf"):
        raw = p.read_bytes()
        if b"/Author" in raw or b"/CreationDate" in raw or b"/ModDate" in raw:
            raise ValueError(f"Unexpected identifying or timestamp metadata: {p.name}")
    print("Figure data and PDF metadata: passed")


if __name__ == "__main__":
    main()
