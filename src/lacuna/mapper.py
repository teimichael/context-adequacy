from __future__ import annotations
import bisect
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
import tree_sitter_java
from tree_sitter import Language, Node, Parser
from lacuna.model.identity import ElementId, ElementKind
_JAVA = Language(tree_sitter_java.language())

def _parser() -> Parser:
    return Parser(_JAVA)

class MappingError(RuntimeError):
    pass

@dataclass(frozen=True)
class Declaration:
    kind: ElementKind
    name: str
    owner: str
    param_count: int
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    source_file: str

    def contains_line(self, line: int) -> bool:
        return self.start_line <= line <= self.end_line

    @property
    def span(self) -> int:
        return self.end_line - self.start_line + 1

@dataclass
class FileMap:
    path: str
    text: bytes
    package: str
    declarations: list[Declaration] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)

    def innermost_at(self, line: int) -> Declaration | None:
        best: Declaration | None = None
        for d in self.declarations:
            if not d.contains_line(line):
                continue
            if best is None or d.span < best.span:
                best = d
        return best

    def source_of(self, d: Declaration) -> str:
        return self.text[d.start_byte:d.end_byte].decode('utf-8', errors='replace')
_TYPE_NODES = {'class_declaration', 'interface_declaration', 'enum_declaration', 'record_declaration', 'annotation_type_declaration'}
_METHOD_NODES = {'method_declaration', 'constructor_declaration', 'compact_constructor_declaration'}

def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode('utf-8', errors='replace')

def parse_file(path: Path, root: Path) -> FileMap:
    src = path.read_bytes()
    tree = _parser().parse(src)
    rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
    package = ''
    imports: list[str] = []
    decls: list[Declaration] = []

    def type_name_stack(node: Node) -> list[str]:
        out: list[str] = []
        cur: Node | None = node
        while cur is not None:
            if cur.type in _TYPE_NODES:
                nm = cur.child_by_field_name('name')
                if nm is not None:
                    out.append(_text(nm, src))
            cur = cur.parent
        return list(reversed(out))

    def walk(node: Node) -> None:
        nonlocal package
        if node.type == 'package_declaration':
            for ch in node.named_children:
                if ch.type in ('scoped_identifier', 'identifier'):
                    package = _text(ch, src)
        elif node.type == 'import_declaration':
            for ch in node.named_children:
                if ch.type in ('scoped_identifier', 'identifier'):
                    imports.append(_text(ch, src))
        if node.type in _TYPE_NODES:
            nm = node.child_by_field_name('name')
            if nm is not None:
                stack = type_name_stack(node)
                owner = '.'.join(filter(None, [package] + stack[:-1]))
                decls.append(Declaration(kind=ElementKind.TYPE, name=stack[-1], owner=owner, param_count=0, start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1, start_byte=node.start_byte, end_byte=node.end_byte, source_file=rel))
        elif node.type in _METHOD_NODES:
            nm = node.child_by_field_name('name')
            params = node.child_by_field_name('parameters')
            n_params = 0
            if params is not None:
                n_params = sum((1 for c in params.named_children if c.type in ('formal_parameter', 'spread_parameter', 'receiver_parameter') and c.type != 'receiver_parameter'))
            stack = type_name_stack(node)
            owner = '.'.join(filter(None, [package] + stack))
            name = _text(nm, src) if nm is not None else stack[-1] if stack else '<init>'
            if node.type in ('constructor_declaration', 'compact_constructor_declaration'):
                name = '<init>'
            decls.append(Declaration(kind=ElementKind.METHOD, name=name, owner=owner, param_count=n_params, start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1, start_byte=node.start_byte, end_byte=node.end_byte, source_file=rel))
        elif node.type == 'enum_constant':
            nm = node.child_by_field_name('name')
            if nm is not None:
                stack = type_name_stack(node)
                owner = '.'.join(filter(None, [package] + stack))
                decls.append(Declaration(kind=ElementKind.FIELD, name=_text(nm, src), owner=owner, param_count=0, start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1, start_byte=node.start_byte, end_byte=node.end_byte, source_file=rel))
        elif node.type in ('field_declaration', 'constant_declaration'):
            stack = type_name_stack(node)
            owner = '.'.join(filter(None, [package] + stack))
            for ch in node.named_children:
                if ch.type != 'variable_declarator':
                    continue
                nm = ch.child_by_field_name('name')
                if nm is None:
                    continue
                decls.append(Declaration(kind=ElementKind.FIELD, name=_text(nm, src), owner=owner, param_count=0, start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1, start_byte=node.start_byte, end_byte=node.end_byte, source_file=rel))
        for ch in node.children:
            walk(ch)
    walk(tree.root_node)
    return FileMap(path=rel, text=src, package=package, declarations=decls, imports=imports)

