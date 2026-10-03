from __future__ import annotations
import hashlib
import json
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field

class CallGraphAlgorithm(str, Enum):
    RTA = 'RTA'
    ZERO_CFA = '0-CFA'
    ZERO_ONE_CFA = '0-1-CFA'

class DataDependence(str, Enum):
    NO_BASE_NO_HEAP = 'NO_BASE_NO_HEAP'
    NO_HEAP = 'NO_HEAP'
    NO_HEAP_NO_EXCEPTIONS = 'NO_HEAP_NO_EXCEPTIONS'

class ControlDependence(str, Enum):
    FULL = 'FULL'
    NO_EXCEPTIONAL_EDGES = 'NO_EXCEPTIONAL_EDGES'
    NONE = 'NONE'

class HeapModel(str, Enum):
    ELEMENT_FIELD_EDGES = 'element-field-edges'
    NONE = 'none'

class ContextSensitivity(str, Enum):
    NONE = 'none'
    ONE_CALL_SITE = '1-call-site'
    ONE_OBJECT = '1-object'

class EntryPointModel(str, Enum):
    DECLARED_MAINS = 'declared-mains'
    PLUS_TEST_METHODS = '+test-methods'
    PLUS_FRAMEWORK = '+framework-annotated'
    ALL_PUBLIC = 'all-public'

class ReflectionPolicy(str, Enum):
    UNRESOLVED_TO_UNKNOWN = 'unresolved->UNKNOWN'

class FrameDefinition(str, Enum):
    NARROW = 'F1-narrow'
    WIDE = 'F2-bounded-wide'

class Property(str, Enum):
    P1_CALLER_BEHAVIOUR = 'P1'
    P2_DECLARED_INVARIANT = 'P2'
    P3_SIGNATURE_TYPES = 'P3'
FAST_PATH_IGNORES: frozenset[str] = frozenset({'data_dep', 'control_dep', 'ctx_sens', 'reflection', 'analysis_timeout_s'})
CRITERION_ONLY: frozenset[str] = frozenset({'depth_cap', 'frame', 'realizable', 'summary_edges'})

class Approximation(BaseModel):
    model_config = ConfigDict(frozen=True)
    engine: str = 'WALA'
    engine_revision: str = '1.6.10'
    cg: CallGraphAlgorithm = CallGraphAlgorithm.RTA
    cha_augmented: bool = True
    data_dep: DataDependence = DataDependence.NO_BASE_NO_HEAP
    control_dep: ControlDependence = ControlDependence.FULL
    heap_model: HeapModel = HeapModel.ELEMENT_FIELD_EDGES
    field_sens: bool = True
    ctx_sens: ContextSensitivity = ContextSensitivity.NONE
    entry_points: EntryPointModel = EntryPointModel.ALL_PUBLIC
    reflection: ReflectionPolicy = ReflectionPolicy.UNRESOLVED_TO_UNKNOWN
    depth_cap: int = Field(default=3, ge=0, le=64)
    frame: FrameDefinition = FrameDefinition.NARROW
    realizable: bool = True
    summary_edges: bool = True
    heap_exclusions_sha256: str = ''
    jdk_version: str = '21'
    stdlib_substituted: bool = False
    analysis_timeout_s: int = Field(default=600, ge=1)

    def digest(self) -> str:
        payload = json.dumps(self.model_dump(mode='json'), sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def analyser_digest(self) -> str:
        payload = json.dumps({k: v for k, v in self.model_dump(mode='json').items() if k in ANALYSER_RELEVANT}, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def render_parts(self) -> list[tuple[str, str]]:
        return [('cg', f"cg={self.cg.value}{('+CHA' if self.cha_augmented else '')}"), ('field_sens', f"field-{('' if self.field_sens else 'in')}sensitive"), ('ctx_sens', f"context-{('in' if self.ctx_sens is ContextSensitivity.NONE else '')}sensitive"), ('entry_points', f'entry-points={self.entry_points.value}'), ('reflection', f'reflection={self.reflection.value}'), ('data_dep', f'data={self.data_dep.value}'), ('heap_model', f'heap={self.heap_model.value}'), ('control_dep', 'control=' + ('termination-sensitive' if self.control_dep is ControlDependence.FULL else self.control_dep.value)), ('frame', f'frame={self.frame.value}'), ('depth_cap', f'depth-cap k={self.depth_cap}'), ('realizable', 'paths=' + ('realizable' if self.realizable else 'unrestricted')), ('summary_edges', 'summary-edges=' + ('on' if self.summary_edges else 'off'))]

    def render(self) -> str:
        parts = [text for _, text in self.render_parts()]
        return '{ ' + ',\n                  '.join(parts) + ' }'

class CriterionLevel(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    max_distance: int = Field(ge=0)
    kinds: tuple[str, ...] | None = None
    description: str = ''
LEVELS: tuple[CriterionLevel, ...] = (CriterionLevel(name='A0', max_distance=0, description='seed (frame union observation points)'), CriterionLevel(name='A1', max_distance=1, kinds=('data', 'control', 'call', 'return', 'heap-read', 'heap-write'), description='+ direct callers/callees'), CriterionLevel(name='A2', max_distance=2, kinds=('data', 'control', 'call', 'return', 'heap-read', 'heap-write'), description='+ depth-2 field writers'), CriterionLevel(name='A3', max_distance=3, description='+ dispatch-closed (CHA)'))
ANALYSER_RELEVANT: frozenset[str] = frozenset(Approximation.model_fields) - FAST_PATH_IGNORES - CRITERION_ONLY
assert ANALYSER_RELEVANT | FAST_PATH_IGNORES | CRITERION_ONLY == frozenset(Approximation.model_fields), 'every field of A must be analyser-relevant, FAST-path-ignored, or criterion-only'
assert not ANALYSER_RELEVANT & FAST_PATH_IGNORES, 'a field cannot be both'
assert not ANALYSER_RELEVANT & CRITERION_ONLY, 'a field cannot be both'
assert not FAST_PATH_IGNORES & CRITERION_ONLY, 'a field cannot be both'
DEFAULT_APPROXIMATION = Approximation()
