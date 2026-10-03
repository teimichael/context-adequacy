from __future__ import annotations
import time
from collections.abc import Iterable, Sequence
from enum import Enum
from pathlib import Path
from pydantic import BaseModel, ConfigDict
from lacuna import SCHEMA_VERSION
from lacuna.corpora.base import Instance, Status
from lacuna.criterion.closure import DIRECTIONS, DOWNSTREAM_KINDS, UPSTREAM_KINDS, ClosureResult, analyse, bfs
from lacuna.criterion.frame import TargetNotIndexed, fields_without_writers, frame
from lacuna.criterion.invariants import scan_target
from lacuna.criterion.properties import PROPERTY_KINDS, build_seed
from lacuna.criterion.verdict import Verdict, VerdictValue, decide
from lacuna.criterion.witness import Witness, canonical_witness
from lacuna.mapper import SourceIndex, hunk_lines
from lacuna.model.approx import LEVELS, Approximation, Property
from lacuna.model.edg import ElementDependenceGraph, UnknownReason
from lacuna.model.identity import EdgeKind, ElementId, ElementKind
from lacuna.tokens import missing_sources, tok

class TargetSource(str, Enum):
    ORACLE = 'oracle'

class TargetResolution(BaseModel):
    model_config = ConfigDict(frozen=True)
    target: str | None
    source: TargetSource
    candidates: tuple[str, ...] = ()
    pure_addition: bool = False
    note: str = ''
    no_java_change: bool = False
SOURCE_ROOT_MARKERS = ('src/main/java/', 'src/test/java/', 'src/main/kotlin/', 'src/test/kotlin/', 'generated-sources/')

def strip_source_root(path: str) -> str:
    for marker in SOURCE_ROOT_MARKERS:
        idx = path.find(marker)
        if idx >= 0:
            return path[idx + len(marker):]
    return path

def resolve_oracle_target(instance: Instance, index: SourceIndex, graph: ElementDependenceGraph) -> TargetResolution:
    if not instance.changed_java_files():
        return TargetResolution(target=None, source=TargetSource.ORACLE, pure_addition=instance.is_pure_addition(), note='the patch touches no main Java file', no_java_change=True)
    touched = hunk_lines(instance.fix_patch)
    candidates: set[str] = set()
    for path, lines in touched.items():
        if not path.endswith('.java'):
            continue
        candidates |= index.elements_covering(strip_source_root(path), lines)
    candidates = {c for c in candidates if c in graph}
    if not candidates:
        return TargetResolution(target=None, source=TargetSource.ORACLE, pure_addition=instance.is_pure_addition(), note='no patched line maps to an element identity in the graph')
    pure = instance.is_pure_addition()
    if pure:
        types = {str(ElementId(c).declaring_type) for c in candidates}
        types &= set(graph.nodes())
        if types:
            return TargetResolution(target=sorted(types, key=ElementId)[0], source=TargetSource.ORACLE, candidates=tuple(sorted(candidates)), pure_addition=True)
    methods = sorted((c for c in candidates if ElementId(c).kind is ElementKind.METHOD), key=ElementId)
    chosen = methods[0] if methods else sorted(candidates, key=ElementId)[0]
    return TargetResolution(target=chosen, source=TargetSource.ORACLE, candidates=tuple(sorted(candidates)), pure_addition=pure)

class InstanceRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_version: str
    instance_id: str
    corpus: str
    repo: str
    commit: str
    approximation: Approximation
    target_source: TargetSource
    property: Property
    status: Status
    target: str | None = None
    pure_addition: bool = False
    frame_size: int = 0
    crit_size: int = 0
    frame_tokens: int = 0
    seed_size: int = 0
    observation_points: int = 0
    property_applicable: bool = True
    applicability_scanned: bool = True
    invariant_kinds: dict[str, int] = {}
    closure_elements: int = 0
    closure_tokens: int = 0
    full_closure_elements: int = 0
    full_closure_tokens: int = 0
    level_elements: dict[str, int] = {}
    level_tokens: dict[str, int] = {}
    levels_match_cap: bool = True
    upstream_elements: int = 0
    upstream_tokens: int = 0
    downstream_elements: int = 0
    downstream_tokens: int = 0
    direction_elements: dict[str, int] = {}
    direction_tokens: dict[str, int] = {}
    ablation_elements: dict[str, int] = {}
    ablation_tokens: dict[str, int] = {}
    omit_elements: int = 0
    omit_tokens: int = 0
    depth: int | None = None
    depth_basis: str = 'seed'
    verdict: VerdictValue | None = None
    unknown_reasons: list[str] = []
    witness_distance: int | None = None
    witness_interior: bool | None = None
    witness_target: str | None = None
    context_elements: int = 0
    context_kind: str = 'none'
    unrenderable_in_closure: int = 0
    unreached_in_closure: int = 0
    writerless_frame_fields: int = 0
    bridges_truncated: int = 0
    shadowing_jars_dropped: list[str] = []
    escapes_fired: dict[str, int] = {}
    escapes_magnitude: dict[str, int] = {}
    seconds: dict[str, float] = {}
    note: str = ''

