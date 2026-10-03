from __future__ import annotations
from collections.abc import Iterable
from typing import TYPE_CHECKING
from lacuna.model.approx import FrameDefinition
from lacuna.model.identity import EdgeKind, ElementId, ElementKind
from lacuna.model.index import ProgramIndex
if TYPE_CHECKING:
    from lacuna.model.edg import ElementDependenceGraph

class TargetNotIndexed(KeyError):
    pass

def frame(target: str, index: ProgramIndex, definition: FrameDefinition) -> set[str]:
    tid = ElementId(target)
    out: set[str] = set()
    if tid.kind is ElementKind.METHOD:
        info = index.methods.get(target)
        if info is None:
            raise TargetNotIndexed(f'method not in index: {target}')
        owner = info.owner
        out.update(index.declared_state(owner))
        out.update(_as_type_ids(info.parameter_types))
        if info.return_type:
            out.update(_as_type_ids([info.return_type]))
        out.update(_as_type_ids(info.thrown_types))
        out.add(owner)
    elif tid.kind is ElementKind.TYPE:
        if target not in index.types:
            raise TargetNotIndexed(f'type not in index: {target}')
        out.update(index.declared_state(target))
        out.add(target)
        for m in index.types[target].methods:
            mi = index.methods.get(m)
            if mi is None:
                continue
            out.update(_as_type_ids(mi.parameter_types))
            if mi.return_type:
                out.update(_as_type_ids([mi.return_type]))
            out.update(_as_type_ids(mi.thrown_types))
    else:
        if target not in index.members:
            raise TargetNotIndexed(f'field not in index: {target}')
        out.add(target)
        out.add(str(tid.declaring_type))
    if definition is FrameDefinition.WIDE:
        out |= _widen(out, index)
    return out

def _as_type_ids(refs: tuple[str, ...] | list[str]) -> set[str]:
    out: set[str] = set()
    for r in refs:
        if not r:
            continue
        if r.startswith('T:'):
            out.add(r)
        elif r[0].isupper() or '.' in r:
            out.add(str(ElementId.type_(r)))
    return out

def _widen(base: set[str], index: ProgramIndex) -> set[str]:
    extra: set[str] = set()
    for element in list(base):
        eid = ElementId(element)
        if eid.kind is not ElementKind.TYPE:
            continue
        info = index.types.get(element)
        if info is None:
            continue
        extra.update(info.fields)
        extra.update(info.methods)
        for imported in info.imported_types:
            imp = index.types.get(imported)
            if imp is None:
                extra.add(imported)
                continue
            extra.add(imported)
            extra.update((f for f in imp.fields if index.members.get(f, None) is None or index.members[f].static))
    return extra

def fields_without_writers(frame_set: Iterable[str], graph: ElementDependenceGraph) -> list[str]:
    out: list[str] = []
    for element in sorted(frame_set):
        if ElementId(element).kind is not ElementKind.FIELD:
            continue
        if element not in graph:
            continue
        kinds = {k for ks in graph.successors(element).values() for k in ks}
        if EdgeKind.HEAP_WRITE not in kinds:
            out.append(element)
    return out
