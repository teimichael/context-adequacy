from __future__ import annotations
import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field
from lacuna.corpora.base import ANALYSABLE, Instance, Status
STAGES: tuple[tuple[str, tuple[Status, ...]], ...] = (('candidate instances', ()), ('image available', (Status.IMAGE_UNAVAILABLE,)), ('builds offline', (Status.BUILD_FAILED, Status.EXPORT_INCOMPLETE)), ('bytecode has debug info', (Status.NO_DEBUG_INFO, Status.BYTECODE_UNSUPPORTED)), ('call graph completes', (Status.CG_TIMEOUT, Status.ANALYSIS_OOM, Status.ANALYSIS_FAILED, Status.NO_ENTRY_POINTS)), ('edit target maps to an element', (Status.TARGET_UNMAPPABLE, Status.NO_JAVA_CHANGE)), ('analysed', ()))
SURVIVING: frozenset[Status] = frozenset(ANALYSABLE)
_excluded = {st for _, sts in STAGES for st in sts}
assert _excluded | SURVIVING | {Status.NO_FAIL_TO_PASS} == frozenset(Status), f'every Status must either exclude at a STAGES row, be ANALYSABLE, or be a corpus-level filter; unplaced: {frozenset(Status) - _excluded - SURVIVING}'
del _excluded

class FunnelEntry(BaseModel):
    model_config = ConfigDict(frozen=True)
    instance_id: str
    corpus: str
    repo: str
    status: Status
    note: str = ''
    build_system: str | None = None
    seconds: float = 0.0

class Funnel(BaseModel):
    entries: list[FunnelEntry] = Field(default_factory=list)

    def add(self, instance: Instance, status: Status, note: str='', seconds: float=0.0) -> None:
        self.entries.append(FunnelEntry(instance_id=instance.instance_id, corpus=instance.corpus, repo=instance.repo_key, status=status, note=note[:400], build_system=instance.build_system, seconds=round(seconds, 1)))

    def by_status(self) -> dict[str, int]:
        return dict(sorted(Counter((e.status.value for e in self.entries)).items()))

    def by_repo(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for e in self.entries:
            out[e.repo][e.status.value] += 1
        return {k: dict(sorted(v.items())) for k, v in sorted(out.items())}

    def analysable(self) -> list[FunnelEntry]:
        return [e for e in self.entries if e.status in ANALYSABLE]

    def stage_counts(self) -> list[tuple[str, int, int]]:
        lost_by_status = Counter((e.status.value for e in self.entries))
        total = len(self.entries)
        out: list[tuple[str, int, int]] = []
        surviving = total
        for label, statuses in STAGES:
            lost = sum((lost_by_status.get(s.value, 0) for s in statuses))
            surviving -= lost
            out.append((label, surviving, lost))
        return out

    def render(self) -> str:
        lines = [f"{'stage':34} {'surviving':>10} {'lost here':>10}", '-' * 56]
        for label, surviving, lost in self.stage_counts():
            lines.append(f'{label:34} {surviving:10d} {lost:10d}')
        lines.append('')
        lines.append('per repository:')
        for repo, counts in self.by_repo().items():
            ok = sum((v for k, v in counts.items() if k in {s.value for s in ANALYSABLE}))
            lines.append(f'  {repo:40} {ok:3d}/{sum(counts.values()):3d}  {counts}')
        return '\n'.join(lines)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('w') as fh:
            for e in self.entries:
                fh.write(e.model_dump_json() + '\n')

    @classmethod
    def read(cls, path: Path) -> Funnel:
        entries = [FunnelEntry.model_validate_json(line) for line in path.read_text().splitlines() if line.strip()]
        return cls(entries=entries)

    def to_json(self) -> str:
        return json.dumps({'total': len(self.entries), 'analysable': len(self.analysable()), 'by_status': self.by_status(), 'by_repo': self.by_repo(), 'stages': [{'stage': s, 'surviving': a, 'lost': b} for s, a, b in self.stage_counts()]}, indent=2)

def check_partition(funnel: Funnel, candidates: Iterable[Instance]) -> None:
    seen = Counter((e.instance_id for e in funnel.entries))
    expected = {i.instance_id for i in candidates}
    duplicated = sorted((k for k, v in seen.items() if v > 1))
    missing = sorted(expected - set(seen))
    extra = sorted(set(seen) - expected)
    problems = []
    if duplicated:
        problems.append(f'duplicated: {duplicated[:5]}')
    if missing:
        problems.append(f'missing: {missing[:5]}')
    if extra:
        problems.append(f'not a candidate: {extra[:5]}')
    if problems:
        raise ValueError('funnel does not partition the candidate set -- ' + '; '.join(problems))
