from __future__ import annotations
import math
import re
from collections.abc import Mapping, Sequence
from lacuna.builders.base import BuildResult, ContextBuilder
from lacuna.builders.safety import BuilderCriterion
from lacuna.model.edg import ElementDependenceGraph
K1 = 1.2
B = 0.75
_SPLIT = re.compile('[^A-Za-z0-9]+')
_CAMEL = re.compile('(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')

def code_tokens(text: str) -> list[str]:
    out: list[str] = []
    for chunk in _SPLIT.split(text):
        if not chunk:
            continue
        for piece in _CAMEL.split(chunk):
            if piece:
                out.append(piece.lower())
    return out

class BM25Index:

    def __init__(self, documents: Mapping[str, str]) -> None:
        self.ids = sorted(documents)
        self.docs = [code_tokens(documents[i]) for i in self.ids]
        self.lengths = [len(d) for d in self.docs]
        self.avgdl = sum(self.lengths) / len(self.lengths) if self.lengths else 0.0
        self.freqs: list[dict[str, int]] = []
        self.df: dict[str, int] = {}
        for doc in self.docs:
            tf: dict[str, int] = {}
            for t in doc:
                tf[t] = tf.get(t, 0) + 1
            self.freqs.append(tf)
            for t in tf:
                self.df[t] = self.df.get(t, 0) + 1
        self.n = len(self.docs)

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log(1.0 + (self.n - df + 0.5) / (df + 0.5))

    def rank(self, query: str) -> list[str]:
        q = code_tokens(query)
        scores: list[tuple[float, str]] = []
        for i, eid in enumerate(self.ids):
            tf = self.freqs[i]
            dl = self.lengths[i] or 1
            s = 0.0
            for term in q:
                f = tf.get(term, 0)
                if not f:
                    continue
                denom = f + K1 * (1 - B + B * dl / (self.avgdl or 1))
                s += self._idf(term) * (f * (K1 + 1)) / denom
            scores.append((s, eid))
        scores.sort(key=lambda kv: (-kv[0], kv[1]))
        return [eid for _, eid in scores]

class LexicalBM25Builder(ContextBuilder):
    name = 'lexical-bm25'

    def build(self, *, graph: ElementDependenceGraph, sources: Mapping[str, str], target: str, seed: frozenset[str], budget: int, issue_text: str='', criterion: BuilderCriterion | None=None, ranking: Sequence[str] | None=None, **kwargs: object) -> BuildResult:
        del graph, seed, criterion, kwargs
        chosen: set[str] = {target} if target in sources else set()
        ranked = [e for e in self._similarity(sources, target, issue_text, ranking) if e != target]
        dropped = self._fill(chosen, ranked, sources, budget)
        return BuildResult(builder=self.name, budget=budget, context=tuple(sorted(chosen)), realised_tokens=self._total(chosen, sources), dropped=dropped)
