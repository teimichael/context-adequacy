from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, ConfigDict
from lacuna.criterion.closure import ClosureResult
from lacuna.model.edg import UnknownReason

class VerdictValue(str, Enum):
    ADEQUATE = 'ADEQUATE'
    ADEQUATE_UP_TO_K = 'ADEQUATE_UP_TO_K'
    INADEQUATE = 'INADEQUATE'
    UNKNOWN = 'UNKNOWN'

    @property
    def favourable(self) -> bool:
        return self in (VerdictValue.ADEQUATE, VerdictValue.ADEQUATE_UP_TO_K)

class Verdict(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: VerdictValue
    omit: tuple[str, ...] = ()
    reasons: tuple[UnknownReason, ...] = ()
    level: int | None = None
    depth: int | None = None
    depth_basis: str = 'seed'

    def render_headline(self) -> str:
        if self.value is VerdictValue.ADEQUATE_UP_TO_K:
            return f'verdict ADEQUATE-UP-TO-{self.level}'
        if self.value is VerdictValue.UNKNOWN:
            return f"verdict UNKNOWN   ({', '.join((r.value for r in self.reasons))})"
        return f'verdict {self.value.value}'

def decide(result: ClosureResult, *, depth_cap: int, extra_reasons: frozenset[UnknownReason]=frozenset()) -> Verdict:
    reasons = tuple(sorted(result.unknown_reasons | extra_reasons, key=lambda r: r.value))
    if result.omit:
        return Verdict(value=VerdictValue.INADEQUATE, omit=tuple(sorted(result.omit)), reasons=reasons, depth=result.depth, depth_basis=result.depth_basis)
    if reasons:
        return Verdict(value=VerdictValue.UNKNOWN, reasons=reasons, depth=result.depth)
    if result.omit_truncated or result.slice_truncated:
        return Verdict(value=VerdictValue.ADEQUATE_UP_TO_K, level=depth_cap, depth=result.depth, depth_basis=result.depth_basis)
    return Verdict(value=VerdictValue.ADEQUATE, depth=result.depth)
