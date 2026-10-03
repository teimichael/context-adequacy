from __future__ import annotations
import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from enum import Enum
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field
InstanceId = str

class Status(str, Enum):
    OK = 'OK'
    OK_GENERATED = 'OK(generated)'
    IMAGE_UNAVAILABLE = 'IMAGE_UNAVAILABLE'
    BUILD_FAILED = 'BUILD_FAILED'
    EXPORT_INCOMPLETE = 'EXPORT_INCOMPLETE'
    PARTIAL_CLASSPATH = 'PARTIAL_CLASSPATH'
    NO_DEBUG_INFO = 'NO_DEBUG_INFO'
    BYTECODE_UNSUPPORTED = 'BYTECODE_UNSUPPORTED'
    NO_ENTRY_POINTS = 'NO_ENTRY_POINTS'
    CG_TIMEOUT = 'CG_TIMEOUT'
    ANALYSIS_OOM = 'ANALYSIS_OOM'
    ANALYSIS_FAILED = 'ANALYSIS_FAILED'
    TARGET_UNMAPPABLE = 'TARGET_UNMAPPABLE'
    NO_JAVA_CHANGE = 'NO_JAVA_CHANGE'
    NO_FAIL_TO_PASS = 'NO_FAIL_TO_PASS'
ANALYSABLE = {Status.OK, Status.OK_GENERATED, Status.PARTIAL_CLASSPATH}

class Instance(BaseModel):
    model_config = ConfigDict(frozen=True)
    instance_id: InstanceId
    corpus: str
    org: str
    repo: str
    base_commit: str
    problem_statement: str
    fix_patch: str
    test_patch: str
    fail_to_pass: tuple[str, ...] = ()
    pass_to_pass: tuple[str, ...] = ()
    image: str | None = None
    build_system: str | None = None

    @property
    def repo_key(self) -> str:
        return f'{self.org}/{self.repo}'

    def changed_java_files(self, *, main_only: bool=True) -> list[str]:
        files = re.findall('^diff --git a/(\\S+) b/', self.fix_patch, re.M)
        out = [f for f in files if f.endswith('.java')]
        if main_only:
            out = [f for f in out if '/test/' not in f and '/tests/' not in f]
        return out

    def is_pure_addition(self) -> bool:
        for part in re.split('^diff --git ', self.fix_patch, flags=re.M)[1:]:
            m = re.match('a/(\\S+) b/', part)
            if not m or not m.group(1).endswith('.java') or '/test/' in m.group(1):
                continue
            body = '\n'.join(part.split('\n')[1:])
            for line in body.split('\n'):
                if line.startswith('-') and (not line.startswith('---')):
                    return False
        return bool(self.changed_java_files())

class CorpusStats(BaseModel):
    total_rows: int = Field(ge=0)
    valid: int = Field(ge=0)
    by_repo: dict[str, int] = {}
    excluded: dict[str, int] = {}

class Corpus(ABC):
    name: str = 'corpus'
    primary: bool = False
    has_images: bool = True

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @abstractmethod
    def download(self) -> None:
        pass

    @abstractmethod
    def iter_raw(self) -> Iterator[dict]:
        pass

    @abstractmethod
    def to_instance(self, raw: dict) -> Instance | None:
        pass

    def instances(self) -> list[Instance]:
        out: list[Instance] = []
        for raw in self.iter_raw():
            inst = self.to_instance(raw)
            if inst is not None:
                out.append(inst)
        return out

    def stats(self) -> CorpusStats:
        total = 0
        valid = 0
        by_repo: dict[str, int] = {}
        excluded: dict[str, int] = {}
        for raw in self.iter_raw():
            total += 1
            inst = self.to_instance(raw)
            if inst is None:
                excluded[Status.NO_FAIL_TO_PASS.value] = excluded.get(Status.NO_FAIL_TO_PASS.value, 0) + 1
                continue
            valid += 1
            by_repo[inst.repo_key] = by_repo.get(inst.repo_key, 0) + 1
        return CorpusStats(total_rows=total, valid=valid, by_repo=dict(sorted(by_repo.items())), excluded=excluded)
