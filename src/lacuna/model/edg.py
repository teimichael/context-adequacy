from __future__ import annotations
import gzip
import json
from collections import defaultdict
from collections.abc import Iterable, Iterator
from enum import Enum
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field
from lacuna import SCHEMA_VERSION
from lacuna.model.identity import EdgeKind, ElementId, edge_kind_rank

class Provenance(str, Enum):
    WALA_SDG = 'wala-sdg'
    FIELD_SCAN = 'field-scan'
    CHA_OVERRIDE = 'cha-override'
    TYPE_REF = 'type-ref'
    SYNTHETIC = 'synthetic'

class UnknownReason(str, Enum):
    REFLECTION = 'reflection'
    BINDING = 'binding'
    ENTRY_POINTS = 'entry-points'
    NO_WRITER = 'no-writer'
    NATIVE = 'native'
    GENERATED = 'generated'
    TIMEOUT = 'timeout'
    OOM = 'oom'
    SEED_EXCEEDS_BUDGET = 'seed-exceeds-budget'
    UNMAPPABLE = 'unmappable'
    NO_DEBUG_INFO = 'no-debug-info'
    BRIDGE_TRUNCATED = 'bridge-truncated'
    DYNAMIC_DISPATCH_UNRESOLVED = 'dynamic-dispatch-unresolved'

class ElementRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    source_file: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    scope: str = 'app'
    in_callgraph: bool | None = None
    generated: bool = False
    synthetic: bool = False
    unknown_reasons: tuple[UnknownReason, ...] = ()

class EdgeRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    src: str
    dst: str
    kind: EdgeKind
    provenance: Provenance = Provenance.WALA_SDG

class EdgeDoesNotExist(KeyError):
    pass

class UnknownElement(KeyError):
    pass

class ElementDependenceGraph:
    __slots__ = ('_nodes', '_succ', '_pred', '_sorted')

    def __init__(self) -> None:
        self._nodes: dict[str, ElementRecord] = {}
        self._succ: dict[str, dict[str, set[EdgeKind]]] = defaultdict(dict)
        self._pred: dict[str, dict[str, set[EdgeKind]]] = defaultdict(dict)
        self._sorted = False

    def add_element(self, rec: ElementRecord) -> None:
        ElementId(rec.id)
        existing = self._nodes.get(rec.id)
        if existing is None:
            self._nodes[rec.id] = rec
        elif existing != rec:
            merged = tuple(sorted(set(existing.unknown_reasons) | set(rec.unknown_reasons)))
            self._nodes[rec.id] = existing.model_copy(update={'unknown_reasons': merged, 'source_file': existing.source_file or rec.source_file, 'start_line': existing.start_line or rec.start_line, 'end_line': existing.end_line or rec.end_line})
        self._sorted = False

    def add_edge(self, src: str, dst: str, kind: EdgeKind, provenance: Provenance=Provenance.WALA_SDG) -> None:
        if src not in self._nodes:
            self.add_element(ElementRecord(id=src))
        if dst not in self._nodes:
            self.add_element(ElementRecord(id=dst))
        if src == dst:
            return
        self._succ[src].setdefault(dst, set()).add(kind)
        self._pred[dst].setdefault(src, set()).add(kind)
        self._sorted = False
        del provenance

    def _ensure_sorted(self) -> None:
        if self._sorted:
            return
        for table in (self._succ, self._pred):
            for key in list(table):
                table[key] = dict(sorted(table[key].items(), key=lambda kv: ElementId(kv[0])))
        self._sorted = True

    def __contains__(self, eid: str) -> bool:
        return eid in self._nodes

    def __len__(self) -> int:
        return len(self._nodes)

    def nodes(self) -> Iterator[str]:
        return iter(self._nodes)

    def record(self, eid: str) -> ElementRecord:
        try:
            return self._nodes[eid]
        except KeyError as exc:
            raise UnknownElement(eid) from exc

    def successors(self, eid: str) -> dict[str, set[EdgeKind]]:
        self._ensure_sorted()
        return self._succ.get(eid, {})

    def predecessors(self, eid: str) -> dict[str, set[EdgeKind]]:
        self._ensure_sorted()
        return self._pred.get(eid, {})

    def edge_kinds(self, src: str, dst: str) -> set[EdgeKind]:
        kinds = self._succ.get(src, {}).get(dst)
        if kinds is None:
            raise EdgeDoesNotExist(f'{src} ~> {dst}')
        return kinds

    def best_kind(self, src: str, dst: str) -> EdgeKind:
        return min(self.edge_kinds(src, dst), key=edge_kind_rank)

    def edge_count(self) -> int:
        return sum((len(kinds) for tbl in self._succ.values() for kinds in tbl.values()))

    def unknown_reasons(self, elements: Iterable[str]) -> set[UnknownReason]:
        out: set[UnknownReason] = set()
        for e in elements:
            rec = self._nodes.get(e)
            if rec is not None:
                out.update(rec.unknown_reasons)
        return out

    def to_jsonl(self, path: Path) -> None:
        self._ensure_sorted()
        opener = gzip.open if path.suffix == '.gz' else open
        with opener(path, 'wt', encoding='utf-8') as fh:
            fh.write(json.dumps({'schema': SCHEMA_VERSION, 'type': 'header'}) + '\n')
            for eid in sorted(self._nodes, key=ElementId):
                fh.write(json.dumps({'type': 'node', **self._nodes[eid].model_dump(mode='json')}) + '\n')
            for src in sorted(self._succ, key=ElementId):
                for dst, kinds in self._succ[src].items():
                    for kind in sorted(kinds, key=edge_kind_rank):
                        fh.write(json.dumps({'type': 'edge', 'src': src, 'dst': dst, 'kind': kind.value}) + '\n')

    @classmethod
    def from_jsonl(cls, path: Path) -> ElementDependenceGraph:
        opener = gzip.open if path.suffix == '.gz' else open
        g = cls()
        seen_header = False
        with opener(path, 'rt', encoding='utf-8') as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                kind = obj.get('type')
                if kind == 'header':
                    if obj.get('schema') != SCHEMA_VERSION:
                        raise ValueError(f"{path}: EDG schema {obj.get('schema')!r} != expected {SCHEMA_VERSION!r}")
                    seen_header = True
                elif kind == 'node':
                    g.add_element(ElementRecord.model_validate({k: v for k, v in obj.items() if k != 'type'}))
                elif kind == 'edge':
                    g.add_edge(obj['src'], obj['dst'], EdgeKind(obj['kind']))
                else:
                    raise ValueError(f'{path}:{lineno}: unknown record type {kind!r}')
        if not seen_header:
            raise ValueError(f'{path}: missing schema header record')
        return g

    @classmethod
    def from_edges(cls, edges: Iterable[tuple[str, str, EdgeKind]]) -> ElementDependenceGraph:
        g = cls()
        for src, dst, kind in edges:
            g.add_edge(src, dst, kind)
        return g

class EdgSummary(BaseModel):
    elements: int = Field(ge=0)
    edges: int = Field(ge=0)
    by_kind: dict[str, int] = {}
    unknown_elements: int = Field(default=0, ge=0)

def summarise(g: ElementDependenceGraph) -> EdgSummary:
    by_kind: dict[str, int] = defaultdict(int)
    for src in g.nodes():
        for dst, kinds in g.successors(src).items():
            del dst
            for k in kinds:
                by_kind[k.value] += 1
    unknown = sum((1 for n in g.nodes() if g.record(n).unknown_reasons))
    return EdgSummary(elements=len(g), edges=g.edge_count(), by_kind=dict(by_kind), unknown_elements=unknown)
