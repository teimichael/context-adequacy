# Replication package

This package supports the four RQs in **What Does Dependence-Closure Containment Cost? A Criterion for Code-Edit Contexts in Java Repositories**.
The default command regenerates reported results from included measurements. It needs no model service,
Docker, or parent checkout.

## Setup and reproduction

Use Python **3.11–3.13**; Python 3.13 was validated. On Linux:

```sh
cd supplementary
python3.13 -m venv .venv
.venv/bin/python -m pip install -c constraints.txt .
./reproduce.sh
./verify.sh
```

Installation needs access to public Python package repositories. Subsequent default
reproduction works offline. `poetry install --only main` is an alternative; then run
`LACUNA_PYTHON="$(poetry env info --executable)" ./verify.sh`.
Shell entry points locate the package and may be invoked from another directory.
`LACUNA_PYTHON` selects an interpreter when `.venv` is not used.
Figures embed DejaVu fonts and need no TeX installation; their input data and labels
reproduce the manuscript, while font appearance can differ from its TeX figures.

Expected outputs in `outputs/retained/`:

- `summary.json`, `annotations.json`, `patch-footprints.json`: derived results.
- `tab-gate.tex`, `tab-builders.tex`, `table-data.json`: both empirical tables.
- `fig-gate-saturation.pdf`, `fig-levers.pdf`, `figure-data.json`: both empirical figures and their inputs.
- `manuscript-values.json`, `verification.json`: comparisons of all 126 manuscript result macros and both tables. Success requires `ok: true` and empty difference objects.

`./verify.sh` also checks submission-file SHA-256 hashes, imports, configuration,
corpus counts, the pinned tokenizer, worked examples, original figure-input data,
PDF metadata, anonymity, and package size. Missing or inconsistent required inputs
cause a nonzero exit; partial default reproduction is not accepted.

## Contents and manuscript correspondence

| Location | Purpose |
| --- | --- |
| `src/lacuna/` | Criterion, builders, mapping, token accounting, execution harness, aggregation |
| `java/analyzer/`, `docker/` | WALA 1.6.10 analyzer and digest-pinned JDK 21/Gradle build images |
| `data/corpora/` | Reduced public snapshots: 128 benchmark pool rows, 108 candidates, 57 Java annotation tasks |
| `evidence/` | Read-only retained measurements and study configuration |
| `configs/full.yaml`, `scripts/` | Fresh-analysis settings and package-local commands |
| `examples/` | Executable pagination/report and slicing counterexamples |
| `reference/` | Manuscript values, table cells, original figure-input digest |

| Reported result | Inputs under `evidence/` |
| --- | --- |
| RQ1: closure cost and budget fit; Table 1, Figure 4 | `records.jsonl`, `funnel.jsonl`, `graph-stats.jsonl` |
| RQ2: sensitivity and traversal ablations; Figure 5 | `records.jsonl`, `ladder-failures.jsonl`, `horizons.json` |
| RQ3: omissions, blockers, controls, builders; Table 2 | `records.jsonl`, `sanity.jsonl`, `builders.json` |
| RQ4: annotation recall and patch footprints | `containment.jsonl`, `annotation-draws.jsonl`, `annotation-join.json`, `conformance.jsonl` |
| Bounded SDG comparison in the implementation | `crosscheck.jsonl`: five instances, three statement hops |

The original schema calls the annotation experiment “probe 3” and horizon
measurements “levers”; these identifiers do not denote additional experiments.
The 20 random draws per instance are retained for aggregation without graph caches.
Counts preserve applicable denominators. Public benchmark identities, necessary
issue text, patches, source identities, and attribution remain intact. Unused
snapshot fields, other languages, and author/workspace provenance are removed.
Public issue-log home paths beyond the evaluated 2,000-character issue prefix are
redacted; every evaluated issue prefix and executable patch is unchanged.

The included Qwen2.5-Coder-7B-Instruct tokenizer has SHA-256
`c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539`.
Its bytes are unchanged because it defines measurement costs. Results concern
computed dependence closures and structural containment, subject to the manuscript's
analysis limitations; they do not measure repair success or semantic completeness.

## Optional fresh analysis

Docker with Linux containers and network access is required. Local JDK/Gradle
installations are unnecessary. Public benchmark images use the `mswebench` namespace;
Maven/Gradle builds inside them run offline. Included snapshots fix instance selection,
while availability of the public images remains an external dependency.

```sh
./reproduce.sh --full --instance google__gson-1391  # one-instance pipeline check
./reproduce.sh --full                            # complete configuration
```

The full settings use four CPU cores per analysis, 8 GiB per analyzer, 6 GiB per
build, three parallel slots, 8 GiB host memory reserved, a 900 s analysis timeout,
and a 2400 s build timeout. At least 32 GiB host RAM is needed for that concurrency;
allow at least 200 GB free disk for images and regenerated caches. The sample uses
one slot. The runner checks resources, rejects overcommit, and holds an exclusive lock.

Fresh outputs go to `outputs/fresh/full/` or `outputs/fresh/sample/`; prepared code
and caches go under `data/instances*` and `data/analysis`. These directories are
excluded from the submission. The pipeline runs configured settings, controls,
four builders, footprints, annotations, and the bounded SDG check. Retained evidence
is never overwritten. Different images, dependencies, hardware, or retention outcomes
can change fresh counts and timings.

Default reproduction and a fresh one-instance pipeline are validated separately.
The complete 108-candidate fresh sweep was not rerun during package preparation.
Default checks certify correspondence with reported results; sample outputs describe
a new execution and do not certify the whole-corpus reference.

## Public provenance

Public inputs derive from [Multi-SWE-bench Java](https://huggingface.co/datasets/ByteDance-Seed/Multi-SWE-bench/tree/main/java),
[ContextBench](https://huggingface.co/datasets/Contextbench/ContextBench), and the
[Qwen tokenizer](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct/blob/main/tokenizer.json).
Required source notices remain in included patches. Third-party materials retain
their upstream terms; this package assigns no new license to them.
