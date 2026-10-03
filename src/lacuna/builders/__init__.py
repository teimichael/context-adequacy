from lacuna.builders.base import BuildResult, ContextBuilder
from lacuna.builders.bm25 import LexicalBM25Builder
from lacuna.builders.closure_first import ClosureFirstBuilder
from lacuna.builders.dependence_1hop import DependenceOneHopBuilder
from lacuna.builders.safety import BuilderCriterion
from lacuna.builders.target_only import TargetOnlyBuilder
BUILDERS: dict[str, type[ContextBuilder]] = {'target-only': TargetOnlyBuilder, 'lexical-bm25': LexicalBM25Builder, 'dependence-1hop': DependenceOneHopBuilder, 'closure-first': ClosureFirstBuilder}
__all__ = ['BUILDERS', 'BuildResult', 'BuilderCriterion', 'ClosureFirstBuilder', 'ContextBuilder', 'DependenceOneHopBuilder', 'LexicalBM25Builder', 'TargetOnlyBuilder']
