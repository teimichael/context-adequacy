from __future__ import annotations
from collections.abc import Mapping, Sequence
from lacuna.builders.base import BuildResult, ContextBuilder
from lacuna.builders.safety import BuilderCriterion
from lacuna.model.edg import ElementDependenceGraph
from lacuna.model.identity import EdgeKind

def one_hop_neighbours(graph: ElementDependenceGraph, target: str, kinds: frozenset[EdgeKind] | None=None) -> set[str]:
    if target not in graph:
        return set()
    out: set[str] = set()
    for table in (graph.successors(target), graph.predecessors(target)):
        for other, ks in table.items():
            if kinds is None or ks & kinds:
                out.add(other)
    out.discard(target)
    return out

class DependenceOneHopBuilder(ContextBuilder):
    name = 'dependence-1hop'

    def build(self, *, graph: ElementDependenceGraph, sources: Mapping[str, str], target: str, seed: frozenset[str], budget: int, issue_text: str='', criterion: BuilderCriterion | None=None, ranking: Sequence[str] | None=None, **kwargs: object) -> BuildResult:
        del seed, kwargs
        crit = criterion if criterion is not None else BuilderCriterion()
        target_tokens = self._total([target], sources) if target in sources else 0
        if target_tokens > budget:
            return BuildResult(builder=self.name, budget=budget, context=(), realised_tokens=0, dropped=1, note=f'unassessable@budget: the target alone needs {target_tokens:,} tokens against a budget of {budget:,}')
        order = self._similarity(sources, target, issue_text, ranking)
        position = {e: i for i, e in enumerate(order)}

        def ranked(elements: set[str]) -> list[str]:
            return sorted(elements, key=lambda e: (position.get(e, len(order)), e))
        chosen: set[str] = {target} if target in sources else set()
        frame_set = {e for e in crit.frame if e in graph and e != target}
        hop = one_hop_neighbours(graph, target, crit.kinds) - frame_set
        dropped = self._fill(chosen, ranked(frame_set), sources, budget)
        after_frame = len(chosen)
        dropped += self._fill(chosen, ranked(hop), sources, budget)
        after_hop = len(chosen)
        dropped += self._fill(chosen, [e for e in order if e not in chosen], sources, budget)
        return BuildResult(builder=self.name, budget=budget, context=tuple(sorted(chosen)), realised_tokens=self._total(chosen, sources), dropped=dropped, note=f'frame={after_frame - (1 if target in chosen else 0)}/{len(frame_set)} hop={after_hop - after_frame}/{len(hop)} fill={len(chosen) - after_hop}')
