from __future__ import annotations
import os
import random
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from pydantic import BaseModel, ConfigDict, ValidationError
from lacuna.corpora import CORPORA
from lacuna.corpora.base import ANALYSABLE, Instance, Status
from lacuna.experiment.config import ExperimentConfig
from lacuna.experiment.evaluate import InstanceRecord, evaluate_instance, load_artifacts
from lacuna.experiment.funnel import Funnel, check_partition
from lacuna.harness.analyze import analyse_instance
from lacuna.harness.prepare import Paths, prepare
from lacuna.harness.resources import RunLock, preflight
from lacuna.model.approx import Approximation

@dataclass
class RunOutcome:
    funnel: Funnel
    records: list[InstanceRecord]
    seconds: float
    ladder_failures: list[LadderFailure] = field(default_factory=list)

def select_instances(config: ExperimentConfig) -> list[Instance]:
    all_instances: list[Instance] = []
    for name in config.corpora:
        corpus = CORPORA[name](config.data_dir / 'corpora' / name)
        all_instances.extend(corpus.instances())
    if config.instances is not None:
        wanted = set(config.instances)
        chosen = [i for i in all_instances if i.instance_id in wanted]
        missing = wanted - {i.instance_id for i in chosen}
        if missing:
            raise ValueError(f'configured instances not found in any corpus: {sorted(missing)}')
    elif config.per_repo is not None:
        rng = random.Random(config.seed)
        by_repo: dict[str, list[Instance]] = {}
        for inst in all_instances:
            by_repo.setdefault(f'{inst.corpus}:{inst.repo_key}', []).append(inst)
        chosen = []
        for key in sorted(by_repo):
            pool = sorted(by_repo[key], key=lambda i: i.instance_id)
            rng.shuffle(pool)
            chosen.extend(pool[:config.per_repo])
    else:
        chosen = list(all_instances)
    rng = random.Random(config.seed)
    chosen.sort(key=lambda i: i.instance_id)
    rng.shuffle(chosen)
    if config.profile.max_instances is not None:
        chosen = chosen[:config.profile.max_instances]
    return chosen

def run_one(instance: Instance, approx: Approximation, config: ExperimentConfig, approx_name: str) -> tuple[Status, str, list[InstanceRecord], float]:
    t0 = time.time()
    prepared = config.data_dir / 'instances' / instance.instance_id
    try:
        manifest = prepare(instance, prepared, cpus=config.profile.cpus_per_analysis, memory_gb=config.profile.build_memory_gb, build_timeout=config.profile.build_timeout_s)
    except Exception as exc:
        return (Status.IMAGE_UNAVAILABLE, f'{type(exc).__name__}: {exc}', [], time.time() - t0)
    if manifest.status is not Status.OK:
        return (manifest.status, manifest.note, [], time.time() - t0)
    analysis = analyse_instance(instance.instance_id, instance.base_commit, prepared, manifest, approx, config.data_dir / 'analysis', cpus=config.profile.cpus_per_analysis, memory_gb=config.profile.memory_per_analysis_gb, timeout=config.profile.analysis_timeout_s + 120)
    if analysis.status not in ANALYSABLE or analysis.edg_path is None:
        return (analysis.status, analysis.stderr_tail[-300:], [], time.time() - t0)
    graph, program_index, source_index, matched, unmatched = load_artifacts(analysis.edg_path, analysis.index_path, [Paths(prepared).sources])
    records: list[InstanceRecord] = []
    status = analysis.status
    note = f'mapping {matched}/{matched + unmatched}; approximation={approx_name}'
    for prop in config.properties:
        record, _, _, _ = evaluate_instance(instance, graph, source_index, program_index, approx, prop=prop, context=(), escapes_fired=analysis.escapes_fired, element_reasons=analysis.element_reasons, reference_context_kind=config.reference_context, bridges_truncated=int((analysis.stats or {}).get('bridges_truncated', 0)), shadowing_jars_dropped=analysis.shadowing_jars_dropped, escapes_magnitude=analysis.escapes_magnitude)
        records.append(record)
        if record.status is not Status.OK:
            status = record.status
            note = record.note
    return (status, note, records, time.time() - t0)

