from __future__ import annotations
import statistics
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from pydantic import BaseModel, ConfigDict
from lacuna.analysis.stats import describe
from lacuna.corpora.base import Instance, Status
from lacuna.criterion.closure import ClosureResult, analyse
from lacuna.criterion.frame import fields_without_writers, frame
from lacuna.criterion.properties import PROPERTY_KINDS, build_seed
from lacuna.criterion.verdict import Verdict, VerdictValue, decide
from lacuna.criterion.witness import Witness, canonical_witness
from lacuna.experiment.evaluate import InstanceRecord
from lacuna.model.approx import Approximation, Property
from lacuna.model.edg import ElementDependenceGraph, UnknownReason
from lacuna.model.identity import EdgeKind, ElementId
from lacuna.tokens import missing_sources, tok
SCHEMA = 'sanity/1'
FILENAME = 'sanity.jsonl'
DEFAULT_ITERATION_CAP = 10
P1 = Property.P1_CALLER_BEHAVIOUR

def property_kinds(prop: Property, approx: Approximation) -> frozenset[EdgeKind]:
    kinds = PROPERTY_KINDS[prop]
    if not approx.summary_edges:
        kinds = frozenset(kinds - {EdgeKind.SUMMARY})
    return kinds

def fixed_reasons(graph: ElementDependenceGraph, target: str, frame_set: Iterable[str], capped_slice: Iterable[str], sources: Mapping[str, str], *, bridges_truncated: int, element_reasons: Mapping[str, Sequence[str]]) -> frozenset[UnknownReason]:
    out: set[UnknownReason] = set()
    if graph.record(target).in_callgraph is False:
        out.add(UnknownReason.ENTRY_POINTS)
    if fields_without_writers(frame_set, graph):
        out.add(UnknownReason.NO_WRITER)
    slice_list = list(capped_slice)
    if any((graph.record(e).in_callgraph is False for e in slice_list)):
        out.add(UnknownReason.ENTRY_POINTS)
    if missing_sources(slice_list, sources):
        out.add(UnknownReason.UNMAPPABLE)
    if bridges_truncated:
        out.add(UnknownReason.BRIDGE_TRUNCATED)
    for name in element_reasons.get('__run__', ()):
        try:
            out.add(UnknownReason(name))
        except ValueError:
            continue
    return frozenset(out)

def closure_reasons(result: ClosureResult, element_reasons: Mapping[str, Sequence[str]]) -> frozenset[UnknownReason]:
    out: set[UnknownReason] = set(result.unknown_reasons)
    for element, names in element_reasons.items():
        if element == '__run__' or element not in result.omit_dist:
            continue
        for name in names:
            try:
                out.add(UnknownReason(name))
            except ValueError:
                continue
    return frozenset(out)

def verdict_for(result: ClosureResult, *, depth_cap: int, fixed: frozenset[UnknownReason], element_reasons: Mapping[str, Sequence[str]]) -> Verdict:
    return decide(result, depth_cap=depth_cap, extra_reasons=fixed | closure_reasons(result, element_reasons))

def verdict_without_escapes(result: ClosureResult, *, depth_cap: int) -> VerdictValue:
    clean = result.model_copy(update={'unknown_reasons': frozenset()})
    return decide(clean, depth_cap=depth_cap).value

@dataclass(frozen=True)
class Superset:
    context: frozenset[str]
    growth: tuple[int, ...]
    iterations: int
    converged: bool
    result: ClosureResult

def least_adequate_superset(graph: ElementDependenceGraph, seed: Iterable[str], *, kinds: frozenset[EdgeKind] | None, depth_cap: int | None, realizable: bool, start: Iterable[str]=(), iteration_cap: int=DEFAULT_ITERATION_CAP) -> Superset:
    seed_set = frozenset(seed)
    context = seed_set | frozenset(start)

    def step(c: frozenset[str]) -> ClosureResult:
        return analyse(graph, seed_set, c, depth_cap=depth_cap, kinds=kinds, realizable=realizable)
    growth = [len(context)]
    result = step(context)
    iterations = 0
    while result.omit and iterations < iteration_cap:
        context = context | result.omit
        iterations += 1
        growth.append(len(context))
        result = step(context)
    return Superset(context=context, growth=tuple(growth), iterations=iterations, converged=not result.omit, result=result)

@dataclass(frozen=True)
class SeededCheck:
    element: str | None
    distance: int | None
    omit: frozenset[str]
    verdict: VerdictValue | None
    witness: Witness | None
    omit_ok: bool | None
    witness_ok: bool | None

    @property
    def passed(self) -> bool | None:
        if self.omit_ok is None:
            return None
        return bool(self.omit_ok and self.witness_ok)

