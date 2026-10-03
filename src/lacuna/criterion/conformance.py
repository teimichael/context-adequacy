from __future__ import annotations
from collections.abc import Iterable
from pydantic import BaseModel, ConfigDict
from lacuna.criterion.closure import analyse
from lacuna.model.edg import ElementDependenceGraph
from lacuna.model.identity import EdgeKind, ElementId, ElementKind, MalformedElementId

class ConformanceResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    conformant: bool
    escaping_refs: tuple[str, ...] = ()
    refs_total: int = 0

def check(refs: Iterable[str], context: Iterable[str], base: Iterable[str]=()) -> ConformanceResult:
    refs_set = set(refs)
    allowed = set(context) | set(base)
    escaping = sorted(refs_set - allowed)
    return ConformanceResult(conformant=not escaping, escaping_refs=tuple(escaping), refs_total=len(refs_set))
REFERENCE_OUT_KINDS: frozenset[EdgeKind] = frozenset({EdgeKind.RETURN, EdgeKind.HEAP_READ, EdgeKind.TYPE_REF})
REFERENCE_IN_KINDS: frozenset[EdgeKind] = frozenset({EdgeKind.HEAP_WRITE})

def _declaring_type(element: str) -> str | None:
    try:
        eid = ElementId(element)
    except MalformedElementId:
        return None
    return None if eid.kind is ElementKind.TYPE else str(eid.declaring_type)

def references(graph: ElementDependenceGraph, elements: Iterable[str]) -> set[str]:
    own = set(elements)
    out: set[str] = set()
    for e in own:
        if e not in graph:
            continue
        home = _declaring_type(e)
        for dst, ks in graph.successors(e).items():
            if not ks & REFERENCE_OUT_KINDS:
                continue
            if dst == home and ks & REFERENCE_OUT_KINDS == {EdgeKind.TYPE_REF}:
                continue
            out.add(dst)
            if EdgeKind.RETURN in ks:
                out.update((impl for impl, iks in graph.successors(dst).items() if EdgeKind.OVERRIDE in iks))
        for src, ks in graph.predecessors(e).items():
            if ks & REFERENCE_IN_KINDS:
                out.add(src)
    return out - own

def introduced_references(patched: ElementDependenceGraph, base: ElementDependenceGraph, elements: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for e in elements:
        after = references(patched, {e})
        before = references(base, {e}) if e in base else set()
        out |= after - before
    return out
