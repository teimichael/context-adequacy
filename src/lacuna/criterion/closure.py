from __future__ import annotations
from collections.abc import Iterable, Mapping
from pydantic import BaseModel, ConfigDict, Field
from lacuna.model.approx import CriterionLevel
from lacuna.model.edg import ElementDependenceGraph, UnknownReason
from lacuna.model.identity import EdgeKind, ElementId
INFINITY = float('inf')

class Reachability(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    dist: Mapping[str, int]
    parent: Mapping[str, str]
    root: Mapping[str, str]
    truncated: bool = False

    def reached(self) -> set[str]:
        return set(self.dist)

    def at_most(self, k: int) -> set[str]:
        return {e for e, d in self.dist.items() if d <= k}

def _kind_filter(level: CriterionLevel | None) -> frozenset[EdgeKind] | None:
    if level is None or level.kinds is None:
        return None
    return frozenset((EdgeKind(k) for k in level.kinds))
DESCENDING: frozenset[EdgeKind] = frozenset({EdgeKind.RETURN, EdgeKind.SUMMARY})
ASCENDING: frozenset[EdgeKind] = frozenset({EdgeKind.CALL})
REOPENS_ASCENT: frozenset[EdgeKind] = frozenset({EdgeKind.HEAP_WRITE})
UPSTREAM_KINDS: frozenset[EdgeKind] = frozenset({EdgeKind.CALL, EdgeKind.OVERRIDE, EdgeKind.HEAP_WRITE})
DOWNSTREAM_KINDS: frozenset[EdgeKind] = frozenset(EdgeKind) - frozenset({EdgeKind.CALL})
DIRECTIONS: dict[str, frozenset[EdgeKind]] = {'ascent': frozenset({EdgeKind.CALL, EdgeKind.HEAP_WRITE}), 'descent': frozenset({EdgeKind.RETURN, EdgeKind.SUMMARY, EdgeKind.HEAP_READ}), 'reference': frozenset({EdgeKind.TYPE_REF}), 'dispatch': frozenset({EdgeKind.OVERRIDE}), 'intra': frozenset({EdgeKind.DATA, EdgeKind.CONTROL})}
_seen: set[EdgeKind] = set()
for _name, _ks in DIRECTIONS.items():
    assert not _seen & _ks, f'DIRECTIONS is not disjoint at {_name!r}'
    _seen |= _ks
assert _seen == frozenset(EdgeKind), f'DIRECTIONS does not cover EdgeKind: missing {frozenset(EdgeKind) - _seen}'
del _seen, _name, _ks
_ASCEND, _DESCEND = (0, 1)

def bfs(graph: ElementDependenceGraph, sources: Iterable[str], *, max_distance: int | None=None, kinds: frozenset[EdgeKind] | None=None, realizable: bool=True, descend_only: Iterable[str]=()) -> Reachability:
    src_list = sorted(set(sources), key=ElementId)
    descend_roots = set(descend_only)
    dist: dict[str, int] = {s: 0 for s in src_list}
    root: dict[str, str] = {s: s for s in src_list}
    parent: dict[str, str] = {}
    truncated = False
    seen_states: set[tuple[str, int]] = set()
    frontier: list[tuple[str, int]] = []
    for s in src_list:
        phase = _DESCEND if realizable and s in descend_roots else _ASCEND
        st = (s, phase)
        seen_states.add(st)
        frontier.append(st)
    depth = 0

    def admissible(phase: int, edge_kinds: frozenset[EdgeKind]) -> tuple[bool, int]:
        ks = edge_kinds if kinds is None else edge_kinds & kinds
        if not ks:
            return (False, phase)
        if not realizable:
            return (True, _ASCEND)
        if phase == _ASCEND:
            ks = ks - DESCENDING
            return (bool(ks), _ASCEND)
        ks = ks - ASCENDING
        if not ks:
            return (False, phase)
        return (True, _ASCEND if ks & REOPENS_ASCENT else _DESCEND)
    while frontier:
        if max_distance is not None and depth >= max_distance:
            for node, phase in frontier:
                for dst, ks in graph.successors(node).items():
                    ok, nxt = admissible(phase, ks)
                    if ok and (dst, nxt) not in seen_states and (dst not in dist):
                        truncated = True
                        break
                if truncated:
                    break
            break
        if realizable:
            for node, phase in list(frontier):
                if phase == _ASCEND and (node, _DESCEND) not in seen_states:
                    seen_states.add((node, _DESCEND))
                    frontier.append((node, _DESCEND))
        candidates: dict[tuple[str, int], list[str]] = {}
        for node, phase in frontier:
            for dst, ks in graph.successors(node).items():
                ok, nxt = admissible(phase, ks)
                if not ok or (dst, nxt) in seen_states:
                    continue
                candidates.setdefault((dst, nxt), []).append(node)
        if not candidates:
            break
        next_layer: list[tuple[str, int]] = []
        for dst, nxt in sorted(candidates, key=lambda k: (ElementId(k[0]), k[1])):
            preds = candidates[dst, nxt]
            canonical = min(preds, key=ElementId)
            seen_states.add((dst, nxt))
            next_layer.append((dst, nxt))
            if dst in dist:
                continue
            dist[dst] = depth + 1
            parent[dst] = canonical
            root[dst] = root[canonical]
        frontier = next_layer
        depth += 1
    return Reachability(dist=dist, parent=parent, root=root, truncated=truncated)

class ClosureResult(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    seed: frozenset[str]
    context: frozenset[str]
    slice_: frozenset[str]
    levels: Mapping[str, frozenset[str]]
    slice_dist: Mapping[str, int]
    slice_truncated: bool
    omit: frozenset[str]
    omit_dist: Mapping[str, int]
    omit_parent: Mapping[str, str]
    omit_root: Mapping[str, str]
    omit_truncated: bool
    depth: int | None
    depth_basis: str = 'seed'
    slice_parent: Mapping[str, str] = {}
    unknown_reasons: frozenset[UnknownReason] = frozenset()
    elements_visited: int = Field(default=0, ge=0)

    @property
    def adequate(self) -> bool:
        return not self.omit

def analyse(graph: ElementDependenceGraph, seed: Iterable[str], context: Iterable[str], *, depth_cap: int | None=None, levels: Iterable[CriterionLevel]=(), kinds: frozenset[EdgeKind] | None=None, realizable: bool=True) -> ClosureResult:
    seed_set = frozenset(seed)
    ctx_set = frozenset(context)
    unknown_seed = [s for s in seed_set if s not in graph]
    if unknown_seed:
        raise KeyError('seed contains elements absent from the graph: ' + ', '.join(sorted(unknown_seed)[:5]))
    slice_reach = bfs(graph, seed_set, max_distance=depth_cap, kinds=kinds, realizable=realizable)
    level_sets: dict[str, frozenset[str]] = {}
    for level in levels:
        level_kinds = _kind_filter(level)
        effective = level_kinds if kinds is None else kinds if level_kinds is None else frozenset(level_kinds & kinds)
        lv = bfs(graph, seed_set, max_distance=level.max_distance, kinds=effective, realizable=realizable)
        level_sets[level.name] = frozenset(lv.reached())
    omit_reach = bfs(graph, seed_set | ctx_set, max_distance=depth_cap, kinds=kinds, descend_only=ctx_set - seed_set, realizable=realizable)
    closure_with_ctx = omit_reach.reached()
    omit = frozenset(closure_with_ctx - ctx_set)
    depth_basis = 'seed'
    if not omit:
        depth: int | None = None
    else:
        seed_reachable = [slice_reach.dist[e] for e in omit if e in slice_reach.dist]
        if seed_reachable:
            depth = min(seed_reachable) - 1
        else:
            depth = min((omit_reach.dist[e] for e in omit)) - 1
            depth_basis = 'context'
    reasons = graph.unknown_reasons(closure_with_ctx)
    return ClosureResult(seed=seed_set, context=ctx_set, slice_=frozenset(slice_reach.reached()), levels=level_sets, slice_dist=dict(slice_reach.dist), slice_truncated=slice_reach.truncated, omit=omit, omit_dist=dict(omit_reach.dist), omit_parent=dict(omit_reach.parent), omit_root=dict(omit_reach.root), omit_truncated=omit_reach.truncated, depth=depth, depth_basis=depth_basis, slice_parent=dict(slice_reach.parent), unknown_reasons=frozenset(reasons), elements_visited=len(closure_with_ctx))
