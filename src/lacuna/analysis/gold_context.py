from __future__ import annotations
import random
from collections.abc import Iterable, Sequence
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field
from lacuna.analysis.witness_quality import ContainmentResult, containment, gold_context_elements
from lacuna.corpora.base import Instance
from lacuna.corpora.contextbench import ContextBench, GoldContext
from lacuna.mapper import SourceIndex
MIN_JOINED = 5
BASELINE_DRAWS = 20
BASELINE_SEED = 20260916
BASELINE_POOLS: dict[str, str] = {'edg': 'every EDG node, drawing |closure| elements: the literal support of the closure. Includes nodes no gold range can convert to, so it is the weaker null', 'indexed': "the EDG nodes the source index declares -- the only elements a gold line range can convert to -- drawing |closure n pool| of them: the closure's own count in the region recall is measured over. EDG nodes with no source declaration (implicit constructors, generated benchmark classes, unstaged test sources) can never be gold, so drawing them would only deflate the null. The harder null, and the headline"}
PRIMARY_POOL = 'indexed'

class JoinReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    java_rows: int = Field(ge=0)
    rows_with_gold: int = Field(ge=0)
    by_instance_id: int = Field(ge=0)
    by_repo_and_commit: int = Field(ge=0)
    repo_only_excluded: int = Field(ge=0)
    analysed_and_joined: int = Field(ge=0)

    @property
    def reportable(self) -> bool:
        return self.analysed_and_joined >= MIN_JOINED

def join_by_instance(gold: Iterable[GoldContext], instances: Iterable[str]) -> dict[str, GoldContext]:
    ours = set(instances)
    out: dict[str, GoldContext] = {}
    for row in gold:
        for candidate in (row.instance_id, row.original_instance_id):
            if candidate and candidate in ours:
                out.setdefault(candidate, row)
                break
    return out

def join_report(gold: Sequence[GoldContext], corpus: Iterable[Instance], analysed: Iterable[str]=()) -> JoinReport:
    instances = list(corpus)
    ids = {i.instance_id for i in instances}
    repo_commit = {(i.repo_key.lower(), i.base_commit) for i in instances}
    repos = {i.repo_key.lower() for i in instances}
    joined = join_by_instance(gold, ids)
    analysed_set = set(analysed)
    return JoinReport(java_rows=len(gold), rows_with_gold=sum((1 for r in gold if r.blocks)), by_instance_id=len(joined), by_repo_and_commit=sum((1 for r in gold if (r.repo.lower(), r.base_commit) in repo_commit)), repo_only_excluded=sum((1 for r in gold if r.repo.lower() in repos)), analysed_and_joined=len(set(joined) & analysed_set) if analysed_set else 0)

def load_java(data_dir: Path) -> list[GoldContext]:
    return list(ContextBench(Path(data_dir) / 'corpora' / 'contextbench').java())

def containment_for(instance_id: str, gold: GoldContext, closure: Iterable[str], index: SourceIndex) -> ContainmentResult:
    return containment(instance_id, _stripped(gold), sorted(closure), index)

def _stripped(gold: GoldContext) -> GoldContext:
    from lacuna.experiment.evaluate import strip_source_root
    return gold.model_copy(update={'blocks': tuple((b.model_copy(update={'file': strip_source_root(b.file)}) for b in gold.blocks))})

def gold_elements_for(gold: GoldContext, index: SourceIndex) -> set[str]:
    return gold_context_elements(_stripped(gold), index)[0]

class PoolBaseline(BaseModel):
    model_config = ConfigDict(frozen=True)
    pool_elements: int = Field(ge=0)
    draw_size: int = Field(ge=0)
    gold_in_pool: int = Field(ge=0)
    contained: tuple[int, ...] = ()
    expected_contained: float = Field(default=0.0, ge=0.0)

class InstanceBaseline(BaseModel):
    model_config = ConfigDict(frozen=True)
    instance_id: str
    seed: int
    edg_nodes: int = Field(ge=0)
    closure_elements: int = Field(ge=0)
    closure_share: float = Field(ge=0.0)
    gold_elements: int = Field(ge=0)
    contained: int = Field(ge=0)
    pools: dict[str, PoolBaseline] = {}

def random_baseline(instance_id: str, gold: Iterable[str], closure: Iterable[str], edg_nodes: Iterable[str], indexed: Iterable[str], *, draws: int=BASELINE_DRAWS, seed: int=BASELINE_SEED) -> InstanceBaseline:
    gold_set = set(gold)
    closure_set = set(closure)
    nodes = set(edg_nodes)
    pool_sets = {'edg': nodes, 'indexed': nodes & set(indexed)}
    pools: dict[str, PoolBaseline] = {}
    for name, pool in pool_sets.items():
        ordered = sorted(pool)
        k = len(closure_set & pool)
        in_pool = len(gold_set & pool)
        rng = random.Random(f'{seed}:probe3:{name}:{instance_id}')
        hits = tuple((sum((1 for e in rng.sample(ordered, k) if e in gold_set)) for _ in range(draws)))
        pools[name] = PoolBaseline(pool_elements=len(pool), draw_size=k, gold_in_pool=in_pool, contained=hits, expected_contained=in_pool * k / len(pool) if pool else 0.0)
    return InstanceBaseline(instance_id=instance_id, seed=seed, edg_nodes=len(nodes), closure_elements=len(closure_set), closure_share=len(closure_set) / len(nodes) if nodes else 0.0, gold_elements=len(gold_set), contained=len(gold_set & closure_set), pools=pools)

