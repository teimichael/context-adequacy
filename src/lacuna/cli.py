from __future__ import annotations
import json
import sys
from pathlib import Path
import typer
from rich.console import Console
from rich.table import Table
from lacuna import SCHEMA_VERSION, __version__
app = typer.Typer(add_completion=False, help='Lacuna: interprocedural closure as a context adequacy check.')
corpus_app = typer.Typer(help='Corpus download and inventory.')
experiment_app = typer.Typer(help='Run and report experiments.')
app.add_typer(corpus_app, name='corpus')
app.add_typer(experiment_app, name='experiment')
console = Console()

@app.command()
def version() -> None:
    from lacuna.model.approx import DEFAULT_APPROXIMATION
    from lacuna.tokens import TOKENIZER_NAME, TOKENIZER_SHA256
    console.print(f'lacuna {__version__} (record schema {SCHEMA_VERSION})')
    console.print(f'engine: {DEFAULT_APPROXIMATION.engine} {DEFAULT_APPROXIMATION.engine_revision}')
    console.print(f'default A digest: {DEFAULT_APPROXIMATION.digest()}')
    console.print(f'tokenizer: {TOKENIZER_NAME} sha256={TOKENIZER_SHA256[:16]}...')

@app.command()
def doctor(clean: bool=typer.Option(False, '--clean', help="Remove this project's leftover containers."), data_dir: Path=typer.Option(Path('data'), help='Where the run lock lives.')) -> None:
    from lacuna.harness.resources import LOCK_NAME, clean_containers, inspect_host, orphaned_containers, reap_orphans, running_experiments
    state = inspect_host()
    console.print('[bold]host[/bold]')
    console.print(state.render())
    lock = data_dir / LOCK_NAME
    if lock.exists():
        console.print(f'[yellow]  run lock      HELD: {lock.read_text().strip()}[/yellow]')
    else:
        console.print('  run lock      free')
    live = running_experiments()
    if live:
        console.print(f'[yellow]  live runs     {len(live)} lacuna experiment process(es):[/yellow]')
        for pid, cmd in live[:5]:
            console.print(f'[yellow]                pid {pid}: {cmd[:80]}[/yellow]')
    else:
        console.print('  live runs     none')
    orphans = orphaned_containers()
    if orphans:
        console.print(f"[yellow]  orphans       {len(orphans)} container(s) whose owner is gone: {', '.join(orphans[:4])}[/yellow]")
        if clean:
            reaped = reap_orphans()
            console.print(f"[green]  reaped {len(reaped)}: {', '.join(reaped)}[/green]")
    if clean and state.stale_containers:
        removed = clean_containers()
        console.print(f"[green]  removed {len(removed)} container(s): {', '.join(removed)}[/green]")
    elif state.stale_containers:
        console.print('  (run `lacuna doctor --clean` to remove them)')
    problems = []
    if not state.docker_ok:
        problems.append('docker unreachable')
    if not state.analyzer_image:
        problems.append('analyser image missing (./scripts/build_analyzer.sh)')
    if state.free_disk_gb < 50:
        problems.append(f'only {state.free_disk_gb:.0f} GB disk free')
    if state.available_gb and state.available_gb < 16:
        problems.append(f'only {state.available_gb:.1f} GB memory available')
    if live:
        problems.append(f'{len(live)} lacuna run(s) already active; wait for them to finish before starting another')
    if orphans and (not clean):
        problems.append(f'{len(orphans)} orphaned container(s); run `lacuna doctor --clean`')
    if problems:
        console.print('[red]not ready: ' + '; '.join(problems) + '[/red]')
        raise typer.Exit(1)
    console.print('[green]ready[/green]')