def pick_seeded(context: Iterable[str], seed: Iterable[str], dist: Mapping[str, int]) -> str | None:
    seed_set = frozenset(seed)
    candidates = [e for e in context if e not in seed_set]
    if not candidates:
        return None
    far = max((dist.get(e, -1) for e in candidates))
    return min((e for e in candidates if dist.get(e, -1) == far), key=ElementId)

def seeded_omission(graph: ElementDependenceGraph, seed: Iterable[str], superset: Superset, dist: Mapping[str, int], *, kinds: frozenset[EdgeKind] | None, depth_cap: int, realizable: bool) -> SeededCheck:
    seed_set = frozenset(seed)
    e = pick_seeded(superset.context, seed_set, dist)
    if e is None:
        return SeededCheck(None, None, frozenset(), None, None, None, None)
    r = analyse(graph, seed_set, superset.context - {e}, depth_cap=depth_cap, kinds=kinds, realizable=realizable)
    w = canonical_witness(graph, r)
    if superset.converged:
        omit_ok = r.omit == frozenset({e})
        witness_ok = w is not None and w.target_omission == e and w.interior
    else:
        omit_ok = e in r.omit and r.omit <= superset.result.omit | {e}
        witness_ok = w is not None and w.interior
    return SeededCheck(element=e, distance=dist.get(e), omit=r.omit, verdict=decide(r, depth_cap=depth_cap).value, witness=w, omit_ok=omit_ok, witness_ok=witness_ok)

