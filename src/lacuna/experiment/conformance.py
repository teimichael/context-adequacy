from __future__ import annotations
import statistics
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any
from pydantic import BaseModel, ConfigDict, Field
from lacuna.corpora.base import ANALYSABLE, Instance, Status
from lacuna.criterion.closure import analyse
from lacuna.criterion.conformance import check, introduced_references, references
from lacuna.criterion.frame import frame
from lacuna.criterion.properties import PROPERTY_KINDS, build_seed
from lacuna.experiment.evaluate import load_artifacts, reference_context, resolve_oracle_target, strip_source_root
from lacuna.harness.analyze import NoCachedAnalysis, analyse_instance, resolve_cached_analysis
from lacuna.harness.prepare import Paths, prepare
from lacuna.mapper import SourceIndex
from lacuna.model.approx import Approximation, Property
from lacuna.model.edg import ElementDependenceGraph
from lacuna.model.index import ProgramIndex
LISTED = 50
CONTEXTS: dict[str, str] = {'slice_capped': 'Cl(seed) under A.depth_cap: the capped seed closure', 'slice': "Cl(seed) uncapped: Def. 3.5's Slice, the full seed closure", 'reference': "the reference context the verdicts are computed against (the target's own file)"}

class ContextConformance(BaseModel):
    model_config = ConfigDict(frozen=True)
    context_elements: int = Field(ge=0)
    escaping_touched: int = Field(ge=0)
    escaping_introduced: int = Field(ge=0)
    escaping_touched_test: int = Field(default=0, ge=0)
    conformant_touched: bool
    conformant_introduced: bool
    edited_outside: int = Field(default=0, ge=0)
    escaping: tuple[str, ...] = ()

class Footprint(BaseModel):
    model_config = ConfigDict(frozen=True)
    touched: int = Field(ge=0)
    touched_created: int = Field(default=0, ge=0)
    refs_touched: int = Field(ge=0)
    refs_introduced: int = Field(ge=0)
    refs_created: int = Field(default=0, ge=0)
    contexts: dict[str, ContextConformance] = {}

class ConformanceRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    instance_id: str
    repo: str
    status: Status
    target: str | None = None
    note: str = ''
    footprint: Footprint | None = None

def added_lines(patch: str) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    current: str | None = None
    new_line = 0
    old_left = new_left = 0
    for raw in patch.splitlines():
        if old_left > 0 or new_left > 0:
            if raw.startswith('+'):
                if current is not None:
                    out.setdefault(current, []).append(new_line)
                new_line += 1
                new_left -= 1
            elif raw.startswith('-'):
                old_left -= 1
            elif raw.startswith('\\'):
                pass
            else:
                new_line += 1
                old_left -= 1
                new_left -= 1
            continue
        if raw.startswith('diff --git '):
            current = None
        elif raw.startswith('+++ '):
            name = raw[4:].strip()
            current = None if name == '/dev/null' else name[2:] if name.startswith('b/') else name
        elif raw.startswith('@@'):
            try:
                old_seg, new_seg = raw.split(' ')[1:3]
                o = old_seg[1:].split(',')
                n = new_seg[1:].split(',')
                old_left = int(o[1]) if len(o) > 1 else 1
                new_line = int(n[0])
                new_left = int(n[1]) if len(n) > 1 else 1
            except (IndexError, ValueError):
                old_left = new_left = 0
    return {k: sorted(set(v)) for k, v in out.items()}

def touched_elements(instance: Instance, pre_image: Iterable[str], patched_graph: ElementDependenceGraph, patched_index: SourceIndex) -> tuple[set[str], set[str]]:
    declared = set(pre_image)
    added = added_lines(instance.fix_patch)
    by_file: dict[str, list[tuple[int, str]]] = {}
    for n in patched_graph.nodes():
        rec = patched_graph.record(n)
        if n.startswith('M:') and rec.source_file and (rec.start_line is not None):
            by_file.setdefault(rec.source_file, []).append((rec.start_line, n))
    compiled: set[str] = set()
    for path, lines in added.items():
        if not path.endswith('.java'):
            continue
        rel = strip_source_root(path)
        declared |= patched_index.elements_covering(rel, lines)
        wanted = set(lines)
        for f, members in by_file.items():
            if f == rel or f.endswith('/' + rel) or rel.endswith('/' + f):
                compiled |= {m for line, m in members if line in wanted and m not in patched_index.by_element}
    for e in declared:
        if e.startswith('F:'):
            owner = e[2:].split(':', 1)[0].rsplit('.', 1)[0]
            compiled |= {m for m in (f'M:{owner}#<clinit>()V',) if m in patched_graph} | {n for n in patched_graph.nodes() if n.startswith(f'M:{owner}#<init>(')}
    declared = {e for e in declared if e in patched_graph}
    return (declared, compiled - declared)