@corpus_app.command('stats')
def corpus_stats(data_dir: Path=typer.Option(Path('data'))) -> None:
    from lacuna.corpora import CORPORA
    table = Table('corpus', 'repository', 'valid', 'pool')
    totals: dict[str, tuple[int, int]] = {}
    for name, cls in CORPORA.items():
        try:
            stats = cls(data_dir / 'corpora' / name).stats()
        except FileNotFoundError as exc:
            console.print(f'[yellow]{name}: {exc}[/yellow]')
            continue
        totals[name] = (stats.valid, stats.total_rows)
        for repo, n in stats.by_repo.items():
            table.add_row(name, repo, str(n), '')
        table.add_row(f'[bold]{name}[/bold]', '[bold]TOTAL[/bold]', f'[bold]{stats.valid}[/bold]', str(stats.total_rows))
    console.print(table)
    console.print(json.dumps({k: {'valid': v[0], 'pool': v[1]} for k, v in totals.items()}))

@experiment_app.command('validate')
def experiment_validate(config: Path) -> None:
    from lacuna.experiment.config import ExperimentConfig
    cfg = ExperimentConfig.load(config)
    console.print(f'[green]{cfg.name}[/green]: configuration is valid')
    for name, approx in cfg.resolved_approximations():
        console.print(f'  A[{name}] = {approx.digest()}  k={approx.depth_cap} cg={approx.cg.value} frame={approx.frame.value}')

@experiment_app.command('run')
def experiment_run(config: Path, dry_run: bool=typer.Option(False, '--dry-run', help='Validate and plan, run nothing.'), quiet: bool=typer.Option(False, '--quiet')) -> None:
    from lacuna.experiment.config import ExperimentConfig
    from lacuna.experiment.runner import run, select_instances, write_records
    from lacuna.harness.resources import ResourcesUnavailable, RunLocked
    cfg = ExperimentConfig.load(config)
    instances = select_instances(cfg)
    console.print(f'[bold]{cfg.name}[/bold]: {len(instances)} instances, {len(cfg.approximations)} A-cells, {cfg.profile.parallel_slots} slots x {cfg.profile.cpus_per_analysis} cores')
    if dry_run:
        for name, approx in cfg.resolved_approximations():
            console.print(f'  A[{name}] {approx.digest()}')
        for inst in instances[:20]:
            console.print(f'  - {inst.instance_id} ({inst.build_system})')
        if len(instances) > 20:
            console.print(f'  ... and {len(instances) - 20} more')
        raise typer.Exit(0)
    out = cfg.results_dir / cfg.name
    out.mkdir(parents=True, exist_ok=True)
    try:
        outcome = run(cfg, progress=not quiet, out_dir=out)
    except (RunLocked, ResourcesUnavailable) as exc:
        console.print(f'[red]{exc}[/red]')
        raise typer.Exit(1) from None
    (out / 'funnel.json').write_text(outcome.funnel.to_json())
    write_records(outcome.records, out / 'records.jsonl')
    (out / 'ladder-failures.jsonl').write_text(''.join((f.model_dump_json() + '\n' for f in outcome.ladder_failures)))
    (out / 'config.resolved.json').write_text(cfg.model_dump_json(indent=2))
    console.print()
    console.print(outcome.funnel.render())
    console.print()
    console.print(f'[green]{len(outcome.records)} records[/green] in {outcome.seconds:.0f}s -> {out}')

@experiment_app.command('conformance')
def experiment_conformance(config: Path, limit: int=typer.Option(None, help='Analyse at most this many instances.'), cached_only: bool=typer.Option(False, '--cached-only', help="Read both revisions' analyses from the cache; never build or analyse. A miss is recorded as a placeholder row, not run.")) -> None:
    import json as _json
    from lacuna.experiment.config import ExperimentConfig
    from lacuna.experiment.conformance import measure, measure_cached, summarise
    from lacuna.experiment.runner import select_instances
    cfg = ExperimentConfig.load(config)
    instances = select_instances(cfg)[:limit or None]
    _, approx = cfg.resolved_approximations()[0]
    out = cfg.results_dir / cfg.name
    out.mkdir(parents=True, exist_ok=True)
    path = out / 'conformance.jsonl'
    rows = []
    tmp = path.with_suffix('.jsonl.tmp')
    with tmp.open('w') as fh:
        for n, inst in enumerate(instances, 1):
            if cached_only:
                rec = measure_cached(inst, approx, cfg.data_dir)
            else:
                rec = measure(inst, approx, cfg.data_dir, cpus=cfg.profile.cpus_per_analysis)
            fh.write(rec.model_dump_json() + '\n')
            rows.append(rec)
            console.print(f'[{n:3d}/{len(instances)}] {inst.instance_id:45} {rec.status.value:20}')
    tmp.replace(path)
    report_dir = out / 'report'
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / 'conformance.json').write_text(_json.dumps(summarise(rows), indent=2, default=float))
    console.print(f'-> {path}')

