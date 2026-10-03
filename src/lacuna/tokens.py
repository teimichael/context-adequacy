from __future__ import annotations
import hashlib
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field
from tokenizers import Tokenizer
TOKENIZER_PATH = Path(__file__).parent / 'resources' / 'tokenizer.json'
TOKENIZER_SHA256 = 'c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539'
TOKENIZER_NAME = 'Qwen/Qwen2.5-Coder-7B-Instruct'

class TokenizerMismatch(RuntimeError):
    pass

@lru_cache(maxsize=1)
def tokenizer() -> Tokenizer:
    if not TOKENIZER_PATH.exists():
        raise TokenizerMismatch(f"pinned tokenizer missing at {TOKENIZER_PATH}; token budgets cannot be computed and no comparison 'at equal token budget' is meaningful")
    digest = hashlib.sha256(TOKENIZER_PATH.read_bytes()).hexdigest()
    if digest != TOKENIZER_SHA256:
        raise TokenizerMismatch(f'tokenizer hash {digest} != pinned {TOKENIZER_SHA256}. Every reported token budget was measured with the pinned file; refusing to proceed.')
    return Tokenizer.from_file(str(TOKENIZER_PATH))

def count(text: str) -> int:
    if not text:
        return 0
    return len(tokenizer().encode(text, add_special_tokens=False).ids)

class Renderer(BaseModel):
    model_config = ConfigDict(frozen=True)
    with_location_header: bool = True

    def render_element(self, element_id: str, source: str, location: str | None) -> str:
        if not self.with_location_header or location is None:
            return source
        return f'// {location}\n{source}'

    def render(self, elements: Iterable[str], sources: Mapping[str, str], locations: Mapping[str, str] | None=None) -> str:
        locations = locations or {}
        parts: list[str] = []
        for eid in sorted(elements):
            src = sources.get(eid)
            if src is None:
                continue
            parts.append(self.render_element(eid, src, locations.get(eid)))
        return '\n\n'.join(parts)
DEFAULT_RENDERER = Renderer()

def tok(elements: Iterable[str], sources: Mapping[str, str], *, renderer: Renderer=DEFAULT_RENDERER, locations: Mapping[str, str] | None=None) -> int:
    return count(renderer.render(elements, sources, locations))

def missing_sources(elements: Iterable[str], sources: Mapping[str, str]) -> list[str]:
    return sorted((e for e in elements if e not in sources))

def tok_per_element(elements: Iterable[str], sources: Mapping[str, str], *, renderer: Renderer=DEFAULT_RENDERER, locations: Mapping[str, str] | None=None) -> dict[str, int]:
    locations = locations or {}
    out: dict[str, int] = {}
    for eid in elements:
        src = sources.get(eid)
        if src is None:
            continue
        out[eid] = count(renderer.render_element(eid, src, locations.get(eid)))
    return out

class BudgetAudit(BaseModel):
    model_config = ConfigDict(frozen=True)
    builder: str
    nominal_budget: int = Field(gt=0)
    realised_tokens: int = Field(ge=0)
    elements: int = Field(ge=0)
    unrenderable_elements: int = Field(default=0, ge=0)
    scaffold_tokens: int = Field(default=0, ge=0)

    @property
    def utilisation(self) -> float:
        return self.realised_tokens / self.nominal_budget

    @property
    def over_budget(self) -> bool:
        return self.realised_tokens > self.nominal_budget

    def within_tolerance(self, tolerance: float=0.05) -> bool:
        return abs(1.0 - self.utilisation) <= tolerance

def audit_ok(audits: Iterable[BudgetAudit], tolerance: float=0.05) -> tuple[bool, list[str]]:
    complaints: list[str] = []
    by_budget: dict[int, list[BudgetAudit]] = {}
    for a in audits:
        if a.over_budget:
            complaints.append(f'{a.builder} exceeded its {a.nominal_budget} budget ({a.realised_tokens} realised)')
        by_budget.setdefault(a.nominal_budget, []).append(a)
    for budget, rows in sorted(by_budget.items()):
        for row in rows:
            if not row.within_tolerance(tolerance):
                complaints.append(f'{row.builder} at {budget}: realised {row.realised_tokens} ({row.utilisation:.0%} of nominal), outside +/-{tolerance:.0%}')
    return (not complaints, complaints)
