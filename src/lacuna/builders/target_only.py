from __future__ import annotations
from collections.abc import Mapping, Sequence
from lacuna.builders.base import BuildResult, ContextBuilder
from lacuna.builders.safety import BuilderCriterion
from lacuna.model.edg import ElementDependenceGraph

class TargetOnlyBuilder(ContextBuilder):
    name = 'target-only'

    def build(self, *, graph: ElementDependenceGraph, sources: Mapping[str, str], target: str, seed: frozenset[str], budget: int, issue_text: str='', criterion: BuilderCriterion | None=None, ranking: Sequence[str] | None=None, **kwargs: object) -> BuildResult:
        del graph, seed, issue_text, criterion, ranking, kwargs
        chosen = {target} if target in sources else set()
        realised = self._total(chosen, sources)
        if realised > budget:
            return BuildResult(builder=self.name, budget=budget, context=(), realised_tokens=0, dropped=len(chosen), note=f'unassessable@budget: the target alone needs {realised:,} tokens against a budget of {budget:,}')
        return BuildResult(builder=self.name, budget=budget, context=tuple(sorted(chosen)), realised_tokens=realised, note='' if chosen else 'target has no renderable source')