@app.command('verdict')
def verdict_cmd(edg: Path=typer.Option(..., help='EDG produced by the analyser.'), index: Path=typer.Option(..., help='Declaration index from the analyser.'), sources: Path=typer.Option(..., help='Source root.'), target: str=typer.Option(..., help='Element identity of the edit target.'), context: Path=typer.Option(None, help='File of element identities, one per line.')) -> None:
    from lacuna.criterion.closure import analyse
    from lacuna.criterion.frame import frame
    from lacuna.criterion.properties import build_seed
    from lacuna.criterion.report import render_verdict
    from lacuna.criterion.verdict import decide
    from lacuna.criterion.witness import canonical_witness
    from lacuna.experiment.evaluate import load_artifacts
    from lacuna.model.approx import DEFAULT_APPROXIMATION, LEVELS, Property
    graph, program_index, _, _, _ = load_artifacts(edg, index, [sources])
    if target not in graph:
        console.print(f'[red]target {target} is not an element of the graph[/red]')
        raise typer.Exit(2)
    ctx = [ln.strip() for ln in context.read_text().splitlines() if ln.strip()] if context else []
    approx = DEFAULT_APPROXIMATION
    fr = frame(target, program_index, approx.frame)
    seed = build_seed(graph, target, fr, Property.P1_CALLER_BEHAVIOUR)
    result = analyse(graph, seed.seed, ctx, depth_cap=approx.depth_cap, levels=LEVELS)
    v = decide(result, depth_cap=approx.depth_cap)
    w = canonical_witness(graph, result)
    print(render_verdict(graph, target, Property.P1_CALLER_BEHAVIOUR, approx, result, v, w))

@app.command('crosscheck')
def crosscheck_cmd(instance_id: str=typer.Argument(..., help='Instance to cross-check.'), corpus: str=typer.Option('multi-swe-bench', help='Corpus the instance is from.'), data_dir: Path=typer.Option(Path('data'), help='Data directory.'), cpus: int=typer.Option(4, help='Cores pinned for the analysis.'), memory_gb: int=typer.Option(8, help='JVM heap ceiling and container --memory.')) -> None:
    from lacuna.analysis.crosscheck import compare
    from lacuna.corpora import CORPORA
    from lacuna.corpora.base import Status
    from lacuna.experiment.evaluate import load_artifacts, resolve_oracle_target
    from lacuna.harness.analyze import analyse_instance
    from lacuna.harness.prepare import Paths, prepare
    from lacuna.harness.resources import RunLock
    from lacuna.model.approx import DEFAULT_APPROXIMATION
    if corpus not in CORPORA:
        console.print(f'[red]unknown corpus {corpus!r}; known: {sorted(CORPORA)}[/red]')
        raise typer.Exit(2)
    instances = {i.instance_id: i for i in CORPORA[corpus](data_dir / 'corpora' / corpus).instances()}
    instance = instances.get(instance_id)
    if instance is None:
        console.print(f'[red]{instance_id} is not an instance of {corpus}[/red]')
        raise typer.Exit(2)
    approx = DEFAULT_APPROXIMATION
    with RunLock(data_dir):
        prepared = data_dir / 'instances' / instance_id
        manifest = prepare(instance, prepared, cpus=cpus, memory_gb=memory_gb)
        if manifest.status is not Status.OK:
            console.print(f'[red]{instance_id}: {manifest.status.value} {manifest.note}[/red]')
            raise typer.Exit(1)
        fast = analyse_instance(instance_id, instance.base_commit, prepared, manifest, approx, data_dir / 'analysis', cpus=cpus, memory_gb=memory_gb)
        if fast.status is not Status.OK or fast.edg_path is None:
            console.print(f'[red]{instance_id}: FAST analysis {fast.status.value}[/red]')
            raise typer.Exit(1)
        graph, _, source_index, _, _ = load_artifacts(fast.edg_path, fast.index_path, [Paths(prepared).sources])
        resolution = resolve_oracle_target(instance, source_index, graph)
        if resolution.target is None:
            console.print(f'[red]{instance_id}: target unmappable[/red]')
            raise typer.Exit(1)
        both = analyse_instance(instance_id, instance.base_commit, prepared, manifest, approx, data_dir / 'analysis', cpus=cpus, memory_gb=memory_gb, mode='BOTH', seeds=[resolution.target])
        if both.status is not Status.OK:
            console.print(f'[red]{instance_id}: BOTH analysis {both.status.value}[/red]')
            raise typer.Exit(1)
    stats = both.stats.get('sdg_crosscheck', {}) if both.stats else {}
    result = compare(instance_id, resolution.target, graph, stats, depth_cap=approx.depth_cap)
    print(result.summary_line())
    if result.missed_count:
        console.print('[red]FAST missed elements the SDG reached. Sec. 5.1 says the default must change.[/red]')
        for e in result.missed_by_fast:
            print(f'  {e}')
        raise typer.Exit(1)
