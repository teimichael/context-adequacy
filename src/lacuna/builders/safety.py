from __future__ import annotations
from collections.abc import Mapping, Sequence
from pydantic import BaseModel, ConfigDict
from lacuna.criterion.closure import ClosureResult
from lacuna.criterion.frame import fields_without_writers
from lacuna.criterion.properties import PROPERTY_KINDS
from lacuna.criterion.verdict import Verdict, decide
from lacuna.model.approx import DEFAULT_APPROXIMATION, Approximation, Property
from lacuna.model.edg import ElementDependenceGraph, UnknownReason
from lacuna.model.identity import EdgeKind
from lacuna.tokens import missing_sources
MIRRORED_RUN_REASONS: frozenset[UnknownReason] = frozenset({UnknownReason.ENTRY_POINTS, UnknownReason.NO_WRITER, UnknownReason.UNMAPPABLE, UnknownReason.BRIDGE_TRUNCATED})

def property_kinds(approx: Approximation, prop: Property) -> frozenset[EdgeKind]:
    kinds = PROPERTY_KINDS[prop]
    if not approx.summary_edges:
        kinds = frozenset(kinds - {EdgeKind.SUMMARY})
    return kinds

class BuilderCriterion(BaseModel):
    model_config = ConfigDict(frozen=True)
    frame: frozenset[str] = frozenset()
    depth_cap: int | None = DEFAULT_APPROXIMATION.depth_cap
    kinds: frozenset[EdgeKind] | None = PROPERTY_KINDS[Property.P1_CALLER_BEHAVIOUR]
    realizable: bool = DEFAULT_APPROXIMATION.realizable
    element_reasons: Mapping[str, Sequence[str]] = {}
    bridges_truncated: int = 0

    @classmethod
    def from_analysis(cls, approx: Approximation, prop: Property, *, frame: frozenset[str] | set[str], element_reasons: Mapping[str, Sequence[str]] | None=None, bridges_truncated: int=0) -> BuilderCriterion:
        return cls(frame=frozenset(frame), depth_cap=approx.depth_cap, kinds=property_kinds(approx, prop), realizable=approx.realizable, element_reasons={k: tuple(v) for k, v in (element_reasons or {}).items()}, bridges_truncated=int(bridges_truncated))

def run_reasons(graph: ElementDependenceGraph, target: str, result: ClosureResult, sources: Mapping[str, str], criterion: BuilderCriterion) -> frozenset[UnknownReason]:
    out: set[UnknownReason] = set()
    if target in graph and graph.record(target).in_callgraph is False:
        out.add(UnknownReason.ENTRY_POINTS)
    if fields_without_writers(criterion.frame, graph):
        out.add(UnknownReason.NO_WRITER)
    if any((graph.record(e).in_callgraph is False for e in result.slice_)):
        out.add(UnknownReason.ENTRY_POINTS)
    if missing_sources(result.slice_, sources):
        out.add(UnknownReason.UNMAPPABLE)
    if criterion.bridges_truncated:
        out.add(UnknownReason.BRIDGE_TRUNCATED)
    for element, names in criterion.element_reasons.items():
        if element == '__run__' or element in result.omit_dist:
            for n in names:
                try:
                    out.add(UnknownReason(n))
                except ValueError:
                    continue
    return frozenset(out)

def safe_verdict(graph: ElementDependenceGraph, target: str, result: ClosureResult, sources: Mapping[str, str], criterion: BuilderCriterion) -> Verdict:
    return decide(result, depth_cap=criterion.depth_cap if criterion.depth_cap is not None else 0, extra_reasons=run_reasons(graph, target, result, sources, criterion))
