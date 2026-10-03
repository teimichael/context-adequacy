from __future__ import annotations
from collections.abc import Iterable
from pydantic import BaseModel, ConfigDict
from lacuna.model.approx import Property
from lacuna.model.edg import ElementDependenceGraph
from lacuna.model.identity import EdgeKind, ElementId
P1_KINDS: frozenset[EdgeKind] = frozenset(EdgeKind)
P2_KINDS: frozenset[EdgeKind] = frozenset({EdgeKind.DATA, EdgeKind.HEAP_WRITE, EdgeKind.CALL, EdgeKind.RETURN, EdgeKind.SUMMARY})
P3_KINDS: frozenset[EdgeKind] = frozenset({EdgeKind.TYPE_REF, EdgeKind.OVERRIDE, EdgeKind.CALL, EdgeKind.RETURN})
PROPERTY_KINDS: dict[Property, frozenset[EdgeKind]] = {Property.P1_CALLER_BEHAVIOUR: P1_KINDS, Property.P2_DECLARED_INVARIANT: P2_KINDS, Property.P3_SIGNATURE_TYPES: P3_KINDS}

def _closure_over_predecessors(graph: ElementDependenceGraph, start: Iterable[str], kinds: frozenset[EdgeKind] | None=None, max_steps: int | None=None) -> set[str]:
    seen = {s for s in start if s in graph}
    frontier = sorted(seen, key=ElementId)
    steps = 0
    while frontier:
        if max_steps is not None and steps >= max_steps:
            break
        nxt: list[str] = []
        for node in frontier:
            for pred, ks in graph.predecessors(node).items():
                if kinds is not None and (not ks & kinds):
                    continue
                if pred not in seen:
                    seen.add(pred)
                    nxt.append(pred)
        frontier = sorted(nxt, key=ElementId)
        steps += 1
    return seen

def forward_set(graph: ElementDependenceGraph, target: str) -> set[str]:
    return _closure_over_predecessors(graph, [target])

def transitive_callers(graph: ElementDependenceGraph, target: str) -> set[str]:
    seen = {target}
    frontier = [target]
    while frontier:
        nxt: list[str] = []
        for node in frontier:
            for pred, ks in graph.predecessors(node).items():
                if EdgeKind.RETURN in ks or EdgeKind.OVERRIDE in ks:
                    if pred not in seen:
                        seen.add(pred)
                        nxt.append(pred)
            for succ, ks in graph.successors(node).items():
                if EdgeKind.CALL in ks:
                    if succ not in seen:
                        seen.add(succ)
                        nxt.append(succ)
        frontier = sorted(nxt, key=ElementId)
    seen.discard(target)
    return seen

class SeedResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    seed: frozenset[str]
    frame: frozenset[str]
    observation_points: frozenset[str]
    crit_size: int = 0
    forward_size: int
    applicable: bool = True

def criterion_set(graph: ElementDependenceGraph, target: str, prop: Property, *, declared_invariants: Iterable[str]=()) -> tuple[set[str], bool]:
    if prop is Property.P1_CALLER_BEHAVIOUR:
        return (transitive_callers(graph, target), True)
    if prop is Property.P3_SIGNATURE_TYPES:
        uses: set[str] = set()
        for pred, ks in graph.predecessors(target).items():
            if ks & {EdgeKind.TYPE_REF, EdgeKind.RETURN, EdgeKind.OVERRIDE}:
                uses.add(pred)
        for succ, ks in graph.successors(target).items():
            if EdgeKind.CALL in ks:
                uses.add(succ)
        uses.discard(target)
        return (uses, True)
    inv = {i for i in declared_invariants if i in graph}
    return (inv, bool(inv))

def build_seed(graph: ElementDependenceGraph, target: str, frame_set: Iterable[str], prop: Property, *, declared_invariants: Iterable[str]=()) -> SeedResult:
    if target not in graph:
        raise KeyError(f'target {target!r} is not an element of the graph')
    frame_frozen = frozenset((f for f in frame_set))
    fwd = forward_set(graph, target)
    crit, applicable = criterion_set(graph, target, prop, declared_invariants=declared_invariants)
    observation = crit & fwd
    seed = frame_frozen & frozenset(graph.nodes()) | observation | {target}
    return SeedResult(seed=frozenset(seed), frame=frame_frozen, observation_points=frozenset(observation), crit_size=len(crit), forward_size=len(fwd), applicable=applicable)
