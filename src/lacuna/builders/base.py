from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from pydantic import BaseModel, ConfigDict, Field
from lacuna.builders.safety import BuilderCriterion
from lacuna.criterion.verdict import Verdict
from lacuna.model.edg import ElementDependenceGraph
from lacuna.tokens import DEFAULT_RENDERER, BudgetAudit, Renderer, tok, tok_per_element

class BuildResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    builder: str
    budget: int = Field(gt=0)
    context: tuple[str, ...]
    realised_tokens: int = Field(ge=0)
    verdict: Verdict | None = None
    layers: int | None = None
    dropped: int = 0
    note: str = ''

    def audit(self, scaffold_tokens: int=0) -> BudgetAudit:
        return BudgetAudit(builder=self.builder, nominal_budget=self.budget, realised_tokens=self.realised_tokens, elements=len(self.context), scaffold_tokens=scaffold_tokens)

class ContextBuilder(ABC):
    name: str = 'builder'

    def __init__(self, renderer: Renderer=DEFAULT_RENDERER) -> None:
        self.renderer = renderer

    @abstractmethod
    def build(self, *, graph: ElementDependenceGraph, sources: Mapping[str, str], target: str, seed: frozenset[str], budget: int, issue_text: str='', criterion: BuilderCriterion | None=None, ranking: Sequence[str] | None=None, **kwargs: object) -> BuildResult:
        pass

    def _cost(self, elements: Iterable[str], sources: Mapping[str, str]) -> dict[str, int]:
        return tok_per_element(elements, sources, renderer=self.renderer)

    def _total(self, elements: Iterable[str], sources: Mapping[str, str]) -> int:
        return tok(elements, sources, renderer=self.renderer)

    @staticmethod
    def _similarity(sources: Mapping[str, str], target: str, issue_text: str, ranking: Sequence[str] | None) -> list[str]:
        if ranking is not None:
            return list(ranking)
        from lacuna.builders.bm25 import BM25Index
        return BM25Index(sources).rank(issue_text or sources.get(target, ''))

    def _fill(self, chosen: set[str], ordered_candidates: list[str], sources: Mapping[str, str], budget: int) -> int:
        dropped = 0
        costs = self._cost(ordered_candidates, sources)
        current = self._total(chosen, sources)
        added: list[str] = []
        for cand in ordered_candidates:
            if cand in chosen:
                continue
            c = costs.get(cand)
            if c is None:
                continue
            if current + c + 2 <= budget:
                chosen.add(cand)
                added.append(cand)
                current += c + 2
            else:
                dropped += 1
        while added and self._total(chosen, sources) > budget:
            chosen.discard(added.pop())
            dropped += 1
        return dropped
