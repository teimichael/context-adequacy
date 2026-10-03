from __future__ import annotations
import json
import re
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from lacuna.analysis.stats import cluster_bootstrap, describe, fraction_within
from lacuna.corpora.base import Status
from lacuna.criterion.closure import DIRECTIONS
from lacuna.experiment.evaluate import InstanceRecord
from lacuna.experiment.funnel import Funnel
from lacuna.model.approx import Approximation
BUDGETS = (8000, 32000, 128000)
ND = '---'
MIN_APPLICABLE = 5
FAVOURABLE_VERDICTS: frozenset[str] = frozenset({'ADEQUATE', 'ADEQUATE_UP_TO_K'})
ESCAPE_ROWS: tuple[tuple[str, str, str, str], ...] = (('dynamic_dispatch', 'Dynamic dispatch', 'CHA over-approximates; RTA narrows', 'false Inadeq. (CHA); false Adeq. possible (narrowed)'), ('reflection', 'Reflection', 'unresolved site $\\to$ \\verdict{Unknown}', 'never \\verdict{Adeq.}'), ('dependency_injection', 'Dependency injection', 'enumerated binding mechanisms parsed; else \\verdict{Unknown}', 'never \\verdict{Adeq.} if flagged'), ('framework_callbacks', 'Framework callbacks', 'entry-point model, printed in $\\apx$', '\\textbf{false \\verdict{Adeq.}} if an entry point is missing'), ('serialisation', 'Serialization', 'frame field with no syntactic writer $\\to$ \\verdict{Unknown}', 'never \\verdict{Adeq.}'), ('depth_cap', 'Depth cap', 'walk stopped at $k$; $\\to$ \\verdict{Adequate-up-to-}$k$', 'never \\verdict{Adeq.}'), ('native_code', 'Native code / JNI', '$\\to$ \\verdict{Unknown}', 'never \\verdict{Adeq.}'), ('generated_code_UNMEASURED', 'Generated sources', '$\\to$ \\verdict{Unknown} if the frame touches them', 'never \\verdict{Adeq.} if flagged'), ('analysis_timeout', 'Analysis timeout', '$\\to$ \\verdict{Unknown}, distinct status', 'never \\verdict{Adeq.}'), ('unmappable', 'Unmappable element', 'closure element with no source mapping $\\to$ \\verdict{Unknown}', 'never \\verdict{Adeq.}'), ('element_field_edges', 'Heap aliasing', 'field edges at declaration granularity; aliasing quotiented away', 'false \\verdict{Inadeq.} only (costs information)'), ('bridge_truncated', 'Bridge truncation', 'summary-edge search abandoned at \\texttt{BRIDGE\\_BUDGET}/\\texttt{BRIDGE\\_HOPS}', '\\textbf{false \\verdict{Adeq.}} possible (edges not built)'), ('shadowing_jars', 'Classpath shadowing', 'dependency jar republishing the project, dropped by class-name collision', '\\textbf{false \\verdict{Adeq.}} possible (application elements removed)'))
POST_HOC_ESCAPES: frozenset[str] = frozenset({'element_field_edges', 'bridge_truncated', 'shadowing_jars', 'unmappable'})
NO_DETECTOR_ESCAPES: frozenset[str] = frozenset({'generated_code_UNMEASURED'})
LEVEL_ROWS = (('A0', 'seed (distance 0)'), ('A1', 'distance $\\le 1$'), ('A2', 'distance $\\le 2$'), ('A3', 'distance $\\le 3$ (capped)'))
LEVEL_PLAIN = {'A0': 'seed', 'A1': 'distance <= 1', 'A2': 'distance <= 2', 'A3': 'distance <= 3 (capped)'}

def _ok(records: Sequence[InstanceRecord]) -> list[InstanceRecord]:
    return [r for r in records if r.status is Status.OK]

class StaleRecords(ValueError):
    pass

def _require_full_closure(records: Sequence[InstanceRecord]) -> None:
    stale = [r.instance_id for r in records if r.closure_elements > 0 and r.full_closure_tokens == 0]
    if stale:
        raise StaleRecords(f'{len(stale)} record(s) have a non-empty closure but no `full_closure_tokens` (e.g. {sorted(set(stale))[:3]}). These predate D40, when the full-closure rows still reported the depth-capped closure. Re-run the experiment: the analyses are cached, so only the criterion is recomputed.')

def _applicability(records: Sequence[InstanceRecord], prop: str) -> tuple[int, int, int]:
    rows = [r for r in records if r.property.value == prop]
    decided = [r for r in rows if r.applicability_scanned]
    return (sum((1 for r in decided if r.property_applicable)), len(decided), len(rows))

class BaseCellNotFound(ValueError):
    pass

def configured_base_digest(results_dir: Path) -> str | None:
    resolved = Path(results_dir) / 'config.resolved.json'
    if not resolved.exists():
        return None
    from lacuna.experiment.config import ExperimentConfig
    try:
        cfg = ExperimentConfig.model_validate_json(resolved.read_text())
    except Exception:
        return None
    cells = cfg.resolved_approximations()
    return cells[0][1].digest() if cells else None

