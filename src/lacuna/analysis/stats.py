from __future__ import annotations
import math
from collections.abc import Sequence
import numpy as np
from pydantic import BaseModel, ConfigDict

class Interval(BaseModel):
    model_config = ConfigDict(frozen=True)
    point: float
    low: float
    high: float
    method: str = ''

    def render(self, pct: bool=False, digits: int=1) -> str:
        scale = 100.0 if pct else 1.0
        suffix = '%' if pct else ''
        return f'{self.point * scale:.{digits}f}{suffix} [{self.low * scale:.{digits}f}, {self.high * scale:.{digits}f}]'

def cluster_bootstrap(values: Sequence[float], clusters: Sequence[str], statistic=np.median, *, resamples: int=10000, alpha: float=0.05, seed: int=20260916) -> Interval:
    if len(values) != len(clusters):
        raise ValueError('values and clusters must be the same length')
    if not values:
        return Interval(point=float('nan'), low=float('nan'), high=float('nan'), method='cluster-bootstrap')
    arr = np.asarray(values, dtype=float)
    groups: dict[str, list[int]] = {}
    for i, c in enumerate(clusters):
        groups.setdefault(c, []).append(i)
    keys = sorted(groups)
    rng = np.random.default_rng(seed)
    point = float(statistic(arr))
    if len(keys) < 2:
        return Interval(point=point, low=float('nan'), high=float('nan'), method='cluster-bootstrap(<2 clusters)')
    draws = np.empty(resamples, dtype=float)
    idx_by_key = [np.asarray(groups[k]) for k in keys]
    for b in range(resamples):
        picked = rng.integers(0, len(keys), size=len(keys))
        sample = np.concatenate([idx_by_key[p] for p in picked])
        draws[b] = statistic(arr[sample])
    low, high = np.quantile(draws, [alpha / 2, 1 - alpha / 2])
    return Interval(point=point, low=float(low), high=float(high), method='cluster-bootstrap')

def describe(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {'n': 0, 'median': float('nan'), 'iqr': float('nan'), 'p90': float('nan')}
    arr = np.asarray(values, dtype=float)
    q1, q3 = np.quantile(arr, [0.25, 0.75])
    return {'n': float(len(arr)), 'median': float(np.median(arr)), 'iqr': float(q3 - q1), 'p90': float(np.quantile(arr, 0.9)), 'mean': float(arr.mean()), 'max': float(arr.max())}

def fraction_within(values: Sequence[float], budget: float) -> float:
    if not values:
        return float('nan')
    return float(np.mean(np.asarray(values, dtype=float) <= budget))