def footprint(instance: Instance, *, target: str, pre_image: Iterable[str], graph: ElementDependenceGraph, patched_graph: ElementDependenceGraph, patched_index: SourceIndex, contexts: dict[str, set[str]]) -> Footprint:
    pre = set(pre_image) | {target}
    declared, compiled = touched_elements(instance, pre, patched_graph, patched_index)
    touched = declared | compiled
    upper = references(patched_graph, touched)
    lower = introduced_references(patched_graph, graph, touched)
    created = {r for r in upper | lower if r not in graph}
    upper_base = upper - created
    lower_base = lower - created
    edited = {e for e in declared | pre if e in graph}

    def scope(e: str) -> str:
        return graph.record(e).scope
    checks: dict[str, ContextConformance] = {}
    for name, ctx in contexts.items():
        hi = check(upper_base, ctx)
        lo = check(lower_base, ctx)
        checks[name] = ContextConformance(context_elements=len(ctx), escaping_touched=len(hi.escaping_refs), escaping_introduced=len(lo.escaping_refs), escaping_touched_test=sum((1 for e in hi.escaping_refs if scope(e) == 'test')), conformant_touched=hi.conformant, conformant_introduced=lo.conformant, edited_outside=len(edited - ctx), escaping=hi.escaping_refs[:LISTED])
    predicted = contexts.get('slice_capped', set())
    return Footprint(touched=len(touched), touched_created=sum((1 for e in touched if e not in graph)), refs_touched=len(upper), refs_introduced=len(lower), refs_created=len(created), contexts=checks)

def _from_artifacts(instance: Instance, approx: Approximation, *, graph: ElementDependenceGraph, program_index: ProgramIndex, source_index: SourceIndex, patched_graph: ElementDependenceGraph, patched_index: SourceIndex, prop: Property, reference_context_kind: str) -> ConformanceRecord:
    resolution = resolve_oracle_target(instance, source_index, graph)
    if resolution.target is None:
        return ConformanceRecord(instance_id=instance.instance_id, repo=instance.repo_key, status=Status.TARGET_UNMAPPABLE, note=resolution.note)
    touched = set(resolution.candidates) | {resolution.target}
    frame_set = frame(resolution.target, program_index, approx.frame)
    seed = build_seed(graph, resolution.target, frame_set, prop)
    kinds = PROPERTY_KINDS[prop]
    contexts = {'slice_capped': set(analyse(graph, seed.seed, (), depth_cap=approx.depth_cap, kinds=kinds, realizable=approx.realizable).slice_), 'slice': set(analyse(graph, seed.seed, (), depth_cap=None, kinds=kinds, realizable=approx.realizable).slice_), 'reference': reference_context(resolution.target, source_index, graph, reference_context_kind)}
    fp = footprint(instance, target=resolution.target, pre_image=resolution.candidates, graph=graph, patched_graph=patched_graph, patched_index=patched_index, contexts=contexts)
    return ConformanceRecord(instance_id=instance.instance_id, repo=instance.repo_key, status=Status.OK, target=resolution.target, footprint=fp)

def measure(instance: Instance, approx: Approximation, data_dir: Path, *, prop: Property=Property.P1_CALLER_BEHAVIOUR, cpus: int=4, reference_context_kind: str='target-file') -> ConformanceRecord:
    base_dir = data_dir / 'instances' / instance.instance_id
    base_manifest = prepare(instance, base_dir, cpus=cpus)
    if base_manifest.status is not Status.OK:
        return ConformanceRecord(instance_id=instance.instance_id, repo=instance.repo_key, status=base_manifest.status, note='base revision did not prepare')
    base_analysis = analyse_instance(instance.instance_id, instance.base_commit, base_dir, base_manifest, approx, data_dir / 'analysis', cpus=cpus)
    if base_analysis.status not in ANALYSABLE:
        return ConformanceRecord(instance_id=instance.instance_id, repo=instance.repo_key, status=base_analysis.status, note='base revision did not analyse')
    graph, program_index, source_index, _, _ = load_artifacts(base_analysis.edg_path, base_analysis.index_path, [Paths(base_dir).sources])
    resolution = resolve_oracle_target(instance, source_index, graph)
    if resolution.target is None:
        return ConformanceRecord(instance_id=instance.instance_id, repo=instance.repo_key, status=Status.TARGET_UNMAPPABLE, note=resolution.note)
    patched_dir = data_dir / 'instances-patched' / instance.instance_id
    patched_manifest = prepare(instance, patched_dir, cpus=cpus, apply_gold_patch=True)
    if patched_manifest.status is not Status.OK:
        return ConformanceRecord(instance_id=instance.instance_id, repo=instance.repo_key, status=patched_manifest.status, target=resolution.target, note='patched revision did not build')
    patched_analysis = analyse_instance(instance.instance_id + '@patched', instance.base_commit, patched_dir, patched_manifest, approx, data_dir / 'analysis', cpus=cpus)
    if patched_analysis.status not in ANALYSABLE or patched_analysis.edg_path is None or patched_analysis.index_path is None:
        return ConformanceRecord(instance_id=instance.instance_id, repo=instance.repo_key, status=patched_analysis.status, target=resolution.target, note='patched revision did not analyse')
    patched_graph, _, patched_index, _, _ = load_artifacts(patched_analysis.edg_path, patched_analysis.index_path, [Paths(patched_dir).sources])
    return _from_artifacts(instance, approx, graph=graph, program_index=program_index, source_index=source_index, patched_graph=patched_graph, patched_index=patched_index, prop=prop, reference_context_kind=reference_context_kind)

