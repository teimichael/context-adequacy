from __future__ import annotations
import shutil
from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from lacuna.corpora.base import Status
from lacuna.experiment.evaluate import InstanceRecord
from lacuna.experiment.funnel import Funnel
TEXT_WIDTH_IN = 395.8225 / 72.27
INK = '#000000'
ACCENT = '#0072B2'
CONTRAST = '#D55E00'
THIRD = '#009E73'
REFERENCE = '#999999'
BUDGET_LABEL = {8000: '8K', 32000: '32K', 128000: '128K', 1000000: '1M'}

def _latex_available() -> bool:
    return False

@contextmanager
def paper_style() -> Iterator[bool]:
    usetex = _latex_available()
    rc: dict[str, Any] = {'font.size': 8, 'axes.labelsize': 8, 'axes.titlesize': 8, 'xtick.labelsize': 7, 'ytick.labelsize': 7, 'legend.fontsize': 7, 'axes.spines.top': False, 'axes.spines.right': False, 'axes.linewidth': 0.6, 'xtick.major.width': 0.6, 'ytick.major.width': 0.6, 'lines.linewidth': 1.0, 'pdf.fonttype': 42, 'ps.fonttype': 42, 'savefig.dpi': 300}
    if usetex:
        rc |= {'text.usetex': True, 'font.family': 'serif', 'text.latex.preamble': '\\usepackage[T1]{fontenc}\\usepackage{libertine}\\usepackage[libertine]{newtxmath}'}
    else:
        rc |= {'text.usetex': False, 'font.family': 'serif', 'font.serif': ['DejaVu Serif'], 'mathtext.fontset': 'dejavuserif'}
    with matplotlib.rc_context(rc):
        yield usetex

def _tex(s: str, usetex: bool) -> str:
    if not usetex:
        return s
    return s.replace('%', '\\%').replace('&', '\\&').replace('#', '\\#').replace('_', '\\_')

def _base_rows(records: Sequence[InstanceRecord], base_digest: str | None) -> list[InstanceRecord]:
    from lacuna.analysis.report import _base_cell, _ok
    return _base_cell(_ok(records), base_digest)

