from __future__ import annotations
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

def _fmt(x: object) -> str:
    if isinstance(x, float):
        return f'{x:,.0f}' if abs(x) >= 100 else f'{x:.3g}'
    if isinstance(x, int):
        return f'{x:,}'
    return str(x)

def values_tex(summary: dict[str, Any], probe3: dict[str, Any] | None, levers: list[dict[str, Any]] | None, budget_cells: list[dict[str, Any]] | None=None) -> str:
    gate = summary.get('gate', {}) or {}
    full = gate.get('full_P1', {}) or {}
    unrend = summary.get('unrenderable', {}) or {}
    funnel = summary.get('funnel', {}) or {}
    cc = summary.get('crosscheck') or {}
    rows: list[tuple[str, object]] = [('resultNanalysed', funnel.get('analysable', 0)), ('resultNcandidates', funnel.get('total', 0)), ('resultNrepos', sum((1 for counts in (funnel.get('by_repo', {}) or {}).values() if any((k in ('OK', 'OK(generated)', 'PARTIAL_CLASSPATH') for k in counts))))), ('resultNreposAttempted', len(funnel.get('by_repo', {}) or {})), ('resultFullMedian', full.get('median', 0)), ('resultFullPninety', full.get('p90', 0)), ('resultFullWithinEightK', 100 * float(full.get('within_8000', 0) or 0)), ('resultFullWithinThirtyTwoK', 100 * float(full.get('within_32000', 0) or 0)), ('resultFullWithinOneTwentyEightK', 100 * float(full.get('within_128000', 0) or 0)), ('resultSeedMedian', (gate.get('A0', {}) or {}).get('median', 0)), ('resultUnrenderableRate', 100 * float(unrend.get('rate', 0) or 0)), ('resultUnrenderableCount', unrend.get('unrenderable_elements', 0)), ('resultClosureElements', (summary.get('closure_elements_median_ci', {}) or {}).get('point', 0)), ('resultClosureElementsTotal', unrend.get('closure_elements', 0)), ('resultContextElements', summary.get('context_elements_median', 0))]
    if cc:
        rows += [('resultCrosscheckN', cc.get('compared', 0)), ('resultCrosscheckMissed', cc.get('missed_elements_total', 0)), ('resultCrosscheckHops', cc.get('statement_hop_cap', 0))]
    if probe3 and probe3.get('reportable'):
        rows += [('resultProbeThreeN', probe3.get('n', 0)), ('resultProbeThreeRecall', 100 * float(probe3.get('recall_pooled', 0) or 0)), ('resultProbeThreeMissed', int(probe3.get('gold_elements_total', 0)) - int(probe3.get('contained_total', 0))), ('resultProbeThreeGold', probe3.get('gold_elements_total', 0)), ('resultProbeThreeMin', 100 * float(probe3.get('recall_min', 0) or 0)), ('resultProbeThreeMedianRecall', 100 * float(probe3.get('recall_median', 0) or 0)), ('resultProbeThreeClosure', probe3.get('closure_elements_median', 0))]
        if probe3.get('recall_min_attainable') is not None:
            rows += [('resultProbeThreeMinAttainable', 100 * float(probe3['recall_min_attainable'])), ('resultProbeThreeAttainableN', probe3.get('n_attainable', 0)), ('resultProbeThreeCeilingExcluded', probe3.get('ceiling_excluded', 0))]
    rb = (probe3 or {}).get('random_baseline') or {}
    if rb:
        pools = rb.get('pools') or {}
        ind = pools.get('indexed') or {}
        edg = pools.get('edg') or {}
        rows += [('resultProbeThreeRandomRecall', 100 * float(ind.get('recall_pooled', 0) or 0)), ('resultProbeThreeRandomRecallEdg', 100 * float(edg.get('recall_pooled', 0) or 0)), ('resultProbeThreeBeatsEveryDraw', ind.get('closure_beats_every_draw', 0)), ('resultProbeThreeBelowEveryDraw', ind.get('closure_below_every_draw', 0)), ('resultProbeThreeDraws', rb.get('draws_per_instance', 0)), ('resultProbeThreeClosureShare', round(100 * float(rb.get('closure_share_median', 0) or 0)))]
    if probe3:
        rows.append(('resultProbeThreeJoined', (probe3.get('join', {}) or {}).get('by_instance_id', 0)))
    if levers:

        def med(vals: Iterable[float]) -> float:
            v = sorted(vals)
            return v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2
        inf = [r['horizons']['inf'] for r in levers]
        one = [r['horizons']['1'] for r in levers]
        rows += [('resultSignatureMedian', med([h['closure_tokens_signature'] for h in inf])), ('resultCritMedian', med([h['crit'] for h in inf])), ('resultHorizonRatio', med([a['closure_tokens'] / b['closure_tokens'] for a, b in zip(one, inf, strict=True) if b['closure_tokens']]))]
        directed = [r['directed'] for r in levers if 'directed' in r]
        if directed:
            rows += [('resultUpstreamElements', med([d['upstream_elements'] for d in directed])), ('resultDownstreamElements', med([d['downstream_elements'] for d in directed]))]
    deg = gate.get('degenerate') or {}
    if deg:
        rows += [('resultFitsThirtyTwoK', deg.get('within_headline_total', 0)), ('resultFitsDegenerate', deg.get('within_headline_degenerate', 0)), ('resultFitsPartial', deg.get('within_headline_partial_classpath', 0)), ('resultFitsPartialCrit', ' and '.join((str(c) for c in deg.get('within_headline_partial_crit', [])))), ('resultFitsGenuine', len(deg.get('within_headline_genuine', []))), ('resultFitsGenuineAnyBudget', deg.get('genuine_any_budget', 0)), ('resultFitsOneTwentyEightK', (deg.get('by_budget') or {}).get('128000', {}).get('total', 0)), ('resultNoCallers', deg.get('n', 0))]
    frame = gate.get('frame') or {}
    if frame:
        rows.append(('resultFrameWithinThirtyTwoK', 100 * float(frame.get('within_32000', 0) or 0)))
    p2 = (summary.get('properties') or {}).get('P2') or {}
    if p2.get('reportable'):
        rows += [('resultPTwoApplicable', p2.get('applicable', 0)), ('resultPTwoAttempted', p2.get('attempted', 0)), ('resultPTwoRate', 100 * float(p2.get('applicability_rate', 0) or 0))]
        p2tok = p2.get('closure_tokens') or {}
        if p2tok:
            rows.append(('resultPTwoMedian', p2tok.get('median', 0)))
    sens = summary.get('sensitivity') or {}
    if sens:
        rows += [('resultLadderCells', len(sens)), ('resultFlipCells', sum((1 for c in sens.values() if c.get('verdict_flips') or 0))), ('resultFlipMaxRate', round(100 * max((c.get('verdict_flip_rate') or 0.0 for c in sens.values())), 1))]
        short_cell = min(sens.values(), key=lambda c: c.get('n_instances') or 0, default=None)
        if short_cell and (short_cell.get('n_instances') or 0):
            rows += [('resultLadderShortN', short_cell['n_instances']), ('resultLadderShortCell', short_cell.get('label', ''))]
    verd = summary.get('verdicts') or {}
    if verd:
        rows += [('resultVerdictRecords', sum(verd.values())), ('resultVerdictUnknown', verd.get('UNKNOWN', 0)), ('resultVerdictInadequate', verd.get('INADEQUATE', 0))]
    fn = summary.get('funnel') or {}
    by_repo = fn.get('by_repo') or {}
    if by_repo:
        from lacuna.corpora.base import ANALYSABLE as _ANALYSABLE
        keep = {s_.value if hasattr(s_, 'value') else str(s_) for s_ in _ANALYSABLE}
        losses = []
        for repo, counts in by_repo.items():
            lost = sum((v for k, v in counts.items() if k not in keep))
            if lost:
                losses.append((lost, sum(counts.values()), repo))
        losses.sort(reverse=True)
        rows += [('resultAttritionLost', sum((n for n, _, _ in losses))), ('resultAttritionRepos', len(losses)), ('resultAttritionClean', len(by_repo) - len(losses)), ('resultAttritionWhere', '; '.join((f"\\texttt{{{repo.replace('/', '/' + chr(92) + 'allowbreak ')}}} ({lost} of {tot})" for lost, tot, repo in losses)))]
    by_status = fn.get('by_status') or {}
    if by_status:
        rows.append(('resultUnmappable', by_status.get('TARGET_UNMAPPABLE', 0)))
    strata = gate.get('by_analysis_status') or {}
    if 'OK' in strata and 'PARTIAL_CLASSPATH' in strata:
        rows += [('resultStratumOkN', strata['OK']['n']), ('resultStratumPartialN', strata['PARTIAL_CLASSPATH']['n']), ('resultStratumOkAzero', strata['OK']['A0_median']), ('resultStratumPartialAzero', strata['PARTIAL_CLASSPATH']['A0_median'])]
        ok_full = strata['OK'].get('full_P1_median')
        if ok_full:
            rows += [('resultStratumOkFullMedian', ok_full), ('resultStratumOkGateFactor', round(float(ok_full) / 32000.0, 1))]
        part_full = strata['PARTIAL_CLASSPATH'].get('full_P1_median')
        if part_full:
            rows.append(('resultStratumPartialFullMedian', part_full))
    full_median = float(full.get('median', 0) or 0)
    if full_median:
        rows.append(('resultGateFactor', round(full_median / 32000.0, 1)))
    full_ci = (gate.get('full_P1') or {}).get('median_ci') or {}
    if full_ci and full_ci.get('low') == full_ci.get('low'):
        rows += [('resultFullMedianCiLow', full_ci['low']), ('resultFullMedianCiHigh', full_ci['high']), ('resultGateFactorCiLow', round(float(full_ci['low']) / 32000.0, 1))]
    n_clusters = summary.get('n_clusters')
    if n_clusters:
        rows.append(('resultClusterCount', n_clusters))
    a0 = gate.get('A0') or {}
    if a0:
        rows += [('resultSeedWithinThirtyTwoK', 100 * float(a0.get('within_32000', 0) or 0)), ('resultSeedWithinOneTwentyEightK', 100 * float(a0.get('within_128000', 0) or 0))]
    a1 = gate.get('A1') or {}
    if a1:
        rows.append(('resultDistOneWithinOneTwentyEightK', 100 * float(a1.get('within_128000', 0) or 0)))
    frame_median = float((gate.get('frame') or {}).get('median', 0) or 0)
    seed_median = float((gate.get('A0') or {}).get('median', 0) or 0)
    if frame_median and seed_median:
        rows.append(('resultFrameSeedRatio', round(seed_median / frame_median, 1)))
    if levers:

        def _med(vals: Iterable[float]) -> float:
            v = sorted(vals)
            return v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2
        sig = _med([r['horizons']['inf']['closure_tokens_signature'] for r in levers])
        body_ = _med([r['horizons']['inf']['closure_tokens'] for r in levers])
        if body_:
            rows.append(('resultSignatureSaving', round(100.0 * (1 - sig / body_))))
    dirs = gate.get('directions') or {}
    _DIRNAME = {'ascent': 'Ascent', 'descent': 'Descent', 'reference': 'Reference', 'dispatch': 'Dispatch', 'intra': 'Intra'}
    for key, camel in _DIRNAME.items():
        d = dirs.get(key) or {}
        if d:
            rows.append((f'resultDir{camel}Elements', d.get('restriction_median', 0)))
            rows.append((f'resultAblate{camel}Elements', d.get('ablation_median', 0)))
            if d.get('ablation_saving') is not None:
                rows.append((f'resultAblate{camel}Saving', round(100 * float(d['ablation_saving']), 1)))
            if d.get('ablation_saving_tokens') is not None:
                rows.append((f'resultAblate{camel}SavingTokens', round(100 * float(d['ablation_saving_tokens']), 1)))
            abl_tok = d.get('ablation_tokens_median')
            if abl_tok:
                rows.append((f'resultAblate{camel}Tokens', abl_tok))
                rows.append((f'resultAblate{camel}Factor', round(float(abl_tok) / 32000.0, 1)))
    if dirs.get('dominant'):
        rows.append(('resultDirDominant', dirs['dominant']))
    if dirs.get('dominant_saving_tokens') is not None:
        rows.append(('resultDirDominantSaving', round(100 * float(dirs['dominant_saving_tokens']), 1)))
    if dirs.get('dominant_elements'):
        rows.append(('resultDirDominantElements', dirs['dominant_elements']))
    for key, camel in (('seed', 'Seed'), ('closure', 'Closure'), ('tokens', 'Tokens')):
        st = (gate.get('stage_seconds') or {}).get(key)
        if st:
            rows.append((f'resultStage{camel}Median', f"{float(st['median']):.2f}"))
            rows.append((f'resultStage{camel}Pninety', f"{float(st['p90']):.2f}"))
    esc = summary.get('escapes') or {}
    if esc.get('declared'):
        rows += [('resultEscapesDeclared', esc['declared']), ('resultEscapesNeverFired', esc.get('never_fired', 0)), ('resultEscapesDangerous', esc.get('dangerous', 0))]
    sat = gate.get('saturation') or {}
    if sat.get('n'):
        rows += [('resultSaturatedN', sat['saturated']), ('resultSaturatedRate', round(100 * sat['saturated'] / sat['n'])), ('resultSaturationPct', round(100 * float(sat.get('fraction', 0.9))))]
        eligible = {k: v for k, v in (sat.get('per_repo') or {}).items() if v.get('n', 0) >= 3 and v.get('tokens_iqr_over_median') is not None}
        if eligible:
            tight = min(eligible, key=lambda k: eligible[k]['tokens_iqr_over_median'])
            loose = max(eligible, key=lambda k: eligible[k]['tokens_iqr_over_median'])
            rows += [('resultSaturationTightRepo', tight.replace('_', '\\_')), ('resultSaturationTightIqr', f"{eligible[tight]['tokens_iqr_over_median']:.2f}"), ('resultSaturationTightN', eligible[tight]['n']), ('resultSaturationLooseIqr', f"{eligible[loose]['tokens_iqr_over_median']:.2f}"), ('resultSaturationReposN', len(eligible))]
        per = sat.get('per_repo') or {}
        if per:
            biggest = max(per, key=lambda k: per[k]['n'])
            rows += [('resultSaturationBigRepo', biggest.replace('_', '\\_')), ('resultSaturationBigN', per[biggest]['n']), ('resultSaturationBigMedian', per[biggest]['elements_median'])]
    p3 = ((summary.get('properties') or {}).get('P3') or {}).get('closure_tokens') or {}
    if p3.get('n'):
        n3 = float(p3['n'])
        for budget, name in ((8000, 'EightK'), (32000, 'ThirtyTwoK'), (128000, 'OneTwentyEightK')):
            frac = p3.get(f'within_{budget}')
            if frac is not None:
                rows += [(f'resultPThreeWithin{name}n', int(round(frac * n3))), (f'resultPThreeWithin{name}', round(100 * float(frac), 1))]
    inert = 0
    for _digest, cell in (summary.get('sensitivity') or {}).items():
        ratio = cell.get('closure_tokens_ratio_median')
        if ratio is None:
            continue
        if 0.95 <= float(ratio) <= 1.05:
            inert += 1
        if cell.get('realizable') is False:
            rows.append(('resultLadderRatioUnrestricted', f'{float(ratio):.3f}'))
        if cell.get('entry_points') == 'declared-mains':
            rows.append(('resultLadderRatioMains', f'{float(ratio):.3f}'))
        if cell.get('heap_model') == 'none':
            rows.append(('resultLadderRatioNoHeap', f'{float(ratio):.3f}'))
    if summary.get('sensitivity'):
        rows += [('resultLadderCellsInert', inert), ('resultLadderCellsTotal', len(summary['sensitivity']))]
    frame = (gate.get('frame') or {}).get('median')
    if frame is not None:
        rows.append(('resultFrameMedian', frame))
    if budget_cells:
        best: dict[str, dict[int, dict[str, Any]]] = {}
        for c in budget_cells:
            best.setdefault(c['builder'], {})[c['budget']] = c
        cf = best.get('closure-first', {})
        lx = best.get('lexical-bm25', {})
        if cf:
            hi = max(cf)
            rows.append(('resultAssessableClosureFirst', f"{cf[hi]['assessable']} of {cf[hi]['instances']}"))
        if lx:
            hi = max(lx)
            rows.append(('resultAssessableLexical', f"{lx[hi]['assessable']} of {lx[hi]['instances']}"))
            rows.append(('resultAssessableLexicalN', lx[hi]['assessable']))
        rows.append(('resultBudgetOverruns', sum((c.get('over_budget', 0) for c in budget_cells))))
        filling = [c for c in budget_cells if c['builder'] != 'target-only']
        if filling:
            worst = max(((c['budget'] - c['median_realised_tokens']) / c['budget'] for c in filling))
            rows.append(('resultBudgetFillGap', round(100 * worst, 1)))
        floor = [c for c in budget_cells if c['builder'] == 'target-only']
        if floor:
            rows.append(('resultFloorTokens', max((c['median_realised_tokens'] for c in floor))))
        if cf:
            hi = max(cf)
            rows.append(('resultAssessableClosureFirstN', cf[hi]['assessable']))
    rows += _revision_rows(summary)
    out = []
    for name, value in rows:
        out.append(f'\\providecommand{{\\{name}}}{{}}\\renewcommand{{\\{name}}}{{{_fmt(value)}}}')
    return '\n'.join(out) + '\n'

