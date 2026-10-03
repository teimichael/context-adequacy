from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from tree_sitter import Node
from lacuna.mapper import FileMap, _text
from lacuna.model.edg import ElementDependenceGraph
from lacuna.model.identity import ElementId, ElementKind
NULLITY_ANNOTATIONS: frozenset[str] = frozenset({'NonNull', 'Nonnull', 'NotNull'})
CHECK_METHODS: frozenset[str] = frozenset({'requireNonNull', 'checkNotNull', 'checkArgument', 'checkState', 'notNull', 'isTrue'})
GUARD_EXCEPTIONS: frozenset[str] = frozenset({'IllegalArgumentException', 'IllegalStateException', 'NullPointerException'})
_TYPE_NODES = {'class_declaration', 'interface_declaration', 'enum_declaration', 'record_declaration', 'annotation_type_declaration'}

class InvariantKind(str, Enum):
    NULLITY_ANNOTATION = 'nullity_annotation'
    ASSERTION = 'assertion'
    PRECONDITION_CHECK = 'precondition_check'
    GUARDED_THROW = 'guarded_throw'

@dataclass(frozen=True)
class DeclaredInvariant:
    kind: InvariantKind
    owner: str
    source_file: str
    line: int
    text: str
    read_names: frozenset[str]

    def __post_init__(self) -> None:
        if not self.read_names:
            raise ValueError(f'a declared invariant with an empty read set is not usable as a seed: {self.kind.value} at {self.source_file}:{self.line}')

def _enclosing_type(node: Node, src: bytes, package: str) -> str:
    stack: list[str] = []
    cur: Node | None = node
    while cur is not None:
        if cur.type in _TYPE_NODES:
            nm = cur.child_by_field_name('name')
            if nm is not None:
                stack.append(_text(nm, src))
        cur = cur.parent
    return '.'.join(filter(None, [package, *reversed(stack)]))

def _annotation_names(node: Node, src: bytes) -> set[str]:
    out: set[str] = set()
    mods = next((c for c in node.children if c.type == 'modifiers'), None)
    if mods is None:
        return out
    for ch in mods.children:
        if ch.type not in ('marker_annotation', 'annotation'):
            continue
        nm = ch.child_by_field_name('name')
        if nm is not None:
            out.add(_text(nm, src).rsplit('.', 1)[-1])
    return out

def _names_read(node: Node, src: bytes) -> set[str]:
    out: set[str] = set()

    def walk(n: Node) -> None:
        if n.type == 'identifier':
            out.add(_text(n, src))
        elif n.type == 'field_access':
            fld = n.child_by_field_name('field')
            if fld is not None:
                out.add(_text(fld, src))
            obj = n.child_by_field_name('object')
            if obj is not None and obj.type != 'this':
                walk(obj)
            return
        elif n.type == 'method_invocation':
            for field in ('object', 'arguments'):
                ch = n.child_by_field_name(field)
                if ch is not None:
                    walk(ch)
            return
        for ch in n.children:
            walk(ch)
    walk(node)
    return out

def _throws_guard_exception(node: Node, src: bytes) -> bool:
    found = False

    def walk(n: Node) -> None:
        nonlocal found
        if found:
            return
        if n.type == 'throw_statement':
            text = _text(n, src)
            if any((e in text for e in GUARD_EXCEPTIONS)):
                found = True
                return
        for ch in n.children:
            walk(ch)
    walk(node)
    return found

def invariants_in_file(fm: FileMap, *, owner: str | None=None) -> list[DeclaredInvariant]:
    from lacuna.mapper import _parser
    src = fm.text
    tree = _parser().parse(src)
    field_names: dict[str, set[str]] = {}
    for d in fm.declarations:
        if d.kind is ElementKind.FIELD:
            field_names.setdefault(d.owner, set()).add(d.name)
    found: list[DeclaredInvariant] = []

    def emit(kind: InvariantKind, node: Node, names: set[str]) -> None:
        enc = _enclosing_type(node, src, fm.package)
        if owner is not None and (not _same_type(owner, enc)):
            return
        reads = names & field_names.get(enc, set())
        if not reads:
            return
        found.append(DeclaredInvariant(kind=kind, owner=enc, source_file=fm.path, line=node.start_point[0] + 1, text=' '.join(_text(node, src).split())[:200], read_names=frozenset(reads)))

    def walk(n: Node) -> None:
        if n.type in ('field_declaration', 'constant_declaration'):
            if _annotation_names(n, src) & NULLITY_ANNOTATIONS:
                declared = {_text(nm, src) for ch in n.named_children if ch.type == 'variable_declarator' and (nm := ch.child_by_field_name('name')) is not None}
                emit(InvariantKind.NULLITY_ANNOTATION, n, declared)
        elif n.type == 'assert_statement':
            emit(InvariantKind.ASSERTION, n, _names_read(n, src))
        elif n.type == 'method_invocation':
            nm = n.child_by_field_name('name')
            if nm is not None and _text(nm, src) in CHECK_METHODS:
                args = n.child_by_field_name('arguments')
                if args is not None:
                    emit(InvariantKind.PRECONDITION_CHECK, n, _names_read(args, src))
        elif n.type == 'if_statement':
            cons = n.child_by_field_name('consequence')
            cond = n.child_by_field_name('condition')
            if cons is not None and cond is not None and _throws_guard_exception(cons, src):
                emit(InvariantKind.GUARDED_THROW, n, _names_read(cond, src))
        for ch in n.children:
            walk(ch)
    walk(tree.root_node)
    return found

def read_set_elements(graph: ElementDependenceGraph, owner: str, invariants: list[DeclaredInvariant]) -> set[str]:
    names: set[str] = set()
    for inv in invariants:
        names |= inv.read_names
    if not names:
        return set()
    out: set[str] = set()
    for node in graph.nodes():
        if not node.startswith('F:'):
            continue
        eid = ElementId(node)
        if eid.name in names and _same_type(eid.owner, owner):
            out.add(node)
    return out

def _same_type(analyser_owner: str, source_owner: str) -> bool:
    return source_owner in (analyser_owner, analyser_owner.replace('$', '.'))

@dataclass(frozen=True)
class InvariantScan:
    invariants: tuple[DeclaredInvariant, ...]
    read_set: frozenset[str]
    scanned: bool
    note: str = ''

    @property
    def applicable(self) -> bool:
        return self.scanned and bool(self.read_set)

    def kind_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for inv in self.invariants:
            out[inv.kind.value] = out.get(inv.kind.value, 0) + 1
        return dict(sorted(out.items()))

def scan_target(index, graph: ElementDependenceGraph, target: str) -> InvariantScan:
    owner = ElementId(target).owner
    decl = index.by_element.get(target)
    if decl is None:
        return InvariantScan((), frozenset(), False, f'{target} has no source declaration')
    fm = index.files.get(decl.source_file)
    if fm is None:
        return InvariantScan((), frozenset(), False, f'source file {decl.source_file} not in the index')
    invariants = tuple(invariants_in_file(fm, owner=owner))
    reads = read_set_elements(graph, owner, list(invariants))
    return InvariantScan(invariants, frozenset(reads), True)
