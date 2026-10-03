from __future__ import annotations
from lacuna.criterion.closure import ClosureResult
from lacuna.criterion.verdict import Verdict, VerdictValue
from lacuna.criterion.witness import Witness
from lacuna.model.approx import FAST_PATH_IGNORES, Approximation, ContextSensitivity, Property, ReflectionPolicy
from lacuna.model.edg import ElementDependenceGraph
from lacuna.model.identity import ElementId, ElementKind
PROPERTY_NAMES = {Property.P1_CALLER_BEHAVIOUR: 'caller-behavior', Property.P2_DECLARED_INVARIANT: 'declared-invariant-preservation', Property.P3_SIGNATURE_TYPES: 'signature-type-correctness'}
COMPACT_WIDTH = 100
NOT_EMITTED: frozenset[str] = frozenset({'data_dep', 'control_dep'})
HARD_WIRED: dict[str, object] = {'ctx_sens': ContextSensitivity.NONE, 'reflection': ReflectionPolicy.UNRESOLVED_TO_UNKNOWN}
HARD_WIRED_LABEL = 'hard-wired'
NOT_EMITTED_LABEL = 'not emitted'
NOT_READ_LABEL = 'recorded, not read'
_APPROX_HEAD = '  approx   A  = { '
_APPROX_INDENT = ' ' * len(_APPROX_HEAD)

def approx_groups(approx: Approximation) -> list[tuple[str | None, list[str]]]:
    acting: list[str] = []
    marked: dict[str, list[str]] = {HARD_WIRED_LABEL: [], NOT_EMITTED_LABEL: [], NOT_READ_LABEL: []}
    for field, text in approx.render_parts():
        if field not in FAST_PATH_IGNORES:
            acting.append(text + " (two-phase)" if field == "realizable" and approx.realizable else text)
        elif field in NOT_EMITTED:
            marked[NOT_EMITTED_LABEL].append(field.removesuffix("_dep"))
        elif field in HARD_WIRED and getattr(approx, field) == HARD_WIRED[field]:
            marked[HARD_WIRED_LABEL].append(text)
        else:
            marked[NOT_READ_LABEL].append(text)
    marked[NOT_EMITTED_LABEL].append("exception")
    out: list[tuple[str | None, list[str]]] = [(None, acting)]
    out.extend(((label, parts) for label, parts in marked.items() if parts))
    return out

def _render_approx(approx: Approximation, *, compact: bool) -> list[str]:
    groups = [(label, parts) for label, parts in approx_groups(approx) if parts]
    lines: list[str] = []
    if not compact:
        items = [text + ('' if label is None else f' ({label})') for label, parts in groups for text in parts]
        for n, item in enumerate(items):
            head = _APPROX_HEAD if n == 0 else _APPROX_INDENT
            lines.append(head + item + (',' if n < len(items) - 1 else ' }'))
        return lines
    for g, (label, parts) in enumerate(groups):
        last_group = g == len(groups) - 1
        pieces = []
        for n, text in enumerate(parts):
            piece = (f'{label}: ' if label is not None and n == 0 else '') + text
            if n < len(parts) - 1:
                piece += ','
            else:
                piece += ' }' if last_group else ';'
            pieces.append(piece)
        cur = _APPROX_HEAD if g == 0 else _APPROX_INDENT
        fresh = True
        for piece in pieces:
            if not fresh and len(cur) + 1 + len(piece) > COMPACT_WIDTH:
                lines.append(cur)
                cur, fresh = (_APPROX_INDENT, True)
            cur = cur + ('' if fresh else ' ') + piece
            fresh = False
        lines.append(cur)
    return lines

def ordered_omissions(result: ClosureResult, verdict: Verdict) -> list[tuple[str, str]]:
    if verdict.depth_basis == 'context':
        dist = result.omit_dist
        beyond = 'dist=inf'
    else:
        dist = result.slice_dist
        if result.slice_truncated and result.slice_dist:
            beyond = f'dist>{max(result.slice_dist.values())}'
        else:
            beyond = 'dist=inf'

    def key(eid: str) -> tuple[float, ElementId]:
        return (float(dist[eid]) if eid in dist else float('inf'), ElementId(eid))
    return [(eid, f'dist={dist[eid]}' if eid in dist else beyond) for eid in sorted(verdict.omit, key=key)]

def _pretty(eid: str) -> str:
    e = ElementId(eid)
    if e.kind is ElementKind.TYPE:
        return e.owner
    if e.kind is ElementKind.FIELD:
        return f'{e.owner}.{e.name}'
    params = e.descriptor.split(')')[0].lstrip('(')
    return f'{e.owner}#{e.name}({_simplify_params(params)})'