class SourceIndex:

    def __init__(self) -> None:
        self.files: dict[str, FileMap] = {}
        self.by_element: dict[str, Declaration] = {}
        self.unmappable: dict[str, str] = {}
        self._join: dict[tuple[str, str, int], list[Declaration]] = {}

    @classmethod
    def parse_roots(cls, roots: Iterable[Path]) -> SourceIndex:
        idx = cls()
        for root in roots:
            root = Path(root)
            if not root.is_dir():
                continue
            for p in sorted(root.rglob('*.java')):
                try:
                    fm = parse_file(p, root)
                except Exception as exc:
                    raise MappingError(f'failed to parse {p}: {exc}') from exc
                idx.files[fm.path] = fm
                for d in fm.declarations:
                    idx._join.setdefault((d.source_file, d.name, d.param_count), []).append(d)
        return idx

    def join_to_elements(self, elements: Iterable[tuple[str, str | None, int | None]]) -> tuple[int, int]:
        matched = unmatched = 0
        for eid, src_file, line in elements:
            e = ElementId(eid)
            if e.kind is ElementKind.TYPE:
                names = _type_name_candidates(e.owner)
            else:
                names = [e.name]
            if e.kind is ElementKind.METHOD:
                n_params = _descriptor_arity(e.descriptor)
            else:
                n_params = 0
            if names[-1].isdigit():
                self.unmappable.setdefault(eid, 'anonymous-class')
                unmatched += 1
                continue
            if _is_compiler_synthesised(e):
                self.unmappable.setdefault(eid, 'compiler-synthesised')
                unmatched += 1
                continue
            if e.kind is ElementKind.METHOD and e.name == '<clinit>':
                self.unmappable.setdefault(eid, 'static-initialiser')
                unmatched += 1
                continue
            cands: list[Declaration] = []
            for candidate_name in names:
                if src_file:
                    cands = list(self._join.get((src_file, candidate_name, n_params), []))
                if not cands:
                    for (f, nm, pc), ds in self._join.items():
                        if nm == candidate_name and pc == n_params and (src_file is None or f.endswith(src_file) or src_file.endswith(f)):
                            cands.extend(ds)
                if cands:
                    break
            if not cands:
                if e.kind is ElementKind.METHOD and e.name == '<init>':
                    self.unmappable.setdefault(eid, 'implicit-constructor')
                else:
                    self.unmappable.setdefault(eid, 'no-declaration-found')
                unmatched += 1
                continue
            if line and len(cands) > 1:
                cands.sort(key=lambda d: abs(d.start_line - line))
            self.by_element[eid] = cands[0]
            matched += 1
        return (matched, unmatched)

    def source_of(self, element_id: str) -> str | None:
        d = self.by_element.get(element_id)
        if d is None:
            return None
        fm = self.files.get(d.source_file)
        if fm is None:
            return None
        if d.kind is ElementKind.TYPE:
            return self._shell(fm, d)
        return fm.source_of(d)

    @staticmethod
    def _shell(fm: FileMap, d: Declaration) -> str:
        inner = sorted((m for m in fm.declarations if m.kind is ElementKind.METHOD and m.start_byte >= d.start_byte and (m.end_byte <= d.end_byte) and (not (m.start_byte == d.start_byte and m.end_byte == d.end_byte))), key=lambda m: m.start_byte)
        out: list[bytes] = []
        cursor = d.start_byte
        for m in inner:
            if m.start_byte < cursor:
                continue
            out.append(fm.text[cursor:m.start_byte])
            cursor = m.end_byte
        out.append(fm.text[cursor:d.end_byte])
        return b''.join(out).decode('utf-8', errors='replace')

    def elements_covering(self, source_file: str, lines: Sequence[int]) -> set[str]:
        fm = self.files.get(source_file)
        if fm is None:
            for path, candidate in self.files.items():
                if path.endswith(source_file) or source_file.endswith(path):
                    fm = candidate
                    break
        if fm is None:
            return set()
        rev = {id(d): eid for eid, d in self.by_element.items()}
        out: set[str] = set()
        for line in lines:
            d = fm.innermost_at(line)
            if d is None:
                continue
            eid = rev.get(id(d))
            if eid is not None:
                out.add(eid)
        return out

def _type_name_candidates(fqcn: str) -> list[str]:
    simple = fqcn.rsplit('.', 1)[-1]
    out = [simple]
    parts = simple.split('$')
    for i in range(1, len(parts)):
        candidate = '$'.join(parts[i:])
        if candidate and candidate not in out:
            out.append(candidate)
    return out

def _is_compiler_synthesised(e: ElementId) -> bool:
    if e.name.startswith('$') or '$VALUES' in e.name:
        return True
    if e.name.startswith('this$') or e.name.startswith('val$'):
        return True
    if e.kind is ElementKind.METHOD and e.name in ('values', 'valueOf'):
        return e.descriptor.startswith('()[') or e.descriptor.startswith('(Ljava/lang/String;)')
    if e.name.startswith('access$') or e.name.startswith('lambda$'):
        return True
    return False

def _descriptor_arity(descriptor: str) -> int:
    inner = descriptor[descriptor.index('(') + 1:descriptor.index(')')]
    i = 0
    n = 0
    while i < len(inner):
        c = inner[i]
        if c == '[':
            i += 1
            continue
        if c == 'L':
            i = inner.index(';', i) + 1
        else:
            i += 1
        n += 1
    return n

def hunk_lines(patch: str) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    current: str | None = None
    old_line = 0
    hunk_start = 1
    for raw in patch.splitlines():
        if raw.startswith('diff --git '):
            current = None
        elif raw.startswith('+++'):
            continue
        elif raw.startswith('---'):
            name = raw[3:].strip()
            current = name[2:] if name.startswith('a/') else name
        elif raw.startswith('@@'):
            try:
                seg = raw.split('-', 1)[1].split(' ', 1)[0]
                old_line = int(seg.split(',')[0])
            except (IndexError, ValueError):
                old_line = 1
            hunk_start = old_line
        elif current and raw.startswith('-'):
            out.setdefault(current, []).append(old_line)
            old_line += 1
        elif current and raw.startswith('+'):
            out.setdefault(current, []).append(max(old_line - 1, hunk_start))
        elif current:
            old_line += 1
    for k in out:
        out[k] = sorted(set(out[k]))
    return out

def bisect_span(sorted_lines: list[int], lo: int, hi: int) -> list[int]:
    i = bisect.bisect_left(sorted_lines, lo)
    j = bisect.bisect_right(sorted_lines, hi)
    return sorted_lines[i:j]