def _ecdf(vals: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    v = np.sort(np.asarray(vals, dtype=float))
    y = np.arange(1, len(v) + 1) / len(v)
    return (v, y)

def _budget_lines(ax: Any, budgets: Sequence[int], usetex: bool, labels: bool) -> None:
    for b in budgets:
        long_context = b not in (8000, 32000, 128000)
        ax.axvline(b, color=REFERENCE, lw=0.6, ls=(0, (1, 2)) if long_context else (0, (4, 2)), zorder=0)
        if labels:
            lab = BUDGET_LABEL.get(b, f'{b // 1000}K')
            ax.text(b, 1.02, lab, transform=ax.get_xaxis_transform(), ha='center', va='bottom', fontsize=7, color='#555555')

def figure_gate_saturation(records: Sequence[InstanceRecord], funnel: Funnel, path: Path, base_digest: str | None=None, budgets: Sequence[int]=(8000, 32000, 128000), saturation_fraction: float=0.9) -> Path:
    rows = _base_rows(records, base_digest)
    p1 = [r for r in rows if r.property.value == 'P1']
    p3 = [r for r in rows if r.property.value == 'P3' and r.property_applicable]
    status = {e.instance_id: e.status for e in funnel.entries}
    shown_budgets = [*budgets, 1000000]
    with paper_style() as usetex:
        fig, (ax, bx) = plt.subplots(2, 1, figsize=(TEXT_WIDTH_IN, 3.95), sharex=True, gridspec_kw={'height_ratios': [1.0, 1.3], 'hspace': 0.12})
        xmin, xmax = (100.0, 3000000.0)
        series = [('edit frame', [float(r.frame_tokens) for r in p1 if r.frame_tokens], {'color': THIRD, 'ls': (0, (1, 1.3)), 'lw': 1.2}), ('seed', [float(r.level_tokens['A0']) for r in p1 if 'A0' in r.level_tokens], {'color': ACCENT, 'ls': '-.', 'lw': 1.0}), ('full closure, $P_3$' if usetex else 'full closure, P3', [float(r.full_closure_tokens) for r in p3], {'color': CONTRAST, 'ls': '--', 'lw': 1.0}), ('full closure, $P_1$' if usetex else 'full closure, P1', [float(r.full_closure_tokens) for r in p1], {'color': INK, 'ls': '-', 'lw': 1.6})]
        for label, vals, style in series:
            if not vals:
                continue
            x, y = _ecdf(vals)
            ax.step(np.r_[xmin, x], np.r_[0.0, y], where='post', label=label, **style)
        _budget_lines(ax, shown_budgets, usetex, labels=True)
        ax.plot([32000], [0.5], marker='D', ms=4.5, mfc='white', mec=INK, mew=0.9, ls='none', zorder=5)
        ax.annotate('threshold:\nhalf within 32K', xy=(32000, 0.5), xytext=(40000, 0.8), fontsize=7, va='center', arrowprops={'arrowstyle': '-', 'lw': 0.5, 'color': '#555555', 'shrinkB': 3})
        ax.set_ylim(0, 1.0)
        ax.set_ylabel('fraction of instances\nwithin $x$ tokens' if usetex else 'fraction of instances\nwithin x tokens')
        ax.legend(loc='upper left', frameon=False, handlelength=2.6, borderaxespad=0.2)
        ax.text(0.0, 1.02, '(a)', transform=ax.transAxes, fontsize=8, va='bottom')
        by_repo: dict[str, list[InstanceRecord]] = defaultdict(list)
        for r in p1:
            by_repo[r.repo].append(r)
        order = sorted(by_repo, key=lambda k: float(np.median([x.full_closure_tokens for x in by_repo[k]])))
        yt, ylabels, notes = ([], [], [])
        for i, repo in enumerate(order):
            rs = sorted(by_repo[repo], key=lambda r: r.full_closure_tokens)
            top = max((r.full_closure_elements for r in rs))
            sat = [r.full_closure_elements >= saturation_fraction * top for r in rs]
            offsets = np.linspace(-0.28, 0.28, len(rs)) if len(rs) > 1 else [0.0]
            for r, s, dy in zip(rs, sat, offsets, strict=True):
                ok = status.get(r.instance_id, Status.OK) is Status.OK
                ax_marker = 'o' if ok else '^'
                bx.plot(r.full_closure_tokens, i + dy, marker=ax_marker, ms=3.4, mec=INK, mew=0.7, mfc=INK if s else 'white', ls='none', zorder=3)
            yt.append(i)
            short = repo.split('/', 1)[-1]
            ylabels.append(_tex(f'{short} ({len(rs)})', usetex))
            k = sum(sat) - 1
            notes.append(f'{k} of {len(rs) - 1}' if len(rs) > 1 else '--')
        _budget_lines(bx, shown_budgets, usetex, labels=False)
        bx.set_yticks(yt)
        bx.set_yticklabels(ylabels)
        bx.set_ylim(-0.7, len(order) - 0.3)
        for i, note in enumerate(notes):
            bx.text(1.005, i, note, transform=bx.get_yaxis_transform(), va='center', fontsize=7, color='#333333')
        bx.text(1.005, len(order) - 0.2, 'saturated', transform=bx.get_yaxis_transform(), va='bottom', fontsize=7, color='#333333')
        bx.set_xscale('log')
        bx.set_xlim(xmin, xmax)
        bx.set_xlabel('closure size (tokens, log scale)')
        bx.text(0.0, 1.01, '(b)', transform=bx.transAxes, fontsize=8, va='bottom')
        proxies = [bx.plot([], [], marker='o', ms=3.4, mec=INK, mfc=INK, ls='none', label='classpath resolved')[0], bx.plot([], [], marker='^', ms=3.4, mec=INK, mfc=INK, ls='none', label='partial classpath')[0], bx.plot([], [], marker='o', ms=3.4, mec=INK, mfc='white', ls='none', label=f'below {int(100 * saturation_fraction)}' + ('\\%' if usetex else '%') + ' of repository max')[0]]
        bx.legend(handles=proxies, loc='lower left', frameon=False, fontsize=7, handletextpad=0.3, borderaxespad=0.2)
        fig.subplots_adjust(left=0.245, right=0.9, top=0.95, bottom=0.1)
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, bbox_inches='tight', pad_inches=0.05, metadata={'Creator': 'Anonymous replication package', 'Producer': 'Matplotlib', 'Author': None, 'CreationDate': None, 'ModDate': None})
        plt.close(fig)
    return path