def _base_cell(records: Sequence[InstanceRecord], base_digest: str | None=None) -> list[InstanceRecord]:
    if not records:
        return []
    if base_digest is None:
        base_digest = records[0].approximation.digest()
    rows = [r for r in records if r.approximation.digest() == base_digest]
    if not rows:
        present = sorted({r.approximation.digest() for r in records})
        raise BaseCellNotFound(f'no record carries the configured base approximation {base_digest!r}; the file holds {present}. Reporting from another cell would attribute every aggregate to the wrong configuration (D50).')
    return rows

def _pct(x: float) -> str:
    return ND if x != x else f'{100 * x:.0f}'
DIRECTION_ROWS: tuple[tuple[str, str], ...] = (('ascent', 'ascent \\textsf{(call, heap-write)}'), ('descent', 'descent \\textsf{(return, summary, heap-read)}'), ('reference', 'reference \\textsf{(type-ref)}'), ('dispatch', 'dispatch \\textsf{(override)}'), ('intra', 'intraprocedural \\textsf{(data, control)}'))
SATURATION_FRACTION = 0.9

def _saturation(rows: Sequence[InstanceRecord]) -> dict[str, Any]:
    by_repo: dict[str, list[InstanceRecord]] = defaultdict(list)
    for r in rows:
        by_repo[r.repo].append(r)
    per_repo: dict[str, Any] = {}
    saturated = 0
    for repo, rs in by_repo.items():
        el = [float(r.full_closure_elements) for r in rs]
        tok = [float(r.full_closure_tokens) for r in rs]
        d = describe(tok)
        top = max(el)
        n_sat = sum((1 for x in el if x >= SATURATION_FRACTION * top))
        saturated += n_sat
        de = describe(el)
        per_repo[repo] = {'n': len(rs), 'elements_min': min(el), 'elements_median': describe(el)['median'], 'elements_max': top, 'tokens_median': d['median'], 'tokens_iqr_over_median': d['iqr'] / d['median'] if d['median'] else None, 'elements_iqr_over_median': de['iqr'] / de['median'] if de['median'] else None, 'saturated': n_sat, 'saturated_nonref': n_sat - 1, 'n_nonref': len(rs) - 1}
    return {'per_repo': per_repo, 'saturated': saturated, 'n': len(rows), 'saturated_nonref': sum((v['saturated_nonref'] for v in per_repo.values())), 'n_nonref': sum((v['n_nonref'] for v in per_repo.values())), 'post_hoc': True, 'fraction': SATURATION_FRACTION, 'definition': 'saturated = the closure is at least 90% of the largest closure measured in the same repository, so the traversal reached nearly everything it could reach there regardless of which element it started from'}