def _simplify_params(desc: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(desc):
        c = desc[i]
        if c == 'L':
            j = desc.index(';', i)
            out.append(desc[i + 1:j].replace('/', '.').split('.')[-1])
            i = j + 1
        elif c == '[':
            i += 1
            continue
        else:
            out.append({'I': 'int', 'J': 'long', 'Z': 'boolean', 'D': 'double', 'F': 'float', 'B': 'byte', 'C': 'char', 'S': 'short'}.get(c, c))
            i += 1
    return ','.join(out)

def _short(loc: str) -> str:
    return loc.rsplit('/', 1)[-1] if loc else loc

def _loc(graph: ElementDependenceGraph, eid: str) -> str:
    try:
        rec = graph.record(eid)
    except KeyError:
        return ''
    if rec.source_file and rec.start_line:
        return f'{rec.source_file}:{rec.start_line}'
    return rec.source_file or ''

def render_verdict(graph: ElementDependenceGraph, target: str, prop: Property, approx: Approximation, result: ClosureResult, verdict: Verdict, witness: Witness | None, *, levels_satisfied: str | None=None, compact: bool=False) -> str:
    lines: list[str] = []
    head = verdict.render_headline()
    if levels_satisfied:
        head = f'{head}   ({levels_satisfied})'
    lines.append(head)
    tloc = _loc(graph, target)
    if compact:
        lines.append(f'  target   t = {_pretty(target)}' + (f'   {_short(tloc)}' if tloc else ''))
    else:
        lines.append(f'  target   t = {_pretty(target)}')
        if tloc:
            lines.append(f'             {tloc}')
    lines.append(f'  property {prop.value} = {PROPERTY_NAMES[prop]}')
    lines.extend(_render_approx(approx, compact=compact))
    frame_ids = sorted(result.seed, key=ElementId)
    frame_fields = [f for f in frame_ids if ElementId(f).kind is ElementKind.FIELD]
    depth_txt = 'inf' if verdict.depth is None else str(verdict.depth)
    basis = '' if verdict.depth_basis == 'seed' else f'  [basis={verdict.depth_basis}]'
    if frame_fields and compact:
        owners = {ElementId(f).owner for f in frame_fields}
        if len(owners) == 1:
            owner = owners.pop()
            names = ', '.join((f'.{ElementId(f).name}' for f in frame_fields))
            lines.append(f'  frame(t)    = {owner} {{ {names} }}')
        else:
            lines.append('  frame(t)    = { ' + ', '.join((_pretty(f) for f in frame_fields)) + ' }')
    elif frame_fields:
        lines.append('  frame(t)    = { ' + ',\n                  '.join((_pretty(f) for f in frame_fields)) + ' }')
    lines.append(f'  depth    d  = {depth_txt}{basis}')
    ordered = ordered_omissions(result, verdict)
    if verdict.value is VerdictValue.INADEQUATE:
        lines.append('')
        origin = 'seed u C' if verdict.depth_basis == 'context' else 'seed'
        lines.append(f'  omitted  Omit(t,{prop.value}) =   by dist({origin}, .), then identity')
        width = max((len(label) for _, label in ordered))
        for n, (oid, label) in enumerate(ordered, 1):
            oloc = _loc(graph, oid)
            if compact:
                lines.append(f'    [{n}] {label:<{width}}  {_pretty(oid)}' + (f'   {_short(oloc)}' if oloc else ''))
                continue
            lines.append(f'    [{n}] {label:<{width}}  {_pretty(oid)}')
            if oloc:
                lines.append(f'        {oloc}')
    if witness is not None:
        lines.append('')
        interior = 'all interior nodes in C' if witness.interior else 'NOT C-interior'
        position = [oid for oid, _ in ordered]
        idx = position.index(witness.target_omission) + 1 if witness.target_omission in position else 1
        origin = ' from seed u C' if witness.rooted_in_context else ''
        lines.append(f'  why [{idx}]  (canonical chain{origin}, distance {witness.distance}, {interior})')
        root = witness.chain[0]
        kindword = 'frame field' if ElementId(root).kind is ElementKind.FIELD else 'element'
        rloc = _loc(graph, root)
        show = _short if compact else lambda x: x
        lines.append(f'    {kindword}  {_pretty(root)}' + (f'   declared {show(rloc)}' if rloc else ''))
        for step in witness.steps:
            dloc = _loc(graph, step.dst)
            tail = f'   {show(dloc)}' if dloc else ''
            lines.append(f'      <-{step.kind.value}-  {_pretty(step.dst)}{tail}')
    if verdict.reasons:
        lines.append('')
        lines.append('  unknown reasons: ' + ', '.join((r.value for r in verdict.reasons)))
    return '\n'.join(lines)