_SETTING_LABEL: dict[tuple[str, str], str] = {('cg', '0-CFA'): 'call graph: 0-CFA', ('cha_augmented', 'False'): 'call graph without CHA edges', ('field_sens', 'False'): 'field-insensitive heap', ('heap_model', 'none'): 'no heap edges', ('realizable', 'False'): 'unrestricted paths', ('summary_edges', 'False'): 'no summary edges', ('frame', 'F2-bounded-wide'): 'wider edit frame (F2)', ('entry_points', 'declared-mains'): 'entry points: main methods', ('entry_points', '+test-methods'): 'entry points: + test methods'}
_DIRECTION_LABEL = {'ascent': 'decline ascent (callers)', 'descent': 'decline descent (callees)', 'reference': 'decline type references', 'dispatch': 'decline override edges'}

def _quartiles(vals: Sequence[float]) -> tuple[float, float, float]:
    a = np.asarray(vals, dtype=float)
    q1, med, q3 = np.quantile(a, [0.25, 0.5, 0.75])
    return (float(q1), float(med), float(q3))
HEADLINE_BUDGET = 32000

def _within(vals: Sequence[float], budget: int) -> int:
    return sum((1 for v in vals if v <= budget))

def lever_rows(records: Sequence[InstanceRecord], base_digest: str | None, levers: Sequence[dict[str, Any]] | None, budget: int=HEADLINE_BUDGET) -> list[dict[str, Any]]:
    from lacuna.analysis.report import _base_cell, _ok
    ok = _ok(records)
    base_rows = _base_cell(ok, base_digest)
    if not base_rows:
        return []
    base_digest = base_rows[0].approximation.digest()
    base_approx = base_rows[0].approximation
    base = {(r.instance_id, r.property.value): r for r in base_rows}
    by_approx: dict[str, list[InstanceRecord]] = defaultdict(list)
    for r in ok:
        by_approx[r.approximation.digest()].append(r)
    b_d = base_approx.model_dump(mode='json')
    n_base = len({r.instance_id for r in base_rows})
    out: list[dict[str, Any]] = []
    for digest, rows in sorted(by_approx.items()):
        if digest == base_digest:
            continue
        a_d = rows[0].approximation.model_dump(mode='json')
        changed = [(f, str(a_d[f])) for f in a_d if f in {'cg', 'cha_augmented', 'field_sens', 'heap_model', 'realizable', 'summary_edges', 'frame', 'entry_points', 'depth_cap'} and a_d[f] != b_d[f]]
        if [f for f, _ in changed] == ['depth_cap']:
            continue
        label = '; '.join((_SETTING_LABEL.get(c, f'{c[0]}={c[1]}') for c in changed))
        ratios, flips, compared = ([], 0, 0)
        for r in rows:
            b = base.get((r.instance_id, r.property.value))
            if b is None:
                continue
            if b.verdict is not None and r.verdict is not None:
                compared += 1
                flips += b.verdict.value != r.verdict.value
            if r.property.value == 'P1' and b.full_closure_tokens and r.property_applicable and b.property_applicable:
                ratios.append(r.full_closure_tokens / b.full_closure_tokens)
        if not ratios:
            continue
        q1, med, q3 = _quartiles(ratios)
        n_cell = len({r.instance_id for r in rows})
        cell_p1 = [float(r.full_closure_tokens) for r in rows if r.property.value == 'P1' and r.property_applicable]
        out.append({'group': 'setting', 'label': label, 'median': med, 'q1': q1, 'q3': q3, 'flip_rate': flips / compared if compared else None, 'n': n_cell, 'n_base': n_base, 'within_32k': _within(cell_p1, budget), 'n_within_denominator': len(cell_p1)})
    if levers:

        def ratio_rows(num: str, den: str, key_num: str='inf') -> list[float]:
            vals = []
            for rec in levers:
                h = rec['horizons']
                d = h['inf'][den]
                if d:
                    vals.append(h[key_num][num] / d)
            return vals

        def tokens(num: str, key_num: str) -> list[float]:
            return [float(rec['horizons'][key_num][num]) for rec in levers]
        for label, vals, absolute in (('caller horizon $j=1$ (direct callers)', ratio_rows('closure_tokens', 'closure_tokens', '1'), tokens('closure_tokens', '1')), ('caller horizon $j=2$', ratio_rows('closure_tokens', 'closure_tokens', '2'), tokens('closure_tokens', '2')), ('signature cost for declared elements', ratio_rows('closure_tokens_signature', 'closure_tokens'), tokens('closure_tokens_signature', 'inf'))):
            if vals:
                q1, med, q3 = _quartiles(vals)
                out.append({'group': 'lever', 'label': label, 'median': med, 'q1': q1, 'q3': q3, 'flip_rate': None, 'n': len(vals), 'n_base': n_base, 'within_32k': _within(absolute, budget), 'n_within_denominator': len(absolute)})
    p1 = [r for r in base_rows if r.property.value == 'P1' and r.direction_elements]
    for key, label in _DIRECTION_LABEL.items():
        tok = [r.ablation_tokens.get(key, 0) / r.full_closure_tokens for r in p1 if r.full_closure_tokens]
        el = [r.ablation_elements.get(key, 0) / r.full_closure_elements for r in p1 if r.full_closure_elements]
        ablated = [float(r.ablation_tokens[key]) for r in p1 if r.full_closure_tokens and key in r.ablation_tokens]
        if tok and el:
            q1, med, q3 = _quartiles(tok)
            e1, emed, e3 = _quartiles(el)
            out.append({'group': 'direction', 'label': label, 'median': med, 'q1': q1, 'q3': q3, 'median_elements': emed, 'q1_elements': e1, 'q3_elements': e3, 'flip_rate': None, 'n': len(tok), 'n_base': n_base, 'within_32k': _within(ablated, budget), 'n_within_denominator': len(ablated)})
    return out