def lever_summary(levers: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not levers:
        return {}
    out: dict[str, Any] = {'n': len(levers)}
    for key in ('1', '2', 'inf'):
        v = [float(r['horizons'][key]['closure_tokens']) for r in levers]
        crit = [float(r['horizons'][key]['crit']) for r in levers]
        out[key] = {**describe(v), 'crit_median': describe(crit)['median'], **{f'within_{b}': sum((1 for x in v if x <= b)) for b in BUDGETS}}
    sig = [float(r['horizons']['inf']['closure_tokens_signature']) for r in levers]
    out['signature'] = {**describe(sig), **{f'within_{b}': sum((1 for x in sig if x <= b)) for b in BUDGETS}}
    deltas = [float(r['horizons']['inf']['closure_tokens'] - r['horizons']['1']['closure_tokens']) for r in levers]
    ratios = [r['horizons']['1']['closure_tokens'] / r['horizons']['inf']['closure_tokens'] for r in levers if r['horizons']['inf']['closure_tokens']]
    out['horizon_one_delta_tokens_median'] = describe(deltas)['median']
    out['horizon_one_ratio_median'] = describe(ratios)['median']
    out['horizon_one_ratio_min'] = min(ratios) if ratios else None
    return out
BUILDER_ROWS: tuple[tuple[str, str], ...] = (('target-only', 'target member only'), ('lexical-bm25', 'BM25 over the issue text'), ('dependence-1hop', '1-hop dependence expansion'), ('closure-first', 'closure-first (\\cref{alg:closurefirst})$^\\ddagger$'))

def table_escapes(records: Sequence[InstanceRecord], funnel: Funnel | None=None, base_digest: str | None=None, counts_out: dict[str, Any] | None=None) -> str:
    rows = _base_cell(_ok(records), base_digest)
    totals: dict[str, int] = defaultdict(int)
    instances_firing: dict[str, int] = defaultdict(int)
    per_instance: dict[str, InstanceRecord] = {}
    for r in rows:
        per_instance.setdefault(r.instance_id, r)
    n = len(per_instance)
    for r in per_instance.values():
        for name, count in (r.escapes_fired or {}).items():
            totals[name] += int(count)
            if count:
                instances_firing[name] += 1
        if r.writerless_frame_fields:
            totals['serialisation'] += r.writerless_frame_fields
            instances_firing['serialisation'] += 1
        totals['element_field_edges'] += 1
        instances_firing['element_field_edges'] += 1
        if r.bridges_truncated:
            totals['bridge_truncated'] += r.bridges_truncated
            instances_firing['bridge_truncated'] += 1
        if r.shadowing_jars_dropped:
            shadowed = sum((int(m.group(1)) if (m := re.search('\\((\\d+) shadowed', j)) else 1 for j in r.shadowing_jars_dropped))
            totals['shadowing_jars'] += shadowed
            instances_firing['shadowing_jars'] += 1
    capped: set[str] = set()
    for r in rows:
        if r.property.value != 'P1':
            continue
        if r.verdict is not None and r.verdict.value == 'ADEQUATE_UP_TO_K' or (r.full_closure_elements and r.closure_elements < r.full_closure_elements):
            capped.add(r.instance_id)
    if capped:
        totals['depth_cap'] = len(capped)
        instances_firing['depth_cap'] = len(capped)
    unmappable = {r.instance_id for r in rows if 'unmappable' in {getattr(u, 'value', u) for u in r.unknown_reasons}}
    if unmappable:
        totals['unmappable'] = len(unmappable)
        instances_firing['unmappable'] = len(unmappable)
    if funnel is not None:
        timeouts = sum((1 for e in funnel.entries if e.status in (Status.CG_TIMEOUT, Status.ANALYSIS_OOM)))
        if timeouts:
            totals['analysis_timeout'] = timeouts
            instances_firing['analysis_timeout'] = timeouts
    if counts_out is not None:
        never = [c for key, c, _t, d in ESCAPE_ROWS if not totals.get(key, 0) and 'false \\verdict{Adeq.}' not in d and (key not in NO_DETECTOR_ESCAPES)]
        counts_out.update(declared=len(ESCAPE_ROWS), declared_before_data=sum((1 for key, *_ in ESCAPE_ROWS if key not in POST_HOC_ESCAPES)), added_after_data=sum((1 for key, *_ in ESCAPE_ROWS if key in POST_HOC_ESCAPES)), added_after_data_names=[c for key, c, *_ in ESCAPE_ROWS if key in POST_HOC_ESCAPES], no_detector=sum((1 for key, *_ in ESCAPE_ROWS if key in NO_DETECTOR_ESCAPES)), never_fired=len(never), never_fired_names=never, share={key: instances_firing.get(key, 0) / n if n else None for key, *_ in ESCAPE_ROWS}, dangerous=sum((1 for _k, _c, _t, d in ESCAPE_ROWS if 'false Adeq' in d or 'false \\verdict{Adeq' in d)))
    lines = ['% Generated by `lacuna experiment report`.', '\\begin{tabularx}{\\linewidth}{@{}>{\\raggedright\\arraybackslash}p{0.17\\linewidth}%', '>{\\raggedright\\arraybackslash}X>{\\raggedright\\arraybackslash}p{0.16\\linewidth}rr@{}}', '\\toprule', '\\textbf{Construct} & \\textbf{Treatment under $\\apx$} &', '\\textbf{Error direction} & \\textbf{fired} & \\textbf{share} \\\\', '\\midrule']
    silent = []
    undetected = []
    for key, construct, treatment, direction in ESCAPE_ROWS:
        fired = totals.get(key, 0)
        share = instances_firing.get(key, 0) / n if n else float('nan')
        if key in NO_DETECTOR_ESCAPES:
            undetected.append(construct)
            continue
        if not fired and 'false \\verdict{Adeq.}' not in direction:
            silent.append(construct)
            continue
        mark = '\\added' if key in POST_HOC_ESCAPES else ''
        lines.append(f"{construct}{mark} & {treatment} & {direction} & {(format(fired, ',') if fired else ND)} & {(_pct(share) if n else ND)} \\\\")
    if silent:
        names = ', '.join(silent)
        lines.append(f'\\emph{{{len(silent)} declared, never observed}} & {names} & {ND} & {ND} & 0 \\\\')
    if undetected:
        names = ', '.join(undetected)
        lines.append(f'\\emph{{{len(undetected)} declared, no detector}} & {names} & {ND} & {ND} & {ND} \\\\')
    lines += ['\\bottomrule', '\\end{tabularx}']
    return '\n'.join(lines)
INERT_BAND = 0.05
_LABEL_FIELDS: tuple[tuple[str, str], ...] = (('cg', 'cg'), ('depth_cap', 'k'), ('frame', 'frame'), ('entry_points', 'ep'), ('heap_model', 'heap'), ('cha_augmented', 'cha'), ('field_sens', 'field-sens'), ('realizable', 'paths-realizable'), ('summary_edges', 'summary-edges'))

def _approx_label(a: Approximation, base: Approximation) -> str:
    a_d, b_d = (a.model_dump(mode='json'), base.model_dump(mode='json'))
    parts = [f'{short}={a_d[field]}' for field, short in _LABEL_FIELDS if a_d[field] != b_d[field]]
    if not parts:
        return f'$\\apx$: (identical to base, digest {a.digest()[:8]})'
    parts = ['paths=unrestricted' if p == 'paths-realizable=False' else 'no summary edges' if p == 'summary-edges=False' else p for p in parts]
    return '$\\apx$: ' + ', '.join(parts)

def read_crosscheck(results_dir: Path) -> list[dict[str, Any]]:
    p = Path(results_dir) / 'crosscheck.jsonl'
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]