def _pct_of(n: float, d: float) -> float:
    return round(100.0 * n / d, 1) if d else 0.0

def _revision_rows(summary: dict[str, Any]) -> list[tuple[str, object]]:
    rv = summary.get('revision') or {}
    gate = summary.get('gate') or {}
    rows: list[tuple[str, object]] = []
    screen = rv.get('fit_screen') or {}
    camel = {'8000': 'EightK', '32000': 'ThirtyTwoK', '128000': 'OneTwentyEightK', '1000000': 'OneM'}
    for prop, name in (('P1', 'POne'), ('P2', 'PTwo'), ('P3', 'PThree')):
        by_b = (screen.get(prop) or {}).get('by_budget') or {}
        for b, bname in camel.items():
            cell = by_b.get(b)
            if not cell:
                continue
            rows += [(f'resultFits{name}{bname}Total', cell['total']), (f'resultFits{name}{bname}Genuine', cell['genuine']), (f'resultFits{name}{bname}NoCallers', cell['degenerate']), (f'resultFits{name}{bname}Partial', cell['partial_classpath'])]
    if rv.get('p1_fits_max_elements') is not None:
        rows.append(('resultFitsMaxElements', rv['p1_fits_max_elements']))
    if rv.get('p1_fits_nocallers_max_elements') is not None:
        rows.append(('resultFitsNoCallersMaxElements', rv['p1_fits_nocallers_max_elements']))
    strata = rv.get('strata') or {}
    for key, name in (('OK|shadowed', 'OkShadowed'), ('OK|clean', 'OkClean'), ('PARTIAL_CLASSPATH|shadowed', 'PartialShadowed'), ('PARTIAL_CLASSPATH|clean', 'PartialClean')):
        st = strata.get(key)
        if st:
            rows += [(f'resultStratum{name}N', st['n']), (f'resultStratum{name}Median', st['full_P1_median'])]
    nc = rv.get('no_callers') or {}
    if nc.get('full_P1_median') is not None:
        rows.append(('resultNoCallersMedian', nc['full_P1_median']))
    p2m = rv.get('p2_matched') or {}
    if p2m.get('P1_median') is not None:
        rows += [('resultPTwoMatchedPOneMedian', p2m['P1_median']), ('resultPTwoWithinThirtyTwoKn', p2m.get('P2_within_headline', 0))]
    lc = rv.get('long_context') or {}
    if lc.get('n'):
        rows += [('resultFullWithinOneMn', lc['within_1000000']), ('resultFullWithinOneM', _pct_of(lc['within_1000000'], lc['n'])), ('resultFullWithinTwoMn', lc['within_2000000'])]
    by_repo_lc = lc.get('by_repository') or {}
    big_lc, out_lc = (by_repo_lc.get('big') or {}, by_repo_lc.get('outside') or {})
    if big_lc.get('n'):
        rows += [('resultBigOneMFits', big_lc['within_1000000']), ('resultBigTokensMedian', big_lc['tokens_median'])]
    if out_lc.get('n'):
        rows += [('resultOutsideBigN', out_lc['n']), ('resultOneMOutsideBigFits', out_lc['within_1000000']), ('resultOutsideBigMaxTokens', out_lc['tokens_max'])]
    base_fits = rv.get('base_cell_fits') or {}
    if base_fits.get('n'):
        rows.append(('resultBaseEntryEscapeN', base_fits['entry_point_escape_instances']))
    wd = rv.get('witness_distance') or {}
    if wd:
        rows += [('resultWitnessDistZero', wd.get('0', 0)), ('resultWitnessDistOne', wd.get('1', 0)), ('resultWitnessDistTwoPlus', sum((v for k, v in wd.items() if k.isdigit() and int(k) >= 2))), ('resultWitnessInteriorNonvacuous', wd.get('interior_nonvacuous', 0))]
    vbp = rv.get('verdicts_by_property') or {}
    for prop, name in (('P1', 'POne'), ('P2', 'PTwo'), ('P3', 'PThree')):
        v = vbp.get(prop) or {}
        if v:
            rows += [(f'resultVerdict{name}Inadequate', v.get('INADEQUATE', 0)), (f'resultVerdict{name}Unknown', v.get('UNKNOWN', 0)), (f'resultVerdict{name}Records', sum(v.values()))]
    reasons = summary.get('unknown_reasons') or {}
    for key, name in (('bridge-truncated', 'Bridge'), ('unmappable', 'Unmappable'), ('reflection', 'Reflection'), ('no-writer', 'NoWriter'), ('entry-points', 'EntryPoints')):
        if key in reasons:
            rows.append((f'resultReason{name}N', reasons[key]))
    sat = gate.get('saturation') or {}
    if sat.get('n_nonref'):
        rows += [('resultSaturatedNonrefN', sat['saturated_nonref']), ('resultSaturatedNonrefDenom', sat['n_nonref'])]
        per = sat.get('per_repo') or {}
        big = max(per, key=lambda k: per[k]['n']) if per else None
        if big:
            rows += [('resultSaturationBigSaturated', per[big]['saturated_nonref']), ('resultSaturationBigNonref', per[big]['n_nonref']), ('resultSaturationBigMax', per[big]['elements_max'])]
        rows += [('resultCorpusJacksonN', sum((v['n'] for k, v in per.items() if k.startswith('fasterxml/jackson')))), ('resultCorpusLogstashN', (per.get('elastic/logstash') or {}).get('n', 0))]
        lg = per.get('elastic/logstash')
        if lg:
            rows += [('resultSaturationLogstashSaturated', lg['saturated_nonref']), ('resultSaturationLogstashNonref', lg['n_nonref'])]
    dirs = gate.get('directions') or {}
    rom: dict[str, float] = {k: float(v['ablation_saving_tokens_ratio_of_medians']) for k, v in dirs.items() if isinstance(v, dict) and v.get('ablation_saving_tokens_ratio_of_medians') is not None}
    if rom:
        top = max(rom, key=lambda k: rom[k])
        rows += [('resultDirRatioOfMediansMax', round(100 * float(rom[top]), 1)), ('resultDirRatioOfMediansMaxName', top)]
    sens = summary.get('sensitivity') or {}
    if sens:
        applicable = [c for c in sens.values() if not c.get('inert_by_construction')]
        inert = [c for c in applicable if c.get('closure_tokens_ratio_median') is not None and 0.95 <= float(c['closure_tokens_ratio_median']) <= 1.05]
        rows += [('resultLadderApplicable', len(applicable)), ('resultLadderInertApplicable', len(inert)), ('resultLadderInertByConstruction', len(sens) - len(applicable))]
        for c in sens.values():
            ratio = c.get('closure_tokens_ratio_median')
            if ratio is None:
                continue
            if c.get('cg') == '0-CFA':
                rows.append(('resultLadderRatioZeroCfa', f'{float(ratio):.3f}'))
            if c.get('changed') == ['cha_augmented']:
                rows.append(('resultLadderRatioNoCha', f'{float(ratio):.3f}'))
            if c.get('changed') == ['field_sens']:
                rows.append(('resultLadderRatioFieldInsens', f'{float(ratio):.3f}'))
            if c.get('changed') == ['frame']:
                rows.append(('resultLadderRatioWideFrame', f'{float(ratio):.3f}'))
        for c in sens.values():
            fits = c.get('cell_fits') or {}
            if not fits.get('n'):
                continue
            within = fits.get('full_P1_within') or {}
            if c.get('changed') == ['entry_points'] and c.get('entry_points') == 'declared-mains':
                rows += [('resultMainsMedian', fits['full_P1_median']), ('resultMainsWithinThirtyTwoKn', within.get('32000', 0)), ('resultMainsWithinOneTwentyEightKn', within.get('128000', 0)), ('resultMainsEntryEscapeN', fits['entry_point_escape_instances'])]
            if c.get('changed') == ['heap_model'] and c.get('heap_model') == 'none':
                rows += [('resultNoHeapN', fits['n']), ('resultNoHeapWithinThirtyTwoKn', within.get('32000', 0)), ('resultNoHeapWithinOneTwentyEightKn', within.get('128000', 0))]
    esc = summary.get('escapes') or {}
    if esc.get('declared'):
        rows += [('resultEscapesDeclaredBefore', esc.get('declared_before_data', 0)), ('resultEscapesAddedAfter', esc.get('added_after_data', 0)), ('resultEscapesNoDetector', esc.get('no_detector', 0))]
        share = esc.get('share') or {}
        for key, name in (('shadowing_jars', 'Shadowing'), ('framework_callbacks', 'Callbacks'), ('dependency_injection', 'Injection'), ('reflection', 'Reflection'), ('serialisation', 'Serialization'), ('depth_cap', 'DepthCap'), ('bridge_truncated', 'Bridge'), ('unmappable', 'Unmappable')):
            if share.get(key) is not None:
                rows.append((f'resultEscapeShare{name}', round(100 * float(share[key]))))
    g = summary.get('graph') or {}
    if g:
        rows += [('resultGraphNodesMedian', (g.get('edg_nodes') or {}).get('median', 0)), ('resultGraphAppNodesMedian', (g.get('app_nodes') or {}).get('median', 0)), ('resultAnalysisSecondsMedian', f"{float((g.get('analysis_seconds') or {}).get('median', 0)):.0f}"), ('resultAnalysisSecondsPninety', f"{float((g.get('analysis_seconds') or {}).get('p90', 0)):.0f}"), ('resultClosureOverNodes', round(100 * float((g.get('closure_over_nodes') or {}).get('median', 0)))), ('resultClosureOverNodesMax', round(100 * float((g.get('closure_over_nodes') or {}).get('max', 0)))), ('resultCappedClosureOverNodes', round(100 * float((g.get('capped_closure_over_nodes') or {}).get('median', 0))))]
        cov = g.get('closure_over_nodes_within_1000000') or {}
        if cov.get('n'):
            rows.append(('resultOneMFitsCoverageMedian', round(100 * float(cov['median']))))
    sn = summary.get('sanity') or {}
    if sn.get('rows'):
        v = sn.get('verdicts') or {}
        vx = sn.get('verdicts_without_escapes') or {}
        rows += [('resultSanityN', sn['rows']), ('resultSanityReproduces', sn.get('reproduces_record', 0)), ('resultSanityConverged', sn.get('converged', 0)), ('resultSanityUnknownN', v.get('UNKNOWN', 0)), ('resultSanityAdequateN', v.get('ADEQUATE', 0) + v.get('ADEQUATE_UP_TO_K', 0)), ('resultSanityAdequateNoEscapesN', vx.get('ADEQUATE', 0) + vx.get('ADEQUATE_UP_TO_K', 0)), ('resultSanityNoContextAdequate', sn.get('no_context_adequate', 0)), ('resultSanitySupersetTokens', (sn.get('superset_tokens') or {}).get('median', 0)), ('resultSanitySupersetElements', (sn.get('superset_elements') or {}).get('median', 0)), ('resultSanitySupersetOverFull', round(100 * float((sn.get('superset_over_full_closure') or {}).get('median', 0)))), ('resultSanitySeededChecked', sn.get('seeded_checked', 0)), ('resultSanitySeededPass', sn.get('seeded_pass', 0))]
    bl = summary.get('builders') or {}
    for key, name in (('target-only', 'TargetOnly'), ('lexical-bm25', 'Lexical'), ('dependence-1hop', 'OneHop'), ('closure-first', 'ClosureFirst')):
        cells = bl.get(key) or {}
        for budget, bname in (('32000', 'ThirtyTwoK'), ('128000', 'OneTwentyEightK')):
            c = cells.get(budget)
            if not c:
                continue
            rows.append((f'resultBuilder{name}Assessable{bname}', c.get('assessable', 0)))
            rows.append((f'resultBuilder{name}NoCallers{bname}', c.get('assessable_no_callers', 0)))
            if c.get('median_seed_recall') is not None:
                rows.append((f'resultBuilder{name}Recall{bname}', round(100 * float(c['median_seed_recall']))))
    cd = summary.get('conformance_d34') or {}
    if cd.get('measured'):
        ctx = (cd.get('def_3_4') or {}).get('contexts') or {}
        rows += [('resultConfMeasured', cd['measured'])]
        for cname, name in (('slice_capped', 'Capped'), ('slice', 'Full'), ('reference', 'Reference')):
            c = ctx.get(cname) or {}
            if c.get('n'):
                rows += [(f'resultConf{name}ConformantTouched', c.get('conformant_touched', 0)), (f'resultConf{name}ConformantIntroduced', c.get('conformant_introduced', 0))]
    lev = summary.get('levers') or {}
    if lev:
        rows += [('resultHorizonCritOne', (lev.get('1') or {}).get('crit_median', 0)), ('resultHorizonDeltaTokens', lev.get('horizon_one_delta_tokens_median', 0)), ('resultHorizonRatioMin', f"{float(lev.get('horizon_one_ratio_min') or 0):.3f}"), ('resultSignatureWithinThirtyTwoKn', (lev.get('signature') or {}).get('within_32000', 0)), ('resultSignatureWithinOneTwentyEightKn', (lev.get('signature') or {}).get('within_128000', 0))]
    return rows
