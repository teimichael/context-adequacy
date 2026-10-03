from __future__ import annotations
import json
import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any
from pydantic import BaseModel, ConfigDict
from lacuna.analysis.stats import describe
from lacuna.corpora.base import ANALYSABLE, Status
from lacuna.experiment.evaluate import InstanceRecord
from lacuna.experiment.funnel import Funnel, FunnelEntry
from lacuna.harness.analyze import AnalysisResult, NoCachedAnalysis, resolve_cached_analysis
from lacuna.model.approx import Approximation, Property
SCHEMA = 'graph-stats/1'
FILENAME = 'graph-stats.jsonl'
KIND_PREFIXES: dict[str, str] = {'T': 'type', 'M': 'method', 'F': 'field'}
if TYPE_CHECKING:
    from lacuna.experiment.config import ExperimentConfig
_MAPPING = re.compile('mapping (\\d+)/(\\d+)')

class NoResolvedConfig(FileNotFoundError):
    pass

def resolved_config(results_dir: Path) -> ExperimentConfig:
    from lacuna.experiment.config import ExperimentConfig
    resolved = Path(results_dir) / 'config.resolved.json'
    if not resolved.exists():
        raise NoResolvedConfig(f'{resolved} is missing; the base cell cannot be identified without it')
    return ExperimentConfig.model_validate_json(resolved.read_text())

def base_cell(results_dir: Path) -> tuple[str, Approximation]:
    cells = resolved_config(results_dir).resolved_approximations()
    if not cells:
        raise NoResolvedConfig(f'{results_dir}: config declares no approximation cell')
    return cells[0]

class EdgCounts(BaseModel):
    model_config = ConfigDict(frozen=True)
    nodes: int
    edges: int
    scope_counts: dict[str, int]
    kind_counts: dict[str, int]
    callgraph_methods: dict[str, int]

def scan_edg(path: Path) -> EdgCounts:
    nodes = edges = 0
    scopes: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    reached: Counter[str] = Counter()
    with Path(path).open(encoding='utf-8') as fh:
        for line in fh:
            if line.startswith('{"type":"edge"'):
                edges += 1
                continue
            if not line.strip():
                continue
            obj = json.loads(line)
            kind = obj.get('type')
            if kind == 'edge':
                edges += 1
            elif kind == 'node':
                nodes += 1
                scope = str(obj.get('scope', 'app'))
                scopes[scope] += 1
                ek = KIND_PREFIXES.get(str(obj.get('id', ''))[:1], 'other')
                kinds[ek] += 1
                if ek == 'method' and obj.get('in_callgraph') is True:
                    reached[scope] += 1
    return EdgCounts(nodes=nodes, edges=edges, scope_counts=dict(sorted(scopes.items())), kind_counts=dict(sorted(kinds.items())), callgraph_methods=dict(sorted(reached.items())))

def mapping_counts(note: str) -> tuple[int | None, int | None]:
    m = _MAPPING.search(note or '')
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)