def figure_levers(records: Sequence[InstanceRecord], path: Path, base_digest: str | None=None, levers: Sequence[dict[str, Any]] | None=None, gate_factor: float | None=None, budget: int=HEADLINE_BUDGET) -> Path:
    rows = lever_rows(records, base_digest, levers, budget)
    if not rows:
        with paper_style():
            fig, ax = plt.subplots(figsize=(TEXT_WIDTH_IN, 1.2))
            ax.text(0.5, 0.5, 'no data', ha='center', transform=ax.transAxes)
            ax.set_axis_off()
            path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(path, bbox_inches='tight', pad_inches=0.05, metadata={'Creator': 'Anonymous replication package', 'Producer': 'Matplotlib', 'Author': None, 'CreationDate': None, 'ModDate': None})
            plt.close(fig)
        return path
    groups = [('setting', 'approximation settings (ladder)'), ('lever', 'criterion levers'), ('direction', 'declining one traversal direction')]
    with paper_style() as usetex:
        ordered: list[dict[str, Any] | str] = []
        for g, title in groups:
            members = sorted((r for r in rows if r['group'] == g), key=lambda r: -r['median'])
            if members:
                ordered.append(title)
                ordered.extend(members)
        height = 0.5 + 0.142 * len(ordered)
        fig, ax = plt.subplots(figsize=(TEXT_WIDTH_IN, height))
        ax.axvspan(0.95, 1.05, color='#E6E6E6', lw=0, zorder=0)
        ax.axvline(1.0, color=REFERENCE, lw=0.6, zorder=0)
        if gate_factor:
            ax.axvline(1.0 / gate_factor, color=INK, lw=0.7, ls=(0, (4, 2)), zorder=0)
            ax.text(1.08 / gate_factor, -1.0, '32K / base median\n(scale reference)', va='top', ha='left', fontsize=6.5, color='#333333')
        yt, ylab = ([], [])
        y = 0.0
        for item in ordered:
            if isinstance(item, str):
                head = f'\\textbf{{{item}}}' if usetex else item
                ax.text(-0.66, y, head, transform=ax.get_yaxis_transform(), ha='left', va='center', fontsize=7, fontweight='bold', color='#333333')
                y -= 1.0
                continue
            yt.append(y)
            ylab.append(_tex(item['label'], usetex) if '$' not in item['label'] else item['label'] if usetex else item['label'].replace('$', ''))
            ax.plot([item['q1'], item['q3']], [y, y], color=ACCENT, lw=0.9, zorder=2)
            ax.plot(item['median'], y, marker='o', ms=3.6, color=ACCENT, ls='none', zorder=3)
            if 'median_elements' in item:
                dy = -0.28
                ax.plot([item['q1_elements'], item['q3_elements']], [y + dy, y + dy], color=CONTRAST, lw=0.9, zorder=2)
                ax.plot(item['median_elements'], y + dy, marker='s', ms=3.3, mfc='white', mec=CONTRAST, mew=0.8, ls='none', zorder=3)
            if item.get('within_32k') is not None and item.get('n_within_denominator'):
                ax.text(1.005, y, f"{item['within_32k']} of {item['n_within_denominator']}", transform=ax.get_yaxis_transform(), va='center', fontsize=6.5, color='#333333')
            y -= 1.0
        ax.set_yticks(yt)
        ax.set_yticklabels(ylab)
        ax.set_ylim(y + 0.4, 0.6)
        ax.set_xscale('log')
        ax.set_xlim(0.04, 1.4)
        ax.set_xticks([0.05, 0.1, 0.2, 0.5, 1.0])
        ax.set_xticklabels(['0.05', '0.1', '0.2', '0.5', '1'])
        ax.set_xlabel('closure relative to the base configuration (median, IQR; log scale)')
        ax.tick_params(axis='y', length=0)
        ax.spines['left'].set_visible(False)
        ax.text(1.005, 0.0, f"within {BUDGET_LABEL.get(budget, f'{budget // 1000}K')}", transform=ax.get_yaxis_transform(), va='center', fontsize=6.5, color='#333333')
        handles = [ax.plot([], [], marker='o', ms=3.6, color=ACCENT, ls='-', lw=0.9, label='tokens')[0], ax.plot([], [], marker='s', ms=3.3, mfc='white', mec=CONTRAST, color=CONTRAST, ls='-', lw=0.9, label='elements')[0]]
        lever_y = [yy for yy, item in zip(yt, [o for o in ordered if not isinstance(o, str)], strict=True) if item['group'] == 'lever']
        anchor_y = lever_y[len(lever_y) // 2] if lever_y else yt[-1]
        ax.legend(handles=handles, loc='center left', frameon=False, fontsize=7, handletextpad=0.4, borderaxespad=0.0, bbox_to_anchor=(0.2, anchor_y), bbox_transform=ax.get_yaxis_transform())
        fig.subplots_adjust(left=0.36, right=0.88, top=0.98, bottom=0.46 / height + 0.02)
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, bbox_inches='tight', pad_inches=0.05, metadata={'Creator': 'Anonymous replication package', 'Producer': 'Matplotlib', 'Author': None, 'CreationDate': None, 'ModDate': None})
        plt.close(fig)
    return path
