from __future__ import annotations
import json
import pathlib
import sys
import time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
from lacuna.corpora import CORPORA
from lacuna.corpora.base import ANALYSABLE
from lacuna.criterion.closure import bfs
from lacuna.criterion.frame import frame
from lacuna.criterion.properties import PROPERTY_KINDS, forward_set
from lacuna.experiment.evaluate import _sources_for, load_artifacts, resolve_oracle_target
from lacuna.harness.prepare import Paths
from lacuna.model.approx import DEFAULT_APPROXIMATION as A
from lacuna.model.approx import Property
from lacuna.model.identity import EdgeKind, ElementId
from lacuna.tokens import missing_sources, tok
ANALYSABLE_NAMES = {st.value for st in ANALYSABLE}
DATA = pathlib.Path('data')
HORIZONS: tuple[int | None, ...] = (1, 2, None)
BUDGETS = (8000, 32000, 128000)
DECLARED_MARKERS = ('final ', 'private ', 'sealed ', '@NonNull', '@Nonnull', '@NotNull', '@Nullable', '@CheckForNull', '@Immutable', '@Value.Immutable')
UPSTREAM_KINDS = frozenset({EdgeKind.CALL, EdgeKind.OVERRIDE, EdgeKind.HEAP_WRITE})
DOWNSTREAM_KINDS = frozenset(EdgeKind) - frozenset({EdgeKind.CALL})

def bounded_callers(graph, target: str, max_hops: int | None) -> set[str]:
    seen = {target}
    frontier = [target]
    hops = 0
    while frontier and (max_hops is None or hops < max_hops):
        nxt: list[str] = []
        for node in frontier:
            for pred, ks in graph.predecessors(node).items():
                if (EdgeKind.RETURN in ks or EdgeKind.OVERRIDE in ks) and pred not in seen:
                    seen.add(pred)
                    nxt.append(pred)
            for succ, ks in graph.successors(node).items():
                if EdgeKind.CALL in ks and succ not in seen:
                    seen.add(succ)
                    nxt.append(succ)
        frontier = sorted(nxt, key=ElementId)
        hops += 1
    seen.discard(target)
    return seen

def signature_of(text: str) -> str:
    depth = 0
    for i, ch in enumerate(text):
        if ch in '([':
            depth += 1
        elif ch in ')]':
            depth -= 1
        elif ch == '{' and depth == 0:
            return text[:i].rstrip() + ';'
    return text

def base_analysis(instance) -> tuple[pathlib.Path | None, str]:
    from lacuna.harness.analyze import NoCachedAnalysis, resolve_cached_analysis
    prepared = DATA / 'instances' / instance.instance_id
    try:
        d = resolve_cached_analysis(instance.instance_id, instance.base_commit, prepared, A, DATA / 'analysis')
    except (NoCachedAnalysis, OSError):
        return (None, '')
    return (d, '')

def main() -> int:
    out_dir = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else 'outputs/fresh/full/report')
    global DATA, A
    from lacuna.experiment.config import ExperimentConfig
    cfg = ExperimentConfig.load(pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else 'configs/full.yaml'))
    DATA = cfg.data_dir
    A = cfg.resolved_approximations()[0][1]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / 'levers.json'
    instances = {i.instance_id: i for i in CORPORA['multi-swe-bench'](DATA / 'corpora' / 'multi-swe-bench').instances()}
    targets = sorted((p.name for p in (DATA / 'instances').iterdir() if p.is_dir()))
    kinds = PROPERTY_KINDS[Property.P1_CALLER_BEHAVIOUR]
    records: list[dict] = []
    for n, iid in enumerate(targets, 1):
        inst = instances.get(iid)
        if inst is None:
            continue
        d, stamp = base_analysis(inst)
        if d is None:
            print(f'[{n}/{len(targets)}] {iid}: no cached base-cell analysis, skipped', flush=True)
            continue
        t0 = time.time()
        graph, pidx, sidx, _, _ = load_artifacts(d / 'edg.jsonl', d / 'index.json', [Paths(DATA / 'instances' / iid).sources])
        res = resolve_oracle_target(inst, sidx, graph)
        if res.target is None:
            print(f'[{n}/{len(targets)}] {iid}: target unmappable, skipped', flush=True)
            continue
        fr = frame(res.target, pidx, A.frame)
        fwd = forward_set(graph, res.target)
        nodes = set(graph.nodes())
        rec: dict = {'instance_id': iid, 'repo': inst.repo_key, 'target': res.target, 'frame_elements': len(fr), 'forward_size': len(fwd), 'horizons': {}}
        for j in HORIZONS:
            crit = bounded_callers(graph, res.target, j)
            obs = crit & fwd
            seed = frozenset(fr) & nodes | obs | {res.target}
            cl = bfs(graph, seed, max_distance=None, kinds=kinds).reached()
            src_body = _sources_for(sidx, cl | seed)
            src_sig, declared = (dict(src_body), 0)
            for eid, text in src_body.items():
                head = text[:400]
                if any((m in head for m in DECLARED_MARKERS)):
                    sig = signature_of(text)
                    if len(sig) < len(text):
                        src_sig[eid] = sig
                        declared += 1
            key = 'inf' if j is None else str(j)
            rec['horizons'][key] = {'crit': len(crit), 'observation_points': len(obs), 'seed': len(seed), 'closure_elements': len(cl), 'closure_tokens': tok(cl, src_body), 'closure_tokens_signature': tok(cl, src_sig), 'seed_tokens': tok(seed, src_body), 'unrenderable': len(missing_sources(cl, src_body)), 'declared_elements': declared}
            if j is None:
                up = bfs(graph, seed, kinds=frozenset(kinds & UPSTREAM_KINDS)).reached()
                dn = bfs(graph, seed, kinds=frozenset(kinds & DOWNSTREAM_KINDS)).reached()
                rec['directed'] = {'upstream_elements': len(up), 'upstream_tokens': tok(up, src_body), 'downstream_elements': len(dn), 'downstream_tokens': tok(dn, src_body)}
        rec['seconds'] = round(time.time() - t0, 1)
        records.append(rec)
        out_file.write_text(json.dumps(records, indent=1))
        h = rec['horizons']
        print(f"[{n}/{len(targets)}] {iid:40s} P1^1={h['1']['closure_tokens']:>9,}  P1^2={h['2']['closure_tokens']:>9,}  P1^inf={h['inf']['closure_tokens']:>9,}  ({rec['seconds']}s)", flush=True)
    out_file.write_text(json.dumps(records, indent=1))
    print(f'\nwrote {out_file}  ({len(records)} instances)')
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