rq5_app = typer.Typer(help='RQ4 annotation overlap.')
app.add_typer(rq5_app, name='annotations')

@rq5_app.command('containment')
def rq5_containment(results_dir: Path=typer.Argument(..., help='A results directory from `experiment run`.'), corpus: str=typer.Option('multi-swe-bench', help='Our corpus.'), data_dir: Path=typer.Option(Path('data'), help='Data directory.'), baseline_draws: int=typer.Option(20, min=1, help='Size-matched random draws per instance and pool (D108).'), seed_override: int | None=typer.Option(None, '--seed', help="Seed for the random baseline. Default: the run's own `seed`.")) -> None:
    import json
    from lacuna.analysis.gold_context import BASELINE_SEED, aggregate, containment_for, gold_elements_for, join_by_instance, join_report, load_java, random_baseline
    from lacuna.corpora import CORPORA
    from lacuna.criterion.closure import analyse
    from lacuna.criterion.frame import frame
    from lacuna.criterion.properties import PROPERTY_KINDS, build_seed
    from lacuna.experiment.evaluate import load_artifacts, resolve_oracle_target
    from lacuna.experiment.funnel import Funnel
    from lacuna.harness.analyze import NoCachedAnalysis, resolve_cached_analysis
    from lacuna.harness.prepare import Paths
    from lacuna.model.approx import DEFAULT_APPROXIMATION as A
    from lacuna.model.approx import Property
    base_approx = A
    run_seed = BASELINE_SEED
    resolved = results_dir / 'config.resolved.json'
    if resolved.exists():
        from lacuna.experiment.config import ExperimentConfig
        cfg = ExperimentConfig.model_validate_json(resolved.read_text())
        base_approx = cfg.resolved_approximations()[0][1]
        run_seed = cfg.seed
    baseline_seed = run_seed if seed_override is None else seed_override
    gold = load_java(data_dir)
    instances = {i.instance_id: i for i in CORPORA[corpus](data_dir / 'corpora' / corpus).instances()}
    analysed = set()
    if (results_dir / 'funnel.jsonl').exists():
        analysed = {e.instance_id for e in Funnel.read(results_dir / 'funnel.jsonl').analysable()}
    joined = join_by_instance(gold, analysed or set(instances))
    rep = join_report(gold, instances.values(), analysed)
    P1 = Property.P1_CALLER_BEHAVIOUR
    results = []
    baselines = []
    not_cached: list[str] = []
    for iid, row in sorted(joined.items()):
        inst = instances.get(iid)
        prepared = data_dir / 'instances' / iid
        if inst is None or not prepared.exists():
            continue
        try:
            d = resolve_cached_analysis(iid, inst.base_commit, prepared, base_approx, data_dir / 'analysis')
        except NoCachedAnalysis:
            not_cached.append(iid)
            continue
        graph, pidx, sidx, _, _ = load_artifacts(d / 'edg.jsonl', d / 'index.json', [Paths(prepared).sources])
        res = resolve_oracle_target(inst, sidx, graph)
        if res.target is None:
            continue
        seed = build_seed(graph, res.target, frame(res.target, pidx, base_approx.frame), P1)
        out = analyse(graph, seed.seed, (), depth_cap=None, kinds=PROPERTY_KINDS[P1], realizable=base_approx.realizable)
        results.append(containment_for(iid, row, out.slice_, sidx))
        baselines.append(random_baseline(iid, gold_elements_for(row, sidx), out.slice_, graph.nodes(), sidx.by_element, draws=baseline_draws, seed=baseline_seed))
        ib = baselines[-1].pools['indexed']
        console.print(f'  {iid}  gold={results[-1].gold_elements} contained={results[-1].contained} closure/edg={baselines[-1].closure_share:.2f} random(indexed)={sum(ib.contained) / max(len(ib.contained), 1):.1f}')
    if not_cached:
        console.print(f"[yellow]{len(not_cached)} joined instance(s) have no cached analysis and were skipped, not analysed: {', '.join(not_cached[:5])}[/yellow]")
    out_dir = results_dir / 'report'
    out_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / 'containment.jsonl').write_text('\n'.join((r.model_dump_json() for r in results)) + ('\n' if results else ''))
    agg = aggregate(results, rep, baselines)
    (out_dir / 'probe3.json').write_text(json.dumps(agg, indent=2, default=float))
    console.print(json.dumps(agg, indent=2, default=float))

