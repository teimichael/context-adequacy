from __future__ import annotations
from collections.abc import Sequence
from pydantic import BaseModel, ConfigDict, Field
from lacuna.corpora.contextbench import GoldContext
from lacuna.mapper import SourceIndex

class ContainmentResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    instance_id: str
    gold_elements: int = Field(ge=0)
    contained: int = Field(ge=0)
    closure_elements: int = Field(ge=0)
    unconvertible_blocks: int = Field(default=0, ge=0)

    @property
    def recall(self) -> float:
        return self.contained / self.gold_elements if self.gold_elements else float('nan')

def gold_context_elements(gold: GoldContext, index: SourceIndex) -> tuple[set[str], int]:
    out: set[str] = set()
    unconvertible = 0
    for block in gold.blocks:
        covering = index.elements_covering(block.file, block.lines())
        if covering:
            out |= covering
        else:
            unconvertible += 1
    return (out, unconvertible)

def containment(instance_id: str, gold: GoldContext, closure: Sequence[str] | set[str], index: SourceIndex) -> ContainmentResult:
    gold_elements, unconvertible = gold_context_elements(gold, index)
    closure_set = set(closure)
    return ContainmentResult(instance_id=instance_id, gold_elements=len(gold_elements), contained=len(gold_elements & closure_set), closure_elements=len(closure_set), unconvertible_blocks=unconvertible)