def reference_context(target: str, index: SourceIndex, graph: ElementDependenceGraph, kind: str) -> set[str]:
    if kind == 'none':
        return set()
    if kind != 'target-file':
        raise ValueError(f'unknown reference context {kind!r}')
    decl = index.by_element.get(target)
    if decl is None:
        return {target} if target in graph else set()
    same_file = {eid for eid, d in index.by_element.items() if d.source_file == decl.source_file and eid in graph}
    same_file.add(target)
    return same_file

def _sources_for(index: SourceIndex, elements: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for e in elements:
        s = index.source_of(e)
        if s is not None:
            out[e] = s
    return out

def evaluate_instance(instance: Instance, graph: ElementDependenceGraph, index: SourceIndex, program_index, approx: Approximation, *, prop: Property=Property.P1_CALLER_BEHAVIOUR, context: Iterable[str]=(), target: str | None=None, target_source: TargetSource=TargetSource.ORACLE, escapes_fired: dict[str, int] | None=None, element_reasons: dict[str, list[str]] | None=None, reference_context_kind: str | None=None, bridges_truncated: int=0, shadowing_jars_dropped: Sequence[str]=(), escapes_magnitude: dict[str, int] | None=None) -> tuple[InstanceRecord, ClosureResult | None, Verdict | None, Witness | None]:
    timings: dict[str, float] = {}
    t0 = time.time()
    resolution = TargetResolution(target=target, source=target_source) if target is not None else resolve_oracle_target(instance, index, graph)
    base = dict(schema_version=SCHEMA_VERSION, instance_id=instance.instance_id, corpus=instance.corpus, repo=instance.repo_key, commit=instance.base_commit, approximation=approx, target_source=resolution.source, property=prop, target=resolution.target, pure_addition=resolution.pure_addition, escapes_fired=escapes_fired or {}, escapes_magnitude=escapes_magnitude or {}, shadowing_jars_dropped=list(shadowing_jars_dropped))
    if resolution.target is None:
        unresolved = Status.NO_JAVA_CHANGE if resolution.no_java_change else Status.TARGET_UNMAPPABLE
        return (InstanceRecord(status=unresolved, note=resolution.note, **base), None, None, None)
    try:
        frame_set = frame(resolution.target, program_index, approx.frame)
    except TargetNotIndexed as exc:
        return (InstanceRecord(status=Status.TARGET_UNMAPPABLE, note=str(exc), **base), None, None, None)
    if reference_context_kind is not None:
        context = reference_context(resolution.target, index, graph, reference_context_kind)
    invariant_scan = None
    declared_invariants: frozenset[str] = frozenset()
    if prop is Property.P2_DECLARED_INVARIANT:
        invariant_scan = scan_target(index, graph, resolution.target)
        declared_invariants = invariant_scan.read_set
    seed = build_seed(graph, resolution.target, frame_set, prop, declared_invariants=declared_invariants)
    timings['seed'] = round(time.time() - t0, 3)
    kinds = PROPERTY_KINDS[prop]
    if not approx.summary_edges:
        kinds = frozenset(kinds - {EdgeKind.SUMMARY})
    t1 = time.time()
    result = analyse(graph, seed.seed, context, depth_cap=approx.depth_cap, levels=LEVELS, kinds=kinds, realizable=approx.realizable)
    timings['closure'] = round(time.time() - t1, 3)
    a3_distance = next((lv.max_distance for lv in LEVELS if lv.name == 'A3'), None)
    levels_match_cap = approx.depth_cap == a3_distance
    t1b = time.time()
    full = analyse(graph, seed.seed, context, depth_cap=None, levels=(), kinds=kinds, realizable=approx.realizable)
    timings['closure_uncapped'] = round(time.time() - t1b, 3)
    t1c = time.time()
    up = bfs(graph, seed.seed, kinds=frozenset(kinds & UPSTREAM_KINDS), realizable=approx.realizable).reached()
    down = bfs(graph, seed.seed, kinds=frozenset(kinds & DOWNSTREAM_KINDS), realizable=approx.realizable).reached()
    directed: dict[str, frozenset[str]] = {}
    ablated: dict[str, frozenset[str]] = {}
    if prop is Property.P1_CALLER_BEHAVIOUR:
        for _dname, _dkinds in DIRECTIONS.items():
            directed[_dname] = frozenset(bfs(graph, seed.seed, kinds=frozenset(kinds & _dkinds), realizable=approx.realizable).reached())
            ablated[_dname] = frozenset(bfs(graph, seed.seed, kinds=frozenset(kinds - _dkinds), realizable=approx.realizable).reached())
    timings['directed'] = round(time.time() - t1c, 3)
    t2 = time.time()
    _charged = result.slice_ | result.omit | full.slice_ | up | down
    for _els in (*directed.values(), *ablated.values()):
        _charged |= _els
    sources = _sources_for(index, _charged)
    closure_tokens = tok(result.slice_, sources)
    full_closure_tokens = tok(full.slice_, sources)
    level_tokens = {name: tok(els, sources) for name, els in result.levels.items()}
    omit_tokens = tok(result.omit, sources)
    timings['tokens'] = round(time.time() - t2, 3)
    run_reasons: set[UnknownReason] = set()
    target_record = graph.record(resolution.target)
    if target_record.in_callgraph is False:
        run_reasons.add(UnknownReason.ENTRY_POINTS)
    writerless = fields_without_writers(frame_set, graph)
    if writerless:
        run_reasons.add(UnknownReason.NO_WRITER)
    unreached_in_closure = sum((1 for e in result.slice_ if graph.record(e).in_callgraph is False))
    if unreached_in_closure:
        run_reasons.add(UnknownReason.ENTRY_POINTS)
    unrenderable = len(missing_sources(result.slice_, sources))
    if unrenderable:
        run_reasons.add(UnknownReason.UNMAPPABLE)
    if bridges_truncated:
        run_reasons.add(UnknownReason.BRIDGE_TRUNCATED)
    for element, names in (element_reasons or {}).items():
        if element == '__run__' or element in result.omit_dist:
            for n in names:
                try:
                    run_reasons.add(UnknownReason(n))
                except ValueError:
                    continue
    verdict = decide(result, depth_cap=approx.depth_cap, extra_reasons=frozenset(run_reasons))
    witness = canonical_witness(graph, result)
    timings['total'] = round(time.time() - t0, 3)
    record = InstanceRecord(status=Status.OK, frame_size=len(frame_set), crit_size=seed.crit_size, upstream_elements=len(up), upstream_tokens=tok(up, sources), downstream_elements=len(down), downstream_tokens=tok(down, sources), direction_elements={k: len(v) for k, v in directed.items()}, direction_tokens={k: tok(v, sources) for k, v in directed.items()}, ablation_elements={k: len(v) for k, v in ablated.items()}, ablation_tokens={k: tok(v, sources) for k, v in ablated.items()}, frame_tokens=tok([e for e in frame_set if e in graph], _sources_for(index, frame_set)), seed_size=len(seed.seed), observation_points=len(seed.observation_points), property_applicable=seed.applicable, applicability_scanned=invariant_scan.scanned if invariant_scan else True, invariant_kinds=invariant_scan.kind_counts() if invariant_scan else {}, closure_elements=len(result.slice_), closure_tokens=closure_tokens, full_closure_elements=len(full.slice_), full_closure_tokens=full_closure_tokens, levels_match_cap=levels_match_cap, level_elements={k: len(v) for k, v in result.levels.items()}, level_tokens=level_tokens, omit_elements=len(result.omit), omit_tokens=omit_tokens, depth=result.depth, depth_basis=result.depth_basis, verdict=verdict.value, unknown_reasons=[r.value for r in verdict.reasons], witness_distance=None if witness is None else witness.distance, witness_interior=None if witness is None else witness.interior, witness_target=None if witness is None else witness.target_omission, context_elements=len(result.context), context_kind=reference_context_kind or 'none', unrenderable_in_closure=unrenderable, unreached_in_closure=unreached_in_closure, writerless_frame_fields=len(writerless), bridges_truncated=bridges_truncated, seconds=timings, **base)
    return (record, result, verdict, witness)

def load_artifacts(edg_path: Path, index_path: Path, source_roots: Iterable[Path]):
    import json
    graph = ElementDependenceGraph.from_jsonl(edg_path)
    raw = json.loads(index_path.read_text())
    raw.pop('schema', None)
    from lacuna.model.index import ProgramIndex
    program_index = ProgramIndex.model_validate(raw)
    source_index = SourceIndex.parse_roots(source_roots)
    elements = [(n, graph.record(n).source_file, graph.record(n).start_line) for n in graph.nodes()]
    matched, unmatched = source_index.join_to_elements(elements)
    return (graph, program_index, source_index, matched, unmatched)