@app.command('adequacy-budget')
def adequacy_budget_cmd(results_dir: Path=typer.Argument(..., help='A results directory from `experiment run`.'), corpus: str=typer.Option('multi-swe-bench', help='Corpus the instances are from.'), data_dir: Path=typer.Option(Path('data'), help='Data directory.'), budgets: str | None=typer.Option(None, help='Comma-separated budgets. Default: the budgets THIS RUN was configured with, read from config.resolved.json.')) -> None:
    import json
    from typing import Any
    from lacuna.analysis.adequacy_budget import cell, seed_recall, table
    from lacuna.builders import BUILDERS
    from lacuna.builders.bm25 import BM25Index
    from lacuna.builders.safety import BuilderCriterion
    from lacuna.corpora import CORPORA
    from lacuna.criterion.closure import analyse
    from lacuna.criterion.frame import frame
    from lacuna.criterion.properties import build_seed
    from lacuna.experiment.evaluate import _sources_for, load_artifacts, resolve_oracle_target
    from lacuna.harness.analyze import AnalysisResult, NoCachedAnalysis, resolve_cached_analysis
    from lacuna.harness.prepare import Paths
    from lacuna.model.approx import DEFAULT_APPROXIMATION as A
    from lacuna.model.approx import Property
    base_approx = A
    cfg = None
    resolved = results_dir / 'config.resolved.json'
    if resolved.exists():
        from lacuna.experiment.config import ExperimentConfig
        try:
            cfg = ExperimentConfig.model_validate_json(resolved.read_text())
        except Exception:
            cfg = None
        if cfg is not None:
            base_approx = cfg.resolved_approximations()[0][1]
    if budgets is not None:
        buds = [int(b) for b in budgets.split(',') if b.strip()]
        budget_source = '--budgets'
    elif cfg is not None:
        buds = list(cfg.budgets)
        budget_source = 'config.resolved.json'
    else:
        buds = [8000, 32000, 128000]
        budget_source = 'library default (no resolved config found)'
    console.print(f'  budgets {buds} from {budget_source}')
    instances = {i.instance_id: i for i in CORPORA[corpus](data_dir / 'corpora' / corpus).instances()}
    cached: dict[str, Path] = {}
    unresolved: list[str] = []
    for iid, prepared_inst in sorted(instances.items()):
        prepared = data_dir / 'instances' / iid
        if not prepared.exists():
            continue
        try:
            cached[iid] = resolve_cached_analysis(iid, prepared_inst.base_commit, prepared, base_approx, data_dir / 'analysis')
        except NoCachedAnalysis:
            unresolved.append(iid)
    if not cached:
        console.print(f'[red]no cached analysis matches the base cell (A={base_approx.analyser_digest()}); run the criterion arm first.[/red]\n[red]{len(unresolved)} prepared instances had no matching analysis.[/red]')
        raise typer.Exit(2)
    if unresolved:
        console.print(f"  [yellow]{len(unresolved)} prepared instances have no analysis for this cell and are excluded: {', '.join(unresolved[:5])}{(' ...' if len(unresolved) > 5 else '')}[/yellow]")
    acc: dict[tuple[str, int], dict[str, Any]] = {(b, bud): {'depths': [], 'realised': [], 'n': 0, 'over': 0, 'recall': {}, 'omit_empty': 0, 'verdicts': {}} for b in BUILDERS for bud in buds}
    P1 = Property.P1_CALLER_BEHAVIOUR
    for iid, d in sorted(cached.items()):
        inst = instances.get(iid)
        if inst is None:
            continue
        graph, pidx, sidx, _, _ = load_artifacts(d / 'edg.jsonl', d / 'index.json', [Paths(data_dir / 'instances' / iid).sources])
        res = resolve_oracle_target(inst, sidx, graph)
        if res.target is None:
            continue
        fr = frame(res.target, pidx, base_approx.frame)
        seed = build_seed(graph, res.target, fr, P1)
        sources = _sources_for(sidx, set(graph.nodes()))
        analysis = AnalysisResult.model_validate_json((d / 'result.json').read_text())
        crit = BuilderCriterion.from_analysis(base_approx, P1, frame=fr, element_reasons=analysis.element_reasons, bridges_truncated=int((analysis.stats or {}).get('bridges_truncated', 0)))
        issue = inst.problem_statement[:2000]
        ranking = BM25Index(sources).rank(issue or sources.get(res.target, ''))
        for bname, cls in BUILDERS.items():
            for bud in buds:
                built = cls().build(graph=graph, sources=sources, target=res.target, seed=seed.seed, budget=bud, issue_text=issue, criterion=crit, ranking=ranking)
                slot = acc[bname, bud]
                slot['n'] += 1
                slot['realised'].append(built.realised_tokens)
                if built.realised_tokens > bud:
                    slot['over'] += 1
                if built.verdict is not None:
                    v = built.verdict.value.value
                    slot['verdicts'][v] = slot['verdicts'].get(v, 0) + 1
                slot['recall'][iid] = round(seed_recall(seed.seed, built.context), 6)
                out = analyse(graph, seed.seed, list(built.context), depth_cap=crit.depth_cap, kinds=crit.kinds, realizable=crit.realizable)
                if out.depth is None:
                    slot['omit_empty'] += 1
                elif out.depth >= 0:
                    slot['depths'].append(out.depth)
        console.print(f'  {iid}')
    cells = [cell(b, bud, v['depths'], v['realised'], v['n'], v['over'], recall=v['recall'], omit_empty=v['omit_empty'], verdicts=v['verdicts']) for (b, bud), v in acc.items()]
    out_dir = results_dir / 'report'
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'tab-adequacy-budget.tex').write_text(table(cells, buds) + '\n')
    (out_dir / 'adequacy-budget.json').write_text(json.dumps([c.model_dump() for c in cells], indent=2, default=float))
    print(table(cells, buds))
    for c in cells:
        full = sum((1 for r in c.seed_recall.values() if r == 1.0))
        if full != c.assessable:
            console.print(f'  [red]{c.builder}@{c.budget}: {full} contexts hold the whole seed but {c.assessable} are assessable[/red]')
    console.print(f'  {len(cached)} cached, {len(unresolved)} without an analysis for this cell, {sum((c.over_budget for c in cells))} over budget')
    console.print(f"  wrote {out_dir / 'tab-adequacy-budget.tex'}")