class GraphStatsRow(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_version: str = SCHEMA
    instance_id: str
    corpus: str
    repo: str
    funnel_status: str | None
    funnel_seconds: float | None
    analysis_key: str
    analysis_digest: str
    base_digest: str
    base_analyser_digest: str
    analysis_status: str
    edg_nodes: int | None
    edg_edges: int | None
    app_classes: int | None
    reachable_app_methods: int | None
    cha_classes: int | None
    cg_nodes: int | None
    entry_points: int | None
    ms_cha: int | None
    ms_cg: int | None
    ms_edg: int | None
    ms_total: int | None
    seconds: float
    scope_counts: dict[str, int]
    kind_counts: dict[str, int]
    callgraph_methods: dict[str, int]
    app_nodes: int
    mapping_matched: int | None
    mapping_total: int | None
    p1_status: str | None
    p1_seed_elements: int | None
    p1_closure_elements: int | None
    p1_closure_tokens: int | None
    p1_full_closure_elements: int | None
    p1_full_closure_tokens: int | None
    analyser_stats: dict[str, Any]
    checks: dict[str, bool | None]

    @property
    def joined(self) -> bool:
        return all((v is not False for v in self.checks.values()))

def _int(stats: dict[str, Any], key: str) -> int | None:
    v = stats.get(key)
    return None if v is None else int(v)

def row_for(record: InstanceRecord, analysis_dir: Path, funnel_entry: FunnelEntry | None, base: Approximation) -> GraphStatsRow:
    res = AnalysisResult.model_validate_json((analysis_dir / 'result.json').read_text())
    stats = dict(res.stats or {})
    counts = scan_edg(analysis_dir / 'edg.jsonl')
    matched, total = mapping_counts(funnel_entry.note if funnel_entry else '')
    ok = record.status is Status.OK
    edg_nodes = _int(stats, 'edg_nodes')
    checks: dict[str, bool | None] = {'edg_file_matches_stats': counts.nodes == edg_nodes and counts.edges == _int(stats, 'edg_edges'), 'callgraph_methods_match_stats': None if 'reachable_app_methods' not in stats else sum(counts.callgraph_methods.values()) == _int(stats, 'reachable_app_methods'), 'funnel_mapping_matches_nodes': None if total is None else total == edg_nodes, 'record_escapes_match': record.escapes_fired == res.escapes_fired and record.escapes_magnitude == res.escapes_magnitude and (list(record.shadowing_jars_dropped) == list(res.shadowing_jars_dropped)), 'record_bridges_match': record.bridges_truncated == int(stats.get('bridges_truncated', 0)) if ok else None, 'funnel_status_matches_record': None if funnel_entry is None else (funnel_entry.status in ANALYSABLE) == ok}
    known = {'edg_nodes', 'edg_edges', 'app_classes', 'reachable_app_methods', 'cha_classes', 'cg_nodes', 'entry_points', 'ms_cha', 'ms_cg', 'ms_edg', 'ms_total'}
    return GraphStatsRow(instance_id=record.instance_id, corpus=record.corpus, repo=record.repo, funnel_status=funnel_entry.status.value if funnel_entry else None, funnel_seconds=funnel_entry.seconds if funnel_entry else None, analysis_key=analysis_dir.name, analysis_digest=res.approximation_digest, base_digest=base.digest(), base_analyser_digest=base.analyser_digest(), analysis_status=res.status.value, edg_nodes=edg_nodes, edg_edges=_int(stats, 'edg_edges'), app_classes=_int(stats, 'app_classes'), reachable_app_methods=_int(stats, 'reachable_app_methods'), cha_classes=_int(stats, 'cha_classes'), cg_nodes=_int(stats, 'cg_nodes'), entry_points=_int(stats, 'entry_points'), ms_cha=_int(stats, 'ms_cha'), ms_cg=_int(stats, 'ms_cg'), ms_edg=_int(stats, 'ms_edg'), ms_total=_int(stats, 'ms_total'), seconds=res.seconds, scope_counts=counts.scope_counts, kind_counts=counts.kind_counts, callgraph_methods=counts.callgraph_methods, app_nodes=counts.scope_counts.get('app', 0), mapping_matched=matched, mapping_total=total, p1_status=record.status.value, p1_seed_elements=record.seed_size if ok else None, p1_closure_elements=record.closure_elements if ok else None, p1_closure_tokens=record.closure_tokens if ok else None, p1_full_closure_elements=record.full_closure_elements if ok else None, p1_full_closure_tokens=record.full_closure_tokens if ok else None, analyser_stats={k: v for k, v in sorted(stats.items()) if k not in known}, checks=checks)

def base_p1_records(records: Iterable[InstanceRecord], base_digest: str) -> dict[str, InstanceRecord]:
    out: dict[str, InstanceRecord] = {}
    for r in records:
        if r.approximation.digest() != base_digest:
            continue
        if r.property is not Property.P1_CALLER_BEHAVIOUR:
            continue
        if r.instance_id in out:
            raise ValueError(f'{r.instance_id}: two base-cell P1 records')
        out[r.instance_id] = r
    return out
Resolver = Callable[[InstanceRecord], Path]

def default_resolver(base: Approximation, data_dir: Path) -> Resolver:

    def resolve(record: InstanceRecord) -> Path:
        return resolve_cached_analysis(record.instance_id, record.commit, Path(data_dir) / 'instances' / record.instance_id, base, Path(data_dir) / 'analysis')
    return resolve

def build_rows(results_dir: Path, data_dir: Path, *, resolver: Resolver | None=None) -> tuple[list[GraphStatsRow], list[str]]:
    from lacuna.experiment.runner import read_records
    results_dir = Path(results_dir)
    _, base = base_cell(results_dir)
    records = base_p1_records(read_records(results_dir / 'records.jsonl'), base.digest())
    funnel_path = results_dir / 'funnel.jsonl'
    funnel = {e.instance_id: e for e in (Funnel.read(funnel_path).entries if funnel_path.exists() else [])}
    resolve = resolver or default_resolver(base, data_dir)
    rows: list[GraphStatsRow] = []
    missing: list[str] = []
    for iid, rec in sorted(records.items()):
        try:
            d = resolve(rec)
        except NoCachedAnalysis:
            missing.append(iid)
            continue
        rows.append(row_for(rec, d, funnel.get(iid), base))
    return (rows, missing)

def write_rows(rows: Sequence[GraphStatsRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join((r.model_dump_json() + '\n' for r in rows)))

def read_rows(path: Path) -> list[GraphStatsRow]:
    return [GraphStatsRow.model_validate_json(line) for line in Path(path).read_text().splitlines() if line.strip()]

def summarise(rows: Sequence[GraphStatsRow]) -> dict[str, Any]:
    analysable = [r for r in rows if r.funnel_status in {s.value for s in ANALYSABLE}]
    full_ratio: list[float] = []
    capped_ratio: list[float] = []
    for r in analysable:
        if not r.edg_nodes:
            continue
        if r.p1_full_closure_elements is not None:
            full_ratio.append(r.p1_full_closure_elements / r.edg_nodes)
        if r.p1_closure_elements is not None:
            capped_ratio.append(r.p1_closure_elements / r.edg_nodes)

    def d(values: Iterable[float | int | None]) -> dict[str, float]:
        return describe([float(v) for v in values if v is not None])
    return {'rows': len(rows), 'joined': sum((1 for r in rows if r.joined)), 'not_joined': sorted((r.instance_id for r in rows if not r.joined)), 'by_funnel_status': dict(sorted(Counter((str(r.funnel_status) for r in rows)).items())), 'analysable': len(analysable), 'result_digest_differs_from_base': sum((1 for r in rows if r.analysis_digest != r.base_digest)), 'edg_nodes': d((r.edg_nodes for r in analysable)), 'edg_edges': d((r.edg_edges for r in analysable)), 'app_nodes': d((r.app_nodes for r in analysable)), 'app_classes': d((r.app_classes for r in analysable)), 'reachable_app_methods': d((r.reachable_app_methods for r in analysable)), 'analysis_seconds': d((r.seconds for r in analysable)), 'analyser_ms_total': d((r.ms_total for r in analysable)), 'analysis_seconds_total': round(sum((r.seconds for r in analysable)), 1), 'closure_over_nodes': describe(full_ratio), 'capped_closure_over_nodes': describe(capped_ratio)}
