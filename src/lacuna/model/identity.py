from __future__ import annotations
import re
from enum import Enum
from functools import total_ordering
from typing import Final
from pydantic import BaseModel, ConfigDict, field_validator

class ElementKind(str, Enum):
    METHOD = 'M'
    FIELD = 'F'
    TYPE = 'T'

class EdgeKind(str, Enum):
    DATA = 'data'
    CONTROL = 'control'
    CALL = 'call'
    RETURN = 'return'
    HEAP_READ = 'heap-read'
    HEAP_WRITE = 'heap-write'
    OVERRIDE = 'override'
    SUMMARY = 'summary'
    TYPE_REF = 'type-ref'
EDGE_KIND_ORDER: Final[dict[EdgeKind, int]] = {kind: rank for rank, kind in enumerate((EdgeKind.DATA, EdgeKind.CONTROL, EdgeKind.CALL, EdgeKind.RETURN, EdgeKind.HEAP_READ, EdgeKind.HEAP_WRITE, EdgeKind.OVERRIDE, EdgeKind.SUMMARY, EdgeKind.TYPE_REF))}
assert len(EDGE_KIND_ORDER) == len(EdgeKind), '<_kappa must be total over EdgeKind'

class MalformedElementId(ValueError):
    pass
_METHOD_RE = re.compile('^M:(?P<owner>[^#]+)#(?P<name>[^(]+)(?P<desc>\\(.*\\).+)$')
_FIELD_RE = re.compile('^F:(?P<owner>.+)\\.(?P<name>[^.:]+):(?P<desc>.+)$')
_TYPE_RE = re.compile('^T:(?P<owner>.+)$')

@total_ordering
class ElementId:
    __slots__ = ('_raw', 'kind', 'owner', 'name', 'descriptor')

    def __init__(self, raw: str) -> None:
        self._raw = raw
        if raw.startswith('M:'):
            m = _METHOD_RE.match(raw)
            if not m:
                raise MalformedElementId(f'not a method identity: {raw!r}')
            self.kind = ElementKind.METHOD
            self.owner = m['owner']
            self.name = m['name']
            self.descriptor = m['desc']
        elif raw.startswith('F:'):
            m = _FIELD_RE.match(raw)
            if not m:
                raise MalformedElementId(f'not a field identity: {raw!r}')
            self.kind = ElementKind.FIELD
            self.owner = m['owner']
            self.name = m['name']
            self.descriptor = m['desc']
        elif raw.startswith('T:'):
            m = _TYPE_RE.match(raw)
            if not m:
                raise MalformedElementId(f'not a type identity: {raw!r}')
            self.kind = ElementKind.TYPE
            self.owner = m['owner']
            self.name = ''
            self.descriptor = ''
        else:
            raise MalformedElementId(f"element identity must start with 'M:', 'F:' or 'T:': {raw!r}")

    @classmethod
    def method(cls, owner: str, name: str, descriptor: str) -> ElementId:
        return cls(f'M:{owner}#{name}{descriptor}')

    @classmethod
    def field(cls, owner: str, name: str, descriptor: str) -> ElementId:
        return cls(f'F:{owner}.{name}:{descriptor}')

    @classmethod
    def type_(cls, fqcn: str) -> ElementId:
        return cls(f'T:{fqcn}')

    @property
    def raw(self) -> str:
        return self._raw

    @property
    def fqn(self) -> str:
        if self.kind is ElementKind.TYPE:
            return self.owner
        sep = '#' if self.kind is ElementKind.METHOD else '.'
        return f'{self.owner}{sep}{self.name}'

    @property
    def declaring_type(self) -> ElementId:
        return self if self.kind is ElementKind.TYPE else ElementId.type_(self.owner)

    def __str__(self) -> str:
        return self._raw

    def __repr__(self) -> str:
        return f'ElementId({self._raw!r})'

    def __hash__(self) -> int:
        return hash(self._raw)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ElementId) and self._raw == other._raw

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, ElementId):
            return NotImplemented
        return (self.fqn, self.descriptor, self.kind.value) < (other.fqn, other.descriptor, other.kind.value)

def edge_kind_rank(kind: EdgeKind) -> int:
    return EDGE_KIND_ORDER[kind]

class Edge(BaseModel):
    model_config = ConfigDict(frozen=True)
    src: str
    dst: str
    kind: EdgeKind

    @field_validator('src', 'dst')
    @classmethod
    def _wellformed(cls, v: str) -> str:
        ElementId(v)
        return v

    def sort_key(self) -> tuple[str, str, int]:
        return (self.src, self.dst, edge_kind_rank(self.kind))