@experiment_app.command('graph-stats')
def experiment_graph_stats(results_dir: Path=typer.Argument(..., help='A results directory from `experiment run`.'), data_dir: Path=typer.Option(Path('data'), help='Data directory holding the cache.')) -> None:
    import json
    from lacuna.analysis.graph_stats import FILENAME, NoResolvedConfig, build_rows, summarise, write_rows
    if not results_dir.exists():
        raise typer.BadParameter(f'no such results directory: {results_dir}')
    try:
        rows, missing = build_rows(results_dir, data_dir)
    except NoResolvedConfig as exc:
        console.print(f'[red]{exc}[/red]')
        raise typer.Exit(2) from None
    if missing:
        console.print(f"[red]{len(missing)} base-cell instance(s) have no cached analysis: {', '.join(missing[:5])}{(' ...' if len(missing) > 5 else '')}. Nothing written.[/red]")
        raise typer.Exit(1)
    out = results_dir / FILENAME
    write_rows(rows, out)
    agg = summarise(rows)
    print(json.dumps(agg, indent=2, default=float))
    console.print(f'  wrote {out} ({len(rows)} rows)')
    if agg['not_joined']:
        console.print(f"[red]{len(agg['not_joined'])} row(s) failed a join check (see `checks`): {', '.join(agg['not_joined'][:5])}[/red]")
        raise typer.Exit(1)