def _pool_summary(rows: Sequence[InstanceBaseline], name: str, closure_pooled: float) -> dict[str, object]:
    gold_total = sum((r.gold_elements for r in rows))
    pools = [r.pools[name] for r in rows]
    draws = min((len(p.contained) for p in pools), default=0)
    per_draw = [sum((p.contained[d] for p in pools)) / gold_total if gold_total else float('nan') for d in range(draws)]
    pooled = sum(per_draw) / len(per_draw) if per_draw else float('nan')
    per_instance = sorted((sum(p.contained) / len(p.contained) / r.gold_elements for r, p in zip(rows, pools, strict=True) if p.contained and r.gold_elements))
    return {'rule': BASELINE_POOLS[name], 'recall_pooled': pooled, 'recall_pooled_min': min(per_draw) if per_draw else None, 'recall_pooled_max': max(per_draw) if per_draw else None, 'recall_pooled_expected': sum((p.expected_contained for p in pools)) / gold_total if gold_total else float('nan'), 'recall_median': _median(per_instance), 'closure_minus_baseline_pooled': closure_pooled - pooled, 'closure_over_baseline_pooled': closure_pooled / pooled if pooled else None, 'closure_beats_every_draw': sum((1 for r, p in zip(rows, pools, strict=True) if p.contained and r.contained > max(p.contained))), 'closure_below_every_draw': sum((1 for r, p in zip(rows, pools, strict=True) if p.contained and r.contained < min(p.contained))), 'draw_size_median': _median(sorted((float(p.draw_size) for p in pools))), 'pool_elements_median': _median(sorted((float(p.pool_elements) for p in pools)))}

def aggregate_baseline(baselines: Sequence[InstanceBaseline], closure_pooled: float) -> dict[str, object]:
    rows = [b for b in baselines if b.gold_elements]
    seeds = sorted({b.seed for b in rows})
    shares = sorted((b.closure_share for b in rows))
    return {'n': len(rows), 'seed': seeds[0] if len(seeds) == 1 else seeds, 'draws_per_instance': min((len(p.contained) for b in rows for p in b.pools.values()), default=0), 'primary_pool': PRIMARY_POOL, 'closure_recall_pooled': closure_pooled, 'pools': {name: _pool_summary(rows, name, closure_pooled) for name in BASELINE_POOLS}, 'closure_share_median': _median(shares), 'closure_share_min': shares[0] if shares else None, 'closure_share_max': shares[-1] if shares else None, 'edg_nodes_median': _median(sorted((float(b.edg_nodes) for b in rows))), 'per_instance': [{'instance_id': b.instance_id, 'edg_nodes': b.edg_nodes, 'closure_elements': b.closure_elements, 'closure_share': b.closure_share, 'gold_elements': b.gold_elements, 'contained': b.contained, 'recall': b.contained / b.gold_elements, 'pools': {name: {'pool_elements': p.pool_elements, 'draw_size': p.draw_size, 'gold_in_pool': p.gold_in_pool, 'recall_mean': sum(p.contained) / len(p.contained) / b.gold_elements if p.contained else None, 'expected_recall': p.expected_contained / b.gold_elements, 'contained_draws': list(p.contained)} for name, p in b.pools.items()}} for b in sorted(rows, key=lambda r: r.instance_id)]}

def _median(values: list[float]) -> float:
    if not values:
        return float('nan')
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2

def aggregate(results: Sequence[ContainmentResult], join: JoinReport, baselines: Sequence[InstanceBaseline]=()) -> dict[str, object]:
    usable = [r for r in results if r.gold_elements]
    if len(usable) < MIN_JOINED:
        return {'reportable': False, 'n': len(usable), 'min_required': MIN_JOINED, 'reason': f'only {len(usable)} instance(s) carry both an analysed closure and a convertible gold context; a recall figure over that many is not a measurement', 'join': join.model_dump()}
    recalls = sorted((r.recall for r in usable))
    attainable = [r for r in usable if r.closure_elements >= r.gold_elements]
    recalls_att = sorted((r.recall for r in attainable))
    contained = sum((r.contained for r in usable))
    total = sum((r.gold_elements for r in usable))
    mid = len(recalls) // 2
    recall_pooled = contained / total if total else float('nan')
    row: dict[str, object] = {'reportable': True, 'n': len(usable), 'recall_pooled': recall_pooled, 'recall_median': recalls[mid] if len(recalls) % 2 else (recalls[mid - 1] + recalls[mid]) / 2, 'recall_min': recalls[0], 'recall_max': recalls[-1], 'recall_min_attainable': recalls_att[0] if recalls_att else None, 'n_attainable': len(attainable), 'ceiling_excluded': len(usable) - len(attainable), 'ceiling_rule': 'a closure smaller than the gold context cannot contain it; those instances measure the analysed graph, not the approximation', 'gold_elements_total': total, 'contained_total': contained, 'unconvertible_blocks': sum((r.unconvertible_blocks for r in usable)), 'closure_elements_median': _median(sorted((float(r.closure_elements) for r in usable))), 'join': join.model_dump()}
    if baselines:
        ours = {r.instance_id for r in usable}
        theirs = {b.instance_id for b in baselines if b.gold_elements}
        if ours != theirs:
            raise ValueError(f'random baseline covers {len(theirs)} instance(s), the closure {len(ours)}; they must be the same set (differ on {sorted(ours ^ theirs)[:5]})')
        row['random_baseline'] = aggregate_baseline(baselines, recall_pooled)
    return row
