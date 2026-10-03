from __future__ import annotations
from pathlib import Path
from typing import Any
from pydantic import BaseModel, ConfigDict, Field
from lacuna.criterion.closure import bfs
from lacuna.model.edg import ElementDependenceGraph
MAX_LISTED = 50

class CrossCheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    instance_id: str
    target: str
    status: str
    depth_cap: int = 0
    sdg_statements: int = 0
    sdg_elements: int = 0
    fast_elements: int = 0
    missed_count: int = 0
    missed_by_fast: list[str] = Field(default_factory=list)
    not_in_graph_count: int = 0
    not_in_graph: list[str] = Field(default_factory=list)
    note: str = ''

    @property
    def clean(self) -> bool:
        return self.status == 'ok' and self.missed_count == 0

    def summary_line(self) -> str:
        if self.status != 'ok':
            return f'{self.instance_id}: cross-check {self.status} -- {self.note}'
        verdict = f'no counterexample within {self.depth_cap} statement hops' if self.clean else f'FAST MISSES {self.missed_count} element(s)'
        return f'{self.instance_id}: SDG {self.sdg_elements} elements ({self.sdg_statements} statements), FAST {self.fast_elements} -- {verdict}'

def compare(instance_id: str, target: str, graph: ElementDependenceGraph, crosscheck_stats: dict[str, Any], *, depth_cap: int) -> CrossCheckResult:
    status = str(crosscheck_stats.get('status', 'missing'))
    if status != 'ok':
        return CrossCheckResult(instance_id=instance_id, target=target, status=status, depth_cap=depth_cap, note='the SDG walk produced no element set; this is not evidence that FAST agrees with the SDG')
    sdg_elements = {str(e) for e in crosscheck_stats.get('element_ids', [])}
    fast = set(bfs(graph, [target]).reached())
    not_in_graph = sorted((e for e in sdg_elements if e not in graph))
    missed = sorted((e for e in sdg_elements if e in graph and e not in fast))
    return CrossCheckResult(instance_id=instance_id, target=target, status=status, depth_cap=depth_cap, sdg_statements=int(crosscheck_stats.get('statements', 0) or 0), sdg_elements=len(sdg_elements), fast_elements=len(fast), missed_count=len(missed), missed_by_fast=missed[:MAX_LISTED], not_in_graph_count=len(not_in_graph), not_in_graph=not_in_graph[:MAX_LISTED])

def run_for_instance(instance: Any, prepared: Path, manifest: Any, approx: Any, analysis_dir: Path, *, cpus: int=4, memory_gb: int=8) -> CrossCheckResult | None:
    from lacuna.corpora.base import ANALYSABLE
    from lacuna.experiment.evaluate import load_artifacts, resolve_oracle_target
    from lacuna.harness.analyze import analyse_instance
    from lacuna.harness.prepare import Paths
    fast = analyse_instance(instance.instance_id, instance.base_commit, prepared, manifest, approx, analysis_dir, cpus=cpus, memory_gb=memory_gb)
    if fast.status not in ANALYSABLE or fast.edg_path is None or fast.index_path is None:
        return None
    graph, _, source_index, _, _ = load_artifacts(fast.edg_path, fast.index_path, [Paths(prepared).sources])
    resolution = resolve_oracle_target(instance, source_index, graph)
    if resolution.target is None:
        return None
    both = analyse_instance(instance.instance_id, instance.base_commit, prepared, manifest, approx, analysis_dir, cpus=cpus, memory_gb=memory_gb, mode='BOTH', seeds=[resolution.target])
    stats = (both.stats or {}).get('sdg_crosscheck', {})
    return compare(instance.instance_id, resolution.target, graph, stats, depth_cap=approx.depth_cap)