def crosscheck_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if r.get('status') == 'ok']
    missed = sum((int(r.get('missed_count', 0)) for r in ok))
    return {'attempted': len(rows), 'compared': len(ok), 'not_compared': len(rows) - len(ok), 'instances_with_a_miss': sum((1 for r in ok if r.get('missed_count', 0))), 'missed_elements_total': missed, 'sdg_elements_total': sum((int(r.get('sdg_elements', 0)) for r in ok)), 'statement_hop_cap': max((int(r.get('depth_cap', 0)) for r in ok), default=0), 'claim': 'no counterexample within the stated statement-hop cap, on the instances compared' if ok and (not missed) else "FAST missed elements the SDG reached; Sec. 5.1's default must change" if missed else 'no instance was successfully compared'}
LONG_CONTEXT_BUDGETS: tuple[int, ...] = (1000000, 2000000)

def _fit_class(r: InstanceRecord, status: Status) -> str:
    if r.crit_size == 0:
        return 'degenerate'
    if status is not Status.OK:
        return 'partial_classpath'
    return 'genuine'
ENTRY_POINT_REASON = 'entry-points'

def _cell_fits(cell_rows: Sequence[InstanceRecord], budgets: Sequence[int]) -> dict[str, Any]:
    p1 = [r for r in cell_rows if r.property.value == 'P1' and r.property_applicable]
    full = [float(r.full_closure_tokens) for r in p1]
    return {'n': len(p1), 'full_P1_median': describe(full)['median'] if full else None, 'full_P1_within': {str(b): sum((1 for v in full if v <= b)) for b in sorted({*budgets, *LONG_CONTEXT_BUDGETS})}, 'entry_point_escape_instances': len({r.instance_id for r in cell_rows if ENTRY_POINT_REASON in r.unknown_reasons})}

