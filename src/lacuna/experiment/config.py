from __future__ import annotations
import os
from pathlib import Path
from typing import ClassVar, Literal
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from lacuna.model.approx import FAST_PATH_IGNORES, Approximation, CallGraphAlgorithm, ControlDependence, DataDependence, EntryPointModel, FrameDefinition, HeapModel, Property

class ApproximationSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str
    cg: CallGraphAlgorithm | None = None
    cha_augmented: bool | None = None
    data_dep: DataDependence | None = None
    control_dep: ControlDependence | None = None
    heap_model: HeapModel | None = None
    field_sens: bool | None = None
    entry_points: EntryPointModel | None = None
    depth_cap: int | None = Field(default=None, ge=0, le=64)
    frame: FrameDefinition | None = None
    realizable: bool | None = None
    summary_edges: bool | None = None
    analysis_timeout_s: int | None = Field(default=None, ge=1)

    def resolve(self, base: Approximation) -> Approximation:
        updates = self.model_dump(exclude={'name'}, exclude_none=True)
        return Approximation.model_validate({**base.model_dump(), **updates})

def host_memory_gb() -> int:
    try:
        return int(os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 1024 ** 3)
    except (ValueError, OSError, AttributeError):
        return 0

class MemoryOverCommit(ValueError):
    pass

class ProfileSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: Literal['dev', 'target'] = 'target'
    cpus_per_analysis: int = Field(default=4, ge=1, le=64)
    parallel_slots: int = Field(default=1, ge=1, le=64)
    memory_per_analysis_gb: int = Field(default=8, ge=1, le=256)
    build_memory_gb: int = Field(default=6, ge=1, le=256)
    reserved_host_gb: int = Field(default=8, ge=0, le=256)
    assume_host_memory_gb: int | None = Field(default=None, ge=1, le=4096)
    analysis_timeout_s: int = Field(default=600, ge=1)
    build_timeout_s: int = Field(default=1800, ge=1)
    max_instances: int | None = Field(default=None, ge=1)
    TARGET_ENVELOPE: ClassVar[dict[str, int]] = {'cpus_per_analysis': 4, 'memory_per_analysis_gb': 8, 'build_memory_gb': 6, 'reserved_host_gb': 8}

    @property
    def peak_gb_per_slot(self) -> int:
        return max(self.memory_per_analysis_gb, self.build_memory_gb)

    @property
    def committed_gb(self) -> int:
        return self.parallel_slots * self.peak_gb_per_slot

    def budget_report(self) -> str:
        total = self.assume_host_memory_gb or host_memory_gb()
        usable = max(0, total - self.reserved_host_gb)
        return f'{self.parallel_slots} slots x {self.peak_gb_per_slot} GB = {self.committed_gb} GB committed; host {total} GB - {self.reserved_host_gb} GB reserved = {usable} GB usable'

    def _memory_budget_fits(self) -> ProfileSpec:
        total = self.assume_host_memory_gb or host_memory_gb()
        if total <= 0:
            return self
        usable = total - self.reserved_host_gb
        if self.committed_gb > usable:
            raise MemoryOverCommit(f'memory budget does not fit: {self.budget_report()}.\nReduce parallel_slots to {max(1, usable // self.peak_gb_per_slot)}, or lower memory_per_analysis_gb/build_memory_gb.')
        return self

    @model_validator(mode='after')
    def _envelope(self) -> ProfileSpec:
        if self.name != 'target':
            return self
        wrong = {k: (getattr(self, k), v) for k, v in self.TARGET_ENVELOPE.items() if getattr(self, k) != v}
        if wrong:
            detail = ', '.join((f'{k}={got} (envelope {want})' for k, (got, want) in sorted(wrong.items())))
            raise ValueError(f"profile 'target' is the envelope every reported number must come from (Sec. 7.5), and this one departs from it: {detail}. Use name: dev for an unreportable run, or state the change in the manuscript rather than silently measuring on a different machine.")
        return self

class InertApproximationCell(ValueError):
    pass

class ExperimentConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str
    description: str = ''
    seed: int = 20260916
    corpora: list[str] = Field(default_factory=lambda: ['multi-swe-bench'])
    instances: list[str] | None = None
    per_repo: int | None = Field(default=None, ge=1)
    properties: list[Property] = Field(default_factory=lambda: [Property.P1_CALLER_BEHAVIOUR])
    budgets: list[int] = Field(default_factory=lambda: [8000, 32000, 128000])
    builders: list[str] = Field(default_factory=lambda: ['target-only', 'lexical-bm25', 'closure-first'])
    approximations: list[ApproximationSpec] = Field(default_factory=lambda: [ApproximationSpec(name='base')])
    profile: ProfileSpec = Field(default_factory=ProfileSpec)
    data_dir: Path = Path('data')
    results_dir: Path = Path('outputs/fresh')
    reference_context: Literal['none', 'target-file'] = 'target-file'
    measure_conformance: bool = False
    crosscheck_k: int = Field(default=0, ge=0)

    @model_validator(mode='after')
    def _validate(self) -> ExperimentConfig:
        from lacuna.builders import BUILDERS
        from lacuna.corpora import CORPORA
        unknown_corpora = [c for c in self.corpora if c not in CORPORA]
        if unknown_corpora:
            raise ValueError(f'unknown corpora {unknown_corpora}; known: {sorted(CORPORA)}')
        unknown_builders = [b for b in self.builders if b not in BUILDERS]
        if unknown_builders:
            raise ValueError(f'unknown builders {unknown_builders}; known: {sorted(BUILDERS)}')
        if not self.corpora:
            raise ValueError('at least one corpus is required')
        imageless = [c for c in self.corpora if not CORPORA[c].has_images]
        if imageless:
            raise ValueError(f'corpora {imageless} ship no container image, so every instance would fail to build and land in IMAGE_UNAVAILABLE -- 100% attrition reported as a funnel. Build images for them first, or drop them from `corpora`. ')
        if self.instances is not None and self.per_repo is not None:
            raise ValueError('`instances` and `per_repo` are mutually exclusive: an explicit instance list and a per-repository sample cannot both define the sample')
        names = [a.name for a in self.approximations]
        if len(names) != len(set(names)):
            raise ValueError(f'approximation names must be unique, got {names}')
        resolved = self.resolved_approximations()
        if resolved:
            base_name, base = resolved[0]
            base_fields = base.model_dump(mode='json')
            for name, approx in resolved[1:]:
                fields = approx.model_dump(mode='json')
                differing = {k for k, v in fields.items() if base_fields.get(k) != v}
                if differing and differing <= FAST_PATH_IGNORES:
                    raise InertApproximationCell(f"approximation {name!r} differs from {base_name!r} only in {sorted(differing)}, which the FAST analyser path does not read, so it would produce an EDG identical to the base cell's and report a guaranteed 0% flip rate. Vary a field that changes the analysis (cg, cha_augmented, heap_model, field_sens, entry_points) or the criterion (depth_cap, frame, realizable, summary_edges), or drop the cell. The criterion-side fields share one cached analysis with the base cell and so cost nothing to sweep (D72).")
        return self

    @classmethod
    def load(cls, path: Path) -> ExperimentConfig:
        raw = yaml.safe_load(Path(path).read_text())
        if not isinstance(raw, dict):
            raise ValueError(f'{path}: expected a YAML mapping at the top level')
        return cls.model_validate(raw)

    def resolved_approximations(self) -> list[tuple[str, Approximation]]:
        base = Approximation(analysis_timeout_s=self.profile.analysis_timeout_s)
        return [(spec.name, spec.resolve(base)) for spec in self.approximations]
