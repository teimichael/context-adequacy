from __future__ import annotations
from pydantic import BaseModel, ConfigDict
from lacuna.criterion.closure import ClosureResult
from lacuna.model.edg import ElementDependenceGraph
from lacuna.model.identity import EdgeKind, ElementId

class WitnessStep(BaseModel):
    model_config = ConfigDict(frozen=True)
    src: str
    dst: str
    kind: EdgeKind

class Witness(BaseModel):
    model_config = ConfigDict(frozen=True)
    target_omission: str
    chain: tuple[str, ...]
    steps: tuple[WitnessStep, ...]
    distance: int
    interior: bool
    rooted_in_context: bool = False

    def is_chordless(self, graph: ElementDependenceGraph) -> bool:
        for i, a in enumerate(self.chain):
            for j in range(i + 2, len(self.chain)):
                if self.chain[j] in graph.successors(a):
                    return False
        return True

def canonical_witness(graph: ElementDependenceGraph, result: ClosureResult) -> Witness | None:
    if not result.omit:
        return None
    dist = result.slice_dist
    parent = result.slice_parent
    rooted_in_context = False
    candidates = [e for e in result.omit if e in dist]
    if not candidates:
        dist = result.omit_dist
        parent = result.omit_parent
        candidates = [e for e in result.omit if e in dist]
        rooted_in_context = True
    if not candidates:
        return None
    best = min((dist[e] for e in candidates))
    star = min((e for e in candidates if dist[e] == best), key=ElementId)
    chain: list[str] = [star]
    node = star
    seen = {star}
    while node in parent:
        node = parent[node]
        if node in seen:
            raise RuntimeError(f'cycle in canonical predecessor chain at {node}')
        seen.add(node)
        chain.append(node)
    chain.reverse()
    steps = tuple((WitnessStep(src=a, dst=b, kind=graph.best_kind(a, b)) for a, b in zip(chain, chain[1:], strict=False)))
    interior = all((n in result.context for n in chain[1:-1]))
    return Witness(target_omission=star, chain=tuple(chain), steps=steps, distance=dist[star], interior=interior, rooted_in_context=rooted_in_context)