def _revision_measures(rows: Sequence[InstanceRecord], funnel: Funnel, budgets: Sequence[int]) -> dict[str, Any]:
    by_status = {e.instance_id: e.status for e in funnel.entries}
    p1 = [r for r in rows if r.property.value == 'P1']
    out: dict[str, Any] = {}
    screen: dict[str, Any] = {}
    all_budgets = sorted({*budgets, *LONG_CONTEXT_BUDGETS})
    for prop in ('P1', 'P2', 'P3'):
        pr = [r for r in rows if r.property.value == prop and r.property_applicable]
        if not pr:
            continue
        per_budget = {}
        for b in all_budgets:
            f = [r for r in pr if r.full_closure_tokens <= b]
            per_budget[str(b)] = {k: sum((1 for r in f if _fit_class(r, by_status.get(r.instance_id, Status.OK)) == k)) for k in ('degenerate', 'partial_classpath', 'genuine')} | {'total': len(f)}
        screen[prop] = {'n': len(pr), 'by_budget': per_budget, 'genuine_registered': per_budget[str(max(budgets))]['genuine'] if budgets else 0}
    out['fit_screen'] = screen
    headline = int(sorted(budgets)[len(budgets) // 2]) if budgets else 32000
    repo_el: dict[str, list[float]] = defaultdict(list)
    for r in p1:
        repo_el[r.repo].append(float(r.full_closure_elements))
    fits: list[dict[str, Any]] = []
    for r in sorted((r for r in p1 if r.full_closure_tokens <= headline), key=lambda r: r.instance_id):
        st = by_status.get(r.instance_id, Status.OK)
        others = [x for x in repo_el[r.repo]]
        fits.append({'instance_id': r.instance_id, 'repo': r.repo, 'crit': r.crit_size, 'status': st.value, 'class': _fit_class(r, st), 'full_closure_elements': r.full_closure_elements, 'full_closure_tokens': r.full_closure_tokens, 'repo_median_elements': describe(others)['median'] if others else None, 'repo_n': len(others), 'shadowing_dropped': bool(r.shadowing_jars_dropped)})
    out['p1_fits'] = fits
    if fits:
        out['p1_fits_max_elements'] = max((int(f['full_closure_elements']) for f in fits))
        nocall = [int(f['full_closure_elements']) for f in fits if f['class'] == 'degenerate']
        if nocall:
            out['p1_fits_nocallers_max_elements'] = max(nocall)
    strata: dict[str, list[InstanceRecord]] = defaultdict(list)
    for r in p1:
        sv = by_status.get(r.instance_id, Status.OK).value
        strata[f"{sv}|{('shadowed' if r.shadowing_jars_dropped else 'clean')}"].append(r)
    out['strata'] = {k: {'n': len(v), 'full_P1_median': describe([float(r.full_closure_tokens) for r in v])['median']} for k, v in sorted(strata.items())}
    none = [r for r in p1 if r.crit_size == 0]
    out['no_callers'] = {'n': len(none), 'full_P1_median': describe([float(r.full_closure_tokens) for r in none])['median'] if none else None}
    p2_ids = {r.instance_id for r in rows if r.property.value == 'P2' and r.property_applicable}
    if p2_ids:
        p1_sub = [float(r.full_closure_tokens) for r in p1 if r.instance_id in p2_ids]
        p2_sub = [float(r.full_closure_tokens) for r in rows if r.property.value == 'P2' and r.instance_id in p2_ids]
        out['p2_matched'] = {'n': len(p2_ids), 'P1_median': describe(p1_sub)['median'] if p1_sub else None, 'P2_median': describe(p2_sub)['median'] if p2_sub else None, 'P2_within_headline': sum((1 for v in p2_sub if v <= headline))}
    full = [float(r.full_closure_tokens) for r in p1]
    if full:
        out['long_context'] = {'post_hoc': True, **{f'within_{b}': sum((1 for v in full if v <= b)) for b in LONG_CONTEXT_BUDGETS}, 'n': len(full), 'majority_budget': describe(full)['median']}
        per_repo = _saturation(p1)['per_repo']
        big = max(per_repo, key=lambda k: per_repo[k]['n'])
        inside = [float(r.full_closure_tokens) for r in p1 if r.repo == big]
        outside = [float(r.full_closure_tokens) for r in p1 if r.repo != big]
        out['long_context']['by_repository'] = {'big_repo': big, 'big': {'n': len(inside), **{f'within_{b}': sum((1 for v in inside if v <= b)) for b in LONG_CONTEXT_BUDGETS}, 'tokens_median': describe(inside)['median']}, 'outside': {'n': len(outside), **{f'within_{b}': sum((1 for v in outside if v <= b)) for b in LONG_CONTEXT_BUDGETS}, 'tokens_max': int(max(outside)) if outside else None}}
    out['base_cell_fits'] = _cell_fits(rows, budgets)
    wd: dict[str, int] = defaultdict(int)
    nonvacuous = 0
    for r in p1:
        if r.witness_distance is None:
            wd['none'] += 1
            continue
        wd[str(r.witness_distance)] += 1
        if r.witness_distance >= 2 and r.witness_interior:
            nonvacuous += 1
    out['witness_distance'] = dict(sorted(wd.items())) | {'interior_nonvacuous': nonvacuous}
    vbp: dict[str, dict[str, int]] = {}
    for prop in ('P1', 'P2', 'P3'):
        counts: dict[str, int] = defaultdict(int)
        for r in rows:
            if r.property.value == prop and r.property_applicable and (r.verdict is not None):
                counts[r.verdict.value] += 1
        if counts:
            vbp[prop] = dict(sorted(counts.items()))
    out['verdicts_by_property'] = vbp
    return out

def summary(records: Sequence[InstanceRecord], funnel: Funnel, budgets: Sequence[int]=BUDGETS, base_digest: str | None=None, crosscheck: Sequence[dict[str, Any]]=(), conformance: Sequence[dict[str, Any]]=()) -> dict[str, Any]:
    rows = _base_cell(_ok(records), base_digest)
    _require_full_closure(rows)
    p1 = [r for r in rows if r.property.value == 'P1']
    repos = [r.repo for r in p1]

    def boot(field: str):
        vals = [float(getattr(r, field)) for r in p1]
        if not vals:
            return None
        iv = cluster_bootstrap(vals, repos)
        return {'point': iv.point, 'low': iv.low, 'high': iv.high, 'method': iv.method}
    verdicts: dict[str, int] = defaultdict(int)
    for r in rows:
        if r.verdict is not None:
            verdicts[r.verdict.value] += 1
    gate: dict[str, Any] = {}
    for key, _ in LEVEL_ROWS:
        vals = [float(r.level_tokens[key]) for r in p1 if key in r.level_tokens]
        gate[key] = {**describe(vals), **{f'within_{b}': fraction_within(vals, b) for b in budgets}}
    full = [float(r.full_closure_tokens) for r in p1]
    _full_ci = cluster_bootstrap(full, repos) if full else None
    gate['full_P1'] = {**describe(full), **{f'within_{b}': fraction_within(full, b) for b in budgets}, **({'median_ci': {'point': _full_ci.point, 'low': _full_ci.low, 'high': _full_ci.high, 'method': _full_ci.method}} if _full_ci else {})}
    frame_vals = [float(r.frame_tokens) for r in p1 if r.frame_tokens]
    if frame_vals:
        gate['frame'] = {**describe(frame_vals), **{f'within_{b}': fraction_within(frame_vals, b) for b in budgets}}
    up = [float(r.upstream_elements) for r in p1 if r.upstream_elements]
    down = [float(r.downstream_elements) for r in p1 if r.downstream_elements]
    if up and down:
        gate['directed'] = {'upstream_elements_median': describe(up)['median'], 'downstream_elements_median': describe(down)['median'], 'upstream_tokens_median': describe([float(r.upstream_tokens) for r in p1])['median'], 'downstream_tokens_median': describe([float(r.downstream_tokens) for r in p1])['median'], 'superseded_by': 'directions', 'warning': 'UPSTREAM_KINDS and DOWNSTREAM_KINDS overlap in `override` and `heap-write`, and downstream was scored over eight edge kinds against three: not a partition, and not a fair comparison (D89)'}
    have_dirs = [r for r in p1 if r.direction_elements]
    if have_dirs:
        full_el = [float(r.full_closure_elements) for r in have_dirs]
        full_tok = [float(r.full_closure_tokens) for r in have_dirs]
        full_med = describe(full_el)['median']
        directions: dict[str, Any] = {}
        for name in sorted({k for r in have_dirs for k in r.direction_elements}):
            restr = [float(r.direction_elements.get(name, 0)) for r in have_dirs]
            abl = [float(r.ablation_elements.get(name, 0)) for r in have_dirs]
            abl_tok = [float(r.ablation_tokens.get(name, 0)) for r in have_dirs]
            abl_med = describe(abl)['median'] if abl else None
            savings = [1.0 - a / f for a, f in zip(abl, full_el, strict=True) if f]
            savings_tok = [1.0 - a / f for a, f in zip(abl_tok, full_tok, strict=True) if f]
            directions[name] = {'kinds': sorted((k.value for k in DIRECTIONS[name])), 'restriction_median': describe(restr)['median'] if restr else None, 'restriction_tokens_median': describe([float(r.direction_tokens.get(name, 0)) for r in have_dirs])['median'], 'ablation_median': abl_med, 'ablation_tokens_median': describe(abl_tok)['median'] if abl_tok else None, 'ablation_saving': describe(savings)['median'] if savings else None, 'ablation_saving_tokens': describe(savings_tok)['median'] if savings_tok else None, 'ablation_saving_tokens_ratio_of_medians': 1.0 - describe(abl_tok)['median'] / describe(full_tok)['median'] if abl_tok and describe(full_tok)['median'] else None, 'n': len(have_dirs)}
        scored = {k: v['ablation_saving_tokens'] for k, v in directions.items() if v['ablation_saving_tokens'] is not None}
        scored_el = {k: v['ablation_saving'] for k, v in directions.items() if v['ablation_saving'] is not None}
        gate['directions'] = {**directions, 'full_closure_elements_median': full_med, 'full_closure_tokens_median': describe(full_tok)['median'], 'dominant': max(scored, key=lambda k: scored[k]) if scored else None, 'dominant_saving_tokens': max(scored.values()) if scored else None, 'dominant_elements': max(scored_el, key=lambda k: scored_el[k]) if scored_el else None, 'units_agree': bool(scored) and bool(scored_el) and (max(scored, key=lambda k: scored[k]) == max(scored_el, key=lambda k: scored_el[k])), 'definition': "restriction = Cl(seed) over that bucket alone (a lower bound on its contribution); ablation = Cl(seed) over the property's relation minus that bucket (what declining the direction actually costs); saving = median per-instance 1 - |ablation| / |full closure|, in elements and in tokens. `dominant` is scored on TOKENS, the unit the budget is denominated in; the two units do not agree on these repositories."}
    stages = {}
    for key in ('seed', 'closure', 'tokens'):
        vals = [r.seconds.get(key, 0.0) for r in p1 if key in r.seconds]
        if vals:
            stages[key] = describe(vals)
    if stages:
        gate['stage_seconds'] = stages
    escape_counts: dict[str, Any] = {}
    table_escapes(records, funnel, base_digest, counts_out=escape_counts)
    if p1:
        gate['saturation'] = _saturation(p1)
    crit = [float(r.crit_size) for r in p1]
    if crit:
        gate['crit_size_median'] = describe(crit)['median']
        gate['crit_size_zero'] = sum((1 for c in crit if c == 0))
        gate['crit_size_n'] = len(crit)
    by_status = {e.instance_id: e.status for e in funnel.entries}
    strata: dict[str, list[InstanceRecord]] = defaultdict(list)
    for r in p1:
        strata[by_status.get(r.instance_id, Status.OK).value].append(r)
    if len(strata) > 1:
        gate['by_analysis_status'] = {name: {'n': len(grp), 'A0_median': describe([float(r.level_tokens.get('A0', 0)) for r in grp])['median'], 'full_P1_median': describe([float(r.full_closure_tokens) for r in grp])['median'], 'full_P1_within_headline': fraction_within([float(r.full_closure_tokens) for r in grp], int(sorted(budgets)[len(budgets) // 2]) if budgets else 32000)} for name, grp in sorted(strata.items())}
    degenerate = [r for r in p1 if r.crit_size == 0 and r.observation_points == 0]
    headline_budget = int(sorted(budgets)[len(budgets) // 2]) if budgets else 32000
    fits = [r for r in p1 if r.full_closure_tokens <= headline_budget]

    def _fit_class(r: InstanceRecord) -> str:
        if r.crit_size == 0:
            return 'degenerate'
        if by_status.get(r.instance_id, Status.OK) is not Status.OK:
            return 'partial_classpath'
        return 'genuine'
    classified = {k: sorted((r.instance_id for r in fits if _fit_class(r) == k)) for k in ('degenerate', 'partial_classpath', 'genuine')}
    by_budget = {}
    for budget in sorted(budgets):
        f = [r for r in p1 if r.full_closure_tokens <= budget]
        by_budget[str(budget)] = {'total': len(f), 'degenerate': sum((1 for r in f if _fit_class(r) == 'degenerate')), 'partial_classpath': sum((1 for r in f if _fit_class(r) == 'partial_classpath')), 'genuine': sum((1 for r in f if _fit_class(r) == 'genuine'))}
    gate['degenerate'] = {'definition': 'crit(P) empty and no observation points: the target has no callers', 'n': len(degenerate), 'instances': sorted((r.instance_id for r in degenerate)), 'headline_budget': headline_budget, 'within_headline_total': len(fits), 'within_headline_degenerate': len(classified['degenerate']), 'within_headline_partial_classpath': len(classified['partial_classpath']), 'within_headline_partial_instances': classified['partial_classpath'], 'within_headline_partial_crit': sorted((r.crit_size for r in fits if _fit_class(r) == 'partial_classpath')), 'within_headline_genuine': classified['genuine'], 'by_budget': by_budget, 'genuine_any_budget': by_budget[str(max(sorted(budgets)))]['genuine'] if budgets else 0, 'genuine_definition': 'crit(P) non-empty AND the analysis resolved a complete classpath: a fit on an incomplete build measures the build'}
    by_approx: dict[str, list[InstanceRecord]] = defaultdict(list)
    for r in _ok(records):
        by_approx[r.approximation.digest()].append(r)
    sensitivity: dict[str, Any] = {}
    if by_approx:
        if base_digest is None:
            base_digest = _ok(records)[0].approximation.digest()
        base_by_key = {(r.instance_id, r.property.value): r for r in by_approx[base_digest]}
        summary_base_approx = by_approx[base_digest][0].approximation
        for digest, cell_rows in sorted(by_approx.items()):
            if digest == base_digest:
                continue
            depth_flips = verdict_flips = compared = 0
            verdict_compared = verdict_absent = 0
            per_prop: dict[str, list[float]] = defaultdict(list)
            token_ratios: list[float] = []
            for r in cell_rows:
                b = base_by_key.get((r.instance_id, r.property.value))
                if b is None:
                    continue
                compared += 1
                if b.depth != r.depth:
                    depth_flips += 1
                if b.verdict is None or r.verdict is None:
                    if b.verdict is not r.verdict:
                        verdict_absent += 1
                else:
                    verdict_compared += 1
                    if b.verdict != r.verdict:
                        verdict_flips += 1
                if b.closure_elements and r.property_applicable and b.property_applicable:
                    per_prop[r.property.value].append(r.closure_elements / b.closure_elements)
                if r.property.value == 'P1' and b.full_closure_tokens and r.property_applicable and b.property_applicable:
                    token_ratios.append(r.full_closure_tokens / b.full_closure_tokens)
            a = cell_rows[0].approximation
            a_d = a.model_dump(mode='json')
            b_d = summary_base_approx.model_dump(mode='json')
            changed = sorted((f for f, _ in _LABEL_FIELDS if a_d[f] != b_d[f]))
            sensitivity[digest] = {'changed': changed, 'inert_by_construction': changed == ['depth_cap'], 'cg': a.cg.value, 'depth_cap': a.depth_cap, 'frame': a.frame.value, 'entry_points': a.entry_points.value, 'heap_model': a.heap_model.value, 'cha_augmented': a.cha_augmented, 'field_sens': a.field_sens, 'realizable': a.realizable, 'summary_edges': a.summary_edges, 'label': _approx_label(a, summary_base_approx), 'compared': compared, 'n_instances': len({r.instance_id for r in cell_rows}), 'verdict_compared': verdict_compared, 'verdict_flips': verdict_flips, 'verdict_flip_rate': verdict_flips / verdict_compared if verdict_compared else None, 'verdict_absent_one_side': verdict_absent, 'depth_flip_rate': depth_flips / compared if compared else None, 'closure_tokens_ratio_median': describe(token_ratios)['median'] if token_ratios else None, 'cell_fits': _cell_fits(cell_rows, budgets), 'closure_ratio_median_by_property': {prop: {'n': len(vals), 'median': sorted(vals)[len(vals) // 2], 'min': min(vals)} for prop, vals in sorted(per_prop.items())}}
    verdict_values = {r.verdict.value for r in rows if r.verdict}
    ladder_values = {r.verdict.value for r in _ok(records) if r.verdict}
    properties: dict[str, object] = {}
    for prop in sorted({r.property.value for r in rows}):
        applicable, decided, total = _applicability(rows, prop)
        usable = [r for r in rows if r.property.value == prop and r.property_applicable]
        vals = [float(r.full_closure_tokens) for r in usable]
        kinds: dict[str, int] = defaultdict(int)
        for r in rows:
            if r.property.value != prop:
                continue
            for k, n in r.invariant_kinds.items():
                kinds[k] += n
        properties[prop] = {'attempted': total, 'applicability_decided': decided, 'applicable': applicable, 'applicability_rate': applicable / decided if decided else None, 'reportable': len(vals) >= MIN_APPLICABLE, 'min_applicable_required': MIN_APPLICABLE, 'invariant_kinds': dict(sorted(kinds.items())), 'closure_tokens': {**describe(vals), **{f'within_{b}': fraction_within(vals, b) for b in budgets}} if vals else None}
    out_crosscheck = crosscheck_summary(crosscheck) if crosscheck else None
    out_conformance = None
    return {'revision': _revision_measures(rows, funnel, budgets), 'crosscheck': out_crosscheck, 'instances_analysed': len(funnel.analysable()), 'instances_total': len(funnel.entries), 'records': len(records), 'verdicts': dict(sorted(verdicts.items())), 'n_clusters': len(set(repos)), 'closure_tokens_median_ci': boot('closure_tokens'), 'closure_elements_median_ci': boot('closure_elements'), 'gate': gate, 'unrenderable': {'denominator': 'P1 records, one per instance', 'instances': len(p1), 'closure_elements': sum((r.closure_elements for r in p1)), 'unrenderable_elements': sum((r.unrenderable_in_closure for r in p1)), 'rate': sum((r.unrenderable_in_closure for r in p1)) / sum((r.closure_elements for r in p1)) if sum((r.closure_elements for r in p1)) else None, 'unreached_in_closure': sum((r.unreached_in_closure for r in p1))}, 'properties': properties, 'properties_never_applicable': sorted((k for k, v in properties.items() if not v['applicable'])), 'pure_addition_rate': sum((1 for r in p1 if r.pure_addition)) / len(p1) if p1 else float('nan'), 'escapes': escape_counts, 'unknown_reasons': dict(sorted({reason: len({r.instance_id for r in rows if reason in r.unknown_reasons}) for r in rows for reason in r.unknown_reasons}.items())), 'sensitivity': sensitivity, 'verdict_saturated': not verdict_values & FAVOURABLE_VERDICTS, 'verdict_constant': len(verdict_values) <= 1, 'verdict_favourable_any_cell': sorted(ladder_values & FAVOURABLE_VERDICTS), 'verdict_values_base_cell': sorted(verdict_values), 'verdict_values_all_cells': sorted(ladder_values), 'context_kind': sorted({r.context_kind for r in _ok(records)}), 'context_elements_median': describe([float(r.context_elements) for r in _ok(records)])['median'], 'funnel': json.loads(funnel.to_json())}

def builder_summary(cells: Sequence[dict[str, Any]], crit_by_instance: dict[str, int] | None=None) -> dict[str, Any]:
    crit = crit_by_instance or {}
    out: dict[str, Any] = {}
    for c in cells:
        rec = c.get('seed_recall') or {}
        whole = [iid for iid, r in rec.items() if r >= 1.0]
        out.setdefault(c['builder'], {})[str(c['budget'])] = {**{k: c.get(k) for k in ('instances', 'assessable', 'median_depth', 'median_seed_recall', 'mean_seed_recall', 'median_realised_tokens', 'over_budget', 'omit_empty')}, 'assessable_no_callers': sum((1 for iid in whole if crit.get(iid) == 0))}
    return out

def graph_summary(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    from lacuna.analysis.graph_stats import read_rows, summarise
    rows = read_rows(path)
    if not rows:
        return None
    out = summarise(rows)
    for b in LONG_CONTEXT_BUDGETS:
        fits = [r for r in rows if r.p1_full_closure_tokens is not None and r.p1_full_closure_tokens <= b]
        out[f'closure_over_nodes_within_{b}'] = summarise(fits)['closure_over_nodes'] if fits else None
    return out

def sanity_summary(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    from lacuna.analysis.sanity import read_rows, summarise
    rows = read_rows(path)
    return summarise(rows, BUDGETS) if rows else None