def measure_cached(instance: Instance, approx: Approximation, data_dir: Path, *, prop: Property=Property.P1_CALLER_BEHAVIOUR, reference_context_kind: str='target-file') -> ConformanceRecord:
    iid = instance.instance_id
    base_dir = data_dir / 'instances' / iid
    patched_dir = data_dir / 'instances-patched' / iid
    try:
        d = resolve_cached_analysis(iid, instance.base_commit, base_dir, approx, data_dir / 'analysis')
        dp = resolve_cached_analysis(iid + '@patched', instance.base_commit, patched_dir, approx, data_dir / 'analysis')
    except NoCachedAnalysis as exc:
        return ConformanceRecord(instance_id=iid, repo=instance.repo_key, status=Status.ANALYSIS_FAILED, note=f'cached-only: no cached analysis, none run ({exc})'[:500])
    graph, program_index, source_index, _, _ = load_artifacts(d / 'edg.jsonl', d / 'index.json', [Paths(base_dir).sources])
    patched_graph, _, patched_index, _, _ = load_artifacts(dp / 'edg.jsonl', dp / 'index.json', [Paths(patched_dir).sources])
    return _from_artifacts(instance, approx, graph=graph, program_index=program_index, source_index=source_index, patched_graph=patched_graph, patched_index=patched_index, prop=prop, reference_context_kind=reference_context_kind)

def _median(values: Sequence[float]) -> float | None:
    return float(statistics.median(values)) if values else None

def summarise(records: Sequence[ConformanceRecord]) -> dict[str, Any]:
    measured = [r for r in records if not r.note and r.footprint is not None]
    fps = [r.footprint for r in measured if r.footprint is not None]
    out: dict[str, Any] = {'attempted': len(records), 'measured': len(measured), 'excluded': len(records) - len(measured)}
    if not measured:
        return out
    per_context: dict[str, Any] = {}
    for name, what in CONTEXTS.items():
        cs = [f.contexts[name] for f in fps if name in f.contexts]
        esc_hi = sum((c.escaping_touched for c in cs))
        per_context[name] = {'context': what, 'n': len(cs), 'context_elements_median': _median([c.context_elements for c in cs]), 'conformant_touched': sum((1 for c in cs if c.conformant_touched)), 'conformant_introduced': sum((1 for c in cs if c.conformant_introduced)), 'escaping_touched_median': _median([c.escaping_touched for c in cs]), 'escaping_introduced_median': _median([c.escaping_introduced for c in cs]), 'escaping_touched_max': max((c.escaping_touched for c in cs), default=0), 'escaping_touched_test_share': sum((c.escaping_touched_test for c in cs)) / esc_hi if esc_hi else None, 'edited_outside_instances': sum((1 for c in cs if c.edited_outside))}
    out['def_3_4'] = {'definition': "refs(rho) \\ (C u B) over the base program. refs = what the touched elements' text names (return, heap-read, type-ref out; heap-write in; plus overrides of call targets), read from the patched build. `touched` bounds it above (every reference of every touched element), `introduced` below (references the element did not make before the patch). B is not an EDG node set, so library references conform by construction; references to identities the patch creates are counted as refs_created, not escaping", 'refs_touched_median': _median([f.refs_touched for f in fps]), 'refs_introduced_median': _median([f.refs_introduced for f in fps]), 'refs_created_instances': sum((1 for f in fps if f.refs_created)), 'touched_median': _median([f.touched for f in fps]), 'contexts': per_context}
    return out
