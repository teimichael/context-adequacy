from __future__ import annotations
from collections.abc import Mapping, Sequence
from lacuna.builders.base import BuildResult, ContextBuilder
from lacuna.builders.safety import BuilderCriterion, safe_verdict
from lacuna.criterion.closure import analyse, bfs
from lacuna.criterion.verdict import Verdict, VerdictValue
from lacuna.model.edg import ElementDependenceGraph, UnknownReason
from lacuna.model.identity import ElementId

class ClosureFirstBuilder(ContextBuilder):
    name = 'closure-first'

    def build(self, *, graph: ElementDependenceGraph, sources: Mapping[str, str], target: str, seed: frozenset[str], budget: int, issue_text: str='', criterion: BuilderCriterion | None=None, ranking: Sequence[str] | None=None, **kwargs: object) -> BuildResult:
        if 'depth_cap' in kwargs:
            raise TypeError('ClosureFirstBuilder no longer takes depth_cap; pass criterion=BuilderCriterion(depth_cap=...) (README.md)')
        del kwargs
        crit = criterion if criterion is not None else BuilderCriterion()
        seed_present = {s for s in seed if s in graph}
        seed_tokens = self._total(seed_present, sources)
        if seed_tokens > budget:
            affordable: set[str] = set()
            dropped_seed = self._fill(affordable, sorted(seed_present, key=ElementId), sources, budget)
            return BuildResult(builder=self.name, budget=budget, context=tuple(sorted(affordable)), realised_tokens=self._total(affordable, sources), dropped=dropped_seed, verdict=Verdict(value=VerdictValue.UNKNOWN, reasons=(UnknownReason.SEED_EXCEEDS_BUDGET,)), note=f'unassessable@budget: the seed needs {seed_tokens:,} tokens against a budget of {budget:,}, so the context cannot hold the edit frame')
        reach = bfs(graph, seed_present, max_distance=crit.depth_cap, kinds=crit.kinds, realizable=crit.realizable)
        layers: dict[int, set[str]] = {}
        for element, dist in reach.dist.items():
            layers.setdefault(dist, set()).add(element)
        context: set[str] = set(seed_present)
        k = 0
        while True:
            frontier = layers.get(k + 1, set()) - context
            if not frontier:
                break
            if self._total(context | frontier, sources) > budget:
                break
            context |= frontier
            k += 1
        ranked = [e for e in self._similarity(sources, target, issue_text, ranking) if e not in context]
        dropped = self._fill(context, ranked, sources, budget)
        result = analyse(graph, seed_present, context, depth_cap=crit.depth_cap, kinds=crit.kinds, realizable=crit.realizable)
        verdict = safe_verdict(graph, target, result, sources, crit)
        return BuildResult(builder=self.name, budget=budget, context=tuple(sorted(context)), realised_tokens=self._total(context, sources), verdict=verdict, layers=k, dropped=dropped, note=f'layers_added={k}')