class SanityRow(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_version: str = SCHEMA
    instance_id: str
    repo: str
    analysis_key: str
    target: str
    seed_elements: int
    iteration_cap: int
    record_verdict: str | None
    record_unknown_reasons: list[str]
    reproduces_record: bool
    reproduction_mismatches: list[str]
    closure_elements: int
    full_closure_elements: int
    full_closure_scope_counts: dict[str, int]
    full_closure_kind_counts: dict[str, int]
    full_closure_callgraph_methods: int
    iterations: int
    converged: bool
    growth: list[int]
    superset_elements: int
    superset_tokens: int
    superset_unrenderable: int
    superset_omit_elements: int
    superset_within_full_closure: bool
    verdict: str
    unknown_reasons: list[str]
    fixed_reasons: list[str]
    closure_only_reasons: list[str]
    verdict_without_escapes: str
    truncated: bool
    seeded_element: str | None
    seeded_distance: int | None
    seeded_omit_elements: int | None
    seeded_omit_is_singleton: bool | None
    seeded_omit_contains_element: bool | None
    seeded_verdict: str | None
    seeded_witness_target: str | None
    seeded_witness_distance: int | None
    seeded_witness_interior: bool | None
    seeded_witness_rooted_in_context: bool | None
    seeded_pass: bool | None
    seconds: float

def _composition(graph: ElementDependenceGraph, elements: Iterable[str]) -> tuple[dict[str, int], dict[str, int], int]:
    scopes: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    reached = 0
    for e in elements:
        rec = graph.record(e)
        scopes[rec.scope] += 1
        k = {'T': 'type', 'M': 'method', 'F': 'field'}.get(e[:1], 'other')
        kinds[k] += 1
        if k == 'method' and rec.in_callgraph is True:
            reached += 1
    return (dict(sorted(scopes.items())), dict(sorted(kinds.items())), reached)

class NotReproducible(ValueError):
    pass

def run_instance(instance: Instance, record: InstanceRecord, analysis_dir: Path, source_roots: Sequence[Path], approx: Approximation, *, reference_kind: str, iteration_cap: int=DEFAULT_ITERATION_CAP, loader: Callable[..., Any] | None=None) -> SanityRow:
    from lacuna.experiment.evaluate import _sources_for, load_artifacts, reference_context, resolve_oracle_target
    from lacuna.harness.analyze import AnalysisResult
    t0 = time.time()
    res = AnalysisResult.model_validate_json((analysis_dir / 'result.json').read_text())
    load = loader or load_artifacts
    graph, pidx, sidx, _, _ = load(analysis_dir / 'edg.jsonl', analysis_dir / 'index.json', list(source_roots))
    resolution = resolve_oracle_target(instance, sidx, graph)
    if resolution.target is None:
        raise NotReproducible(f'{instance.instance_id}: target unmappable: {resolution.note}')
    target = resolution.target
    frame_set = frame(target, pidx, approx.frame)
    seed = build_seed(graph, target, frame_set, P1).seed
    kinds = property_kinds(P1, approx)
    k = approx.depth_cap
    reasons_by_element: dict[str, list[str]] = dict(res.element_reasons or {})
    bridges = int((res.stats or {}).get('bridges_truncated', 0))
    ref_ctx = reference_context(target, sidx, graph, reference_kind)
    ref = analyse(graph, seed, ref_ctx, depth_cap=k, kinds=kinds, realizable=approx.realizable)
    capped_slice = ref.slice_
    fixed = fixed_reasons(graph, target, frame_set, capped_slice, _sources_for(sidx, capped_slice), bridges_truncated=bridges, element_reasons=reasons_by_element)
    ref_verdict = verdict_for(ref, depth_cap=k, fixed=fixed, element_reasons=reasons_by_element)
    full = analyse(graph, seed, (), depth_cap=None, kinds=kinds, realizable=approx.realizable)
    mismatches: list[str] = []
    for name, ours, theirs in [('target', target, record.target), ('seed_size', len(seed), record.seed_size), ('closure_elements', len(capped_slice), record.closure_elements), ('full_closure_elements', len(full.slice_), record.full_closure_elements), ('context_elements', len(ref.context), record.context_elements), ('omit_elements', len(ref.omit), record.omit_elements), ('verdict', ref_verdict.value, record.verdict), ('unknown_reasons', sorted((r.value for r in ref_verdict.reasons)), sorted(record.unknown_reasons))]:
        if ours != theirs:
            mismatches.append(name)
    sup = least_adequate_superset(graph, seed, kinds=kinds, depth_cap=k, realizable=approx.realizable, iteration_cap=iteration_cap)
    sup_verdict = verdict_for(sup.result, depth_cap=k, fixed=fixed, element_reasons=reasons_by_element)
    sup_sources = _sources_for(sidx, sup.context)
    seeded = seeded_omission(graph, seed, sup, full.slice_dist, kinds=kinds, depth_cap=k, realizable=approx.realizable)
    scope_counts, kind_counts, reached = _composition(graph, full.slice_)
    closure_only = closure_reasons(sup.result, reasons_by_element) - fixed
    w = seeded.witness
    return SanityRow(instance_id=instance.instance_id, repo=record.repo, analysis_key=analysis_dir.name, target=target, seed_elements=len(seed), iteration_cap=iteration_cap, record_verdict=None if record.verdict is None else record.verdict.value, record_unknown_reasons=sorted(record.unknown_reasons), reproduces_record=not mismatches, reproduction_mismatches=mismatches, closure_elements=len(capped_slice), full_closure_elements=len(full.slice_), full_closure_scope_counts=scope_counts, full_closure_kind_counts=kind_counts, full_closure_callgraph_methods=reached, iterations=sup.iterations, converged=sup.converged, growth=list(sup.growth), superset_elements=len(sup.context), superset_tokens=tok(sup.context, sup_sources), superset_unrenderable=len(missing_sources(sup.context, sup_sources)), superset_omit_elements=len(sup.result.omit), superset_within_full_closure=sup.context <= full.slice_, verdict=sup_verdict.value.value, unknown_reasons=[r.value for r in sup_verdict.reasons], fixed_reasons=sorted((r.value for r in fixed)), closure_only_reasons=sorted((r.value for r in closure_only)), verdict_without_escapes=verdict_without_escapes(sup.result, depth_cap=k).value, truncated=sup.result.slice_truncated or sup.result.omit_truncated, seeded_element=seeded.element, seeded_distance=seeded.distance, seeded_omit_elements=None if seeded.element is None else len(seeded.omit), seeded_omit_is_singleton=None if seeded.element is None else seeded.omit == frozenset({seeded.element}), seeded_omit_contains_element=None if seeded.element is None else seeded.element in seeded.omit, seeded_verdict=None if seeded.verdict is None else seeded.verdict.value, seeded_witness_target=None if w is None else w.target_omission, seeded_witness_distance=None if w is None else w.distance, seeded_witness_interior=None if w is None else w.interior, seeded_witness_rooted_in_context=None if w is None else w.rooted_in_context, seeded_pass=seeded.passed, seconds=round(time.time() - t0, 2))

def base_p1_ok_records(records: Iterable[InstanceRecord], base_digest: str) -> dict[str, InstanceRecord]:
    from lacuna.analysis.graph_stats import base_p1_records
    return {iid: r for iid, r in base_p1_records(records, base_digest).items() if r.status is Status.OK}

@dataclass
class SanityRun:
    rows: list[SanityRow]
    missing: list[str]
    failed: list[tuple[str, str]]
    budgets: list[int]

def run(results_dir: Path, data_dir: Path, *, corpus: str='multi-swe-bench', iteration_cap: int=DEFAULT_ITERATION_CAP, only: Iterable[str] | None=None, on_row: Callable[[SanityRow], None] | None=None) -> SanityRun:
    from lacuna.analysis.graph_stats import resolved_config
    from lacuna.corpora import CORPORA
    from lacuna.experiment.runner import read_records
    from lacuna.harness.analyze import NoCachedAnalysis, resolve_cached_analysis
    from lacuna.harness.prepare import Paths
    results_dir, data_dir = (Path(results_dir), Path(data_dir))
    cfg = resolved_config(results_dir)
    _, base = cfg.resolved_approximations()[0]
    records = base_p1_ok_records(read_records(results_dir / 'records.jsonl'), base.digest())
    if only is not None:
        wanted = set(only)
        records = {k: v for k, v in records.items() if k in wanted}
    instances = {i.instance_id: i for i in CORPORA[corpus](data_dir / 'corpora' / corpus).instances()}
    out = SanityRun(rows=[], missing=[], failed=[], budgets=list(cfg.budgets))
    for iid, rec in sorted(records.items()):
        inst = instances.get(iid)
        if inst is None:
            out.failed.append((iid, f'not an instance of {corpus}'))
            continue
        prepared = data_dir / 'instances' / iid
        try:
            d = resolve_cached_analysis(iid, rec.commit, prepared, base, data_dir / 'analysis')
        except NoCachedAnalysis:
            out.missing.append(iid)
            continue
        try:
            row = run_instance(inst, rec, d, [Paths(prepared).sources], base, reference_kind=cfg.reference_context, iteration_cap=iteration_cap)
        except NotReproducible as exc:
            out.failed.append((iid, str(exc)))
            continue
        out.rows.append(row)
        if on_row is not None:
            on_row(row)
    return out

def write_rows(rows: Sequence[SanityRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join((r.model_dump_json() + '\n' for r in rows)))

def read_rows(path: Path) -> list[SanityRow]:
    return [SanityRow.model_validate_json(line) for line in Path(path).read_text().splitlines() if line.strip()]

def _rate(num: int, den: int) -> float | None:
    return None if not den else num / den

def summarise(rows: Sequence[SanityRow], budgets: Sequence[int]=()) -> dict[str, Any]:
    n = len(rows)
    converged = [r for r in rows if r.converged]
    seeded = [r for r in rows if r.seeded_pass is not None]
    tokens = [float(r.superset_tokens) for r in converged]
    fixed_freq: Counter[str] = Counter((x for r in rows for x in r.fixed_reasons))
    closure_freq: Counter[str] = Counter((x for r in rows for x in r.closure_only_reasons))
    ratio = [r.superset_elements / r.full_closure_elements for r in converged if r.full_closure_elements]
    return {'rows': n, 'reproduces_record': sum((1 for r in rows if r.reproduces_record)), 'reproduction_mismatches': dict(sorted(Counter((m for r in rows for m in r.reproduction_mismatches)).items())), 'converged': len(converged), 'convergence_rate': _rate(len(converged), n), 'iterations': describe([float(r.iterations) for r in rows]), 'verdicts': dict(sorted(Counter((r.verdict for r in rows)).items())), 'verdicts_without_escapes': dict(sorted(Counter((r.verdict_without_escapes for r in rows)).items())), 'no_context_adequate': sum((1 for r in rows if r.fixed_reasons)), 'fixed_reason_frequency': dict(sorted(fixed_freq.items())), 'closure_only_reason_frequency': dict(sorted(closure_freq.items())), 'superset_elements': describe([float(r.superset_elements) for r in converged]), 'superset_tokens': describe(tokens), 'superset_median_tokens': statistics.median(tokens) if tokens else None, 'superset_over_full_closure': describe(ratio), 'superset_within_budget': {str(b): _rate(sum((1 for t in tokens if t <= b)), len(tokens)) for b in budgets}, 'superset_with_unrenderable': sum((1 for r in rows if r.superset_unrenderable)), 'superset_within_full_closure': sum((1 for r in rows if r.superset_within_full_closure)), 'seeded_checked': len(seeded), 'seeded_pass': sum((1 for r in seeded if r.seeded_pass)), 'seeded_pass_rate': _rate(sum((1 for r in seeded if r.seeded_pass)), len(seeded)), 'seeded_singleton': sum((1 for r in seeded if r.seeded_omit_is_singleton)), 'seeded_witness_interior': sum((1 for r in seeded if r.seeded_witness_interior)), 'seeded_witness_rooted_in_context': sum((1 for r in seeded if r.seeded_witness_rooted_in_context))}