class _IncrementalWriter:

    def __init__(self, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        self.records_path = out_dir / 'records.jsonl'
        self.funnel_path = out_dir / 'funnel.jsonl'
        self.records_path.write_text('')
        self.funnel_path.write_text('')
        self._lock = Lock()

    def add_records(self, records: Sequence[InstanceRecord]) -> None:
        if not records:
            return
        with self._lock, self.records_path.open('a') as fh:
            for r in records:
                fh.write(r.model_dump_json() + '\n')

    def add_funnel(self, entry) -> None:
        with self._lock, self.funnel_path.open('a') as fh:
            fh.write(entry.model_dump_json() + '\n')

def run(config: ExperimentConfig, *, progress: bool=True, out_dir: Path | None=None) -> RunOutcome:
    config.profile._memory_budget_fits()
    t_start = time.time()
    lock = RunLock(config.data_dir, owner=config.name).acquire()
    try:
        return _run_locked(config, progress=progress, out_dir=out_dir, t_start=t_start)
    finally:
        lock.release()

def _run_locked(config: ExperimentConfig, *, progress: bool, out_dir: Path | None, t_start: float) -> RunOutcome:
    require_image = 'LACUNA_ANALYZER_CP' not in os.environ
    state = preflight(required_memory_gb=config.profile.committed_gb, require_image=require_image)
    if progress:
        print('host preflight:', flush=True)
        print(state.render(), flush=True)
        print(f'memory budget: {config.profile.budget_report()}', flush=True)
    instances = select_instances(config)
    funnel = Funnel()
    records: list[InstanceRecord] = []
    writer = _IncrementalWriter(out_dir) if out_dir is not None else None
    approximations = config.resolved_approximations()
    primary_name, primary_approx = approximations[0]

    def work(inst: Instance):
        return (inst, run_one(inst, primary_approx, config, primary_name))
    done = 0
    with ThreadPoolExecutor(max_workers=config.profile.parallel_slots) as pool:
        futures = [pool.submit(work, inst) for inst in instances]
        for fut in as_completed(futures):
            inst, (status, note, recs, seconds) = fut.result()
            funnel.add(inst, status, note, seconds)
            records.extend(recs)
            if writer is not None:
                writer.add_funnel(funnel.entries[-1])
                writer.add_records(recs)
            done += 1
            if progress:
                print(f'[{done:3d}/{len(instances)}] {inst.instance_id:45} {status.value:20} {seconds:6.1f}s', flush=True)
    survivors = {e.instance_id for e in funnel.analysable()}
    ladder_failures: list[LadderFailure] = []
    ladder = [(inst, name, approx) for name, approx in approximations[1:] for inst in instances if inst.instance_id in survivors]
    if ladder:
        with ThreadPoolExecutor(max_workers=config.profile.parallel_slots) as pool:
            futures = {pool.submit(run_one, inst, approx, config, name): (inst, name) for inst, name, approx in ladder}
            for n, fut in enumerate(as_completed(futures), 1):
                inst, name = futures[fut]
                status, note, recs, _ = fut.result()
                records.extend(recs)
                if writer is not None:
                    writer.add_records(recs)
                if not recs:
                    ladder_failures.append(LadderFailure(instance_id=inst.instance_id, cell=name, status=status, note=note[:300]))
                if progress:
                    flag = '' if recs else f'  <- NO RECORDS ({status.value})'
                    print(f'[ladder {n:3d}/{len(ladder)}] {name:16} {inst.instance_id}{flag}', flush=True)
    if config.crosscheck_k:
        import random as _random
        from lacuna.analysis.crosscheck import run_for_instance
        out = (out_dir or config.results_dir / config.name) / 'crosscheck.jsonl'
        out.parent.mkdir(parents=True, exist_ok=True)
        eligible = sorted((i for i in survivors))
        sample = _random.Random(config.seed).sample(eligible, min(config.crosscheck_k, len(eligible)))
        by_id = {i.instance_id: i for i in instances}
        with out.open('w') as fh:
            for n, iid in enumerate(sample, 1):
                inst = by_id[iid]
                prepared = config.data_dir / 'instances' / iid
                try:
                    manifest = prepare(inst, prepared, cpus=config.profile.cpus_per_analysis, memory_gb=config.profile.build_memory_gb, build_timeout=config.profile.build_timeout_s)
                    res = run_for_instance(inst, prepared, manifest, approximations[0][1], config.data_dir / 'analysis', cpus=config.profile.cpus_per_analysis, memory_gb=config.profile.memory_per_analysis_gb)
                except Exception as exc:
                    if progress:
                        print(f'[crosscheck] {iid}: {type(exc).__name__}: {exc}', flush=True)
                    continue
                if res is None:
                    continue
                fh.write(res.model_dump_json() + '\n')
                if progress:
                    print(f'[crosscheck {n}/{len(sample)}] {res.summary_line()}', flush=True)
    if config.measure_conformance:
        from lacuna.experiment.conformance import measure
        out = (out_dir or config.results_dir / config.name) / 'conformance.jsonl'
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open('w') as fh:
            for n, inst in enumerate([i for i in instances if i.instance_id in survivors], 1):
                try:
                    rec = measure(inst, approximations[0][1], config.data_dir, cpus=config.profile.cpus_per_analysis)
                except Exception as exc:
                    if progress:
                        print(f'[conformance] {inst.instance_id}: {type(exc).__name__}: {exc}', flush=True)
                    continue
                fh.write(rec.model_dump_json() + '\n')
                if progress:
                    print(f'[conformance {n:3d}] {inst.instance_id}', flush=True)
    check_partition(funnel, instances)
    return RunOutcome(funnel=funnel, records=records, seconds=time.time() - t_start, ladder_failures=ladder_failures)

def write_records(records: Sequence[InstanceRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w') as fh:
        for r in records:
            fh.write(r.model_dump_json() + '\n')

class LadderFailure(BaseModel):
    model_config = ConfigDict(frozen=True)
    instance_id: str
    cell: str
    status: Status
    note: str = ''

class StaleRecordSchema(ValueError):
    pass

def read_records(path: Path) -> list[InstanceRecord]:
    from lacuna import SCHEMA_VERSION
    out: list[InstanceRecord] = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(InstanceRecord.model_validate_json(line))
        except ValidationError as exc:
            missing = ['.'.join((str(x) for x in e['loc'])) for e in exc.errors() if e['type'] == 'missing']
            raise StaleRecordSchema(f'{path}:{n} does not match record schema {SCHEMA_VERSION!r}' + (f'; missing {missing}' if missing else '') + ". Re-run the experiment: the analyses are cached, so only the criterion is recomputed. Do not report from these records -- a missing field renders as a zero, and a zero closure reads as 'within every budget'.") from exc
    return out

def summarise(records: Iterable[InstanceRecord]) -> dict[str, object]:
    ok = [r for r in records if r.status is Status.OK]
    return {'records': len(list(records)) if not isinstance(records, list) else len(records), 'ok': len(ok), 'verdicts': {v: sum((1 for r in ok if r.verdict is not None and r.verdict.value == v)) for v in sorted({r.verdict.value for r in ok if r.verdict is not None})}}
ANALYSABLE_STATUSES = ANALYSABLE