@experiment_app.command('sanity')
def experiment_sanity(results_dir: Path=typer.Argument(..., help='A results directory from `experiment run`.'), corpus: str=typer.Option('multi-swe-bench', help='Corpus the instances are from.'), data_dir: Path=typer.Option(Path('data'), help='Data directory holding the cache.'), iterations: int=typer.Option(10, min=0, help='Cap on C := C u Omit(C) steps; a capped row counts as unconverged.'), only: str | None=typer.Option(None, help='Comma-separated instance ids. Prints their rows; the released file is written only by a full pass.')) -> None:
    import json
    from lacuna.analysis.graph_stats import NoResolvedConfig
    from lacuna.analysis.sanity import FILENAME, SanityRow, run, summarise, write_rows
    if not results_dir.exists():
        raise typer.BadParameter(f'no such results directory: {results_dir}')
    wanted = [s.strip() for s in only.split(',') if s.strip()] if only else None

    def progress(row: SanityRow) -> None:
        seeded = {True: 'pass', False: 'FAIL', None: 'n/a'}[row.seeded_pass]
        repro = '' if row.reproduces_record else f'  MISMATCH {row.reproduction_mismatches}'
        console.print(f"  {row.instance_id:45} {row.verdict:16} |C*|={row.superset_elements:6d} tok={row.superset_tokens:9d} it={row.iterations}{('' if row.converged else '(capped)')} seeded={seeded} {row.seconds:5.1f}s{repro}")
    try:
        outcome = run(results_dir, data_dir, corpus=corpus, iteration_cap=iterations, only=wanted, on_row=progress)
    except NoResolvedConfig as exc:
        console.print(f'[red]{exc}[/red]')
        raise typer.Exit(2) from None
    for iid, why in outcome.failed:
        console.print(f'[yellow]  {iid}: {why}[/yellow]')
    if outcome.missing:
        console.print(f"[red]{len(outcome.missing)} instance(s) have no cached base-cell analysis: {', '.join(outcome.missing[:5])}. Nothing written.[/red]")
        raise typer.Exit(1)
    agg = summarise(outcome.rows, outcome.budgets)
    agg['failed'] = [iid for iid, _ in outcome.failed]
    print(json.dumps(agg, indent=2, default=float))
    if wanted is None:
        out = results_dir / FILENAME
        write_rows(outcome.rows, out)
        console.print(f'  wrote {out} ({len(outcome.rows)} rows)')
    if outcome.failed or agg['reproduces_record'] != agg['rows']:
        console.print('[red]some rows do not reproduce their record; see above[/red]')
        raise typer.Exit(1)

def main() -> int:
    app()
    return 0
if __name__ == '__main__':
    sys.exit(main())
