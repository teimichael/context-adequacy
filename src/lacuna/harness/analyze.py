from __future__ import annotations
import functools
import hashlib
import json
import os
import shutil
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field
from lacuna.corpora.base import ANALYSABLE, Status
from lacuna.harness import classpath as cp_select
from lacuna.harness.prepare import ClasspathManifest, Paths
from lacuna.harness.resources import owner_label_args
from lacuna.model.approx import Approximation
ANALYZER_IMAGE = 'lacuna-analyzer:1.6.10'

class AnalyzerError(RuntimeError):
    pass

class AnalysisResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    instance_id: str
    approximation_digest: str
    status: Status
    edg_path: Path | None = None
    index_path: Path | None = None
    escapes_path: Path | None = None
    stats: dict = {}
    shadowing_jars_dropped: list[str] = []
    escapes_fired: dict[str, int] = {}
    escapes_magnitude: dict[str, int] = {}
    element_reasons: dict[str, list[str]] = {}
    seconds: float = Field(default=0.0, ge=0.0)
    stderr_tail: str = ''
EXIT_STATUS: dict[int, Status] = {0: Status.OK, 2: Status.ANALYSIS_FAILED, 3: Status.NO_ENTRY_POINTS, 4: Status.CG_TIMEOUT, 5: Status.ANALYSIS_OOM, 6: Status.ANALYSIS_FAILED}
FAST_PATH_SOURCES: tuple[str, ...] = ('EdgBuilder.java', 'ScopeBuilder.java', 'ElementIds.java', 'Escapes.java', 'Main.java', 'Config.java', 'IndexWriter.java', 'JsonWriter.java')

@functools.lru_cache(maxsize=1)
def fast_path_digest() -> str | None:
    root = Path(__file__).resolve().parents[3] / 'java' / 'analyzer' / 'src' / 'main' / 'java' / 'lacuna' / 'analyzer'
    if not root.is_dir():
        return None
    h = hashlib.sha256()
    for name in sorted(FAST_PATH_SOURCES):
        f = root / name
        if not f.exists():
            return None
        h.update(name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]

@functools.lru_cache(maxsize=1)
def analyzer_identity() -> str:
    if not os.environ.get('LACUNA_ANALYZER_FORCE_IMAGE_ID'):
        fast = fast_path_digest()
        if fast is not None:
            return f'fast:{fast}'
    cp = os.environ.get('LACUNA_ANALYZER_CP')
    if cp:
        newest = 0.0
        for entry in cp.split(os.pathsep):
            path = Path(entry.rstrip('*'))
            if path.is_dir():
                for f in path.rglob('*.class'):
                    newest = max(newest, f.stat().st_mtime)
            elif path.exists():
                newest = max(newest, path.stat().st_mtime)
        return f'local:{newest:.0f}'
    res = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', ANALYZER_IMAGE], capture_output=True, text=True, check=False)
    return res.stdout.strip() or 'unknown-analyzer'
DEFAULT_MODE = 'FAST'

def cache_key(instance_id: str, commit: str, approx: Approximation, manifest_digest: str, mode: str=DEFAULT_MODE, seeds: Sequence[str]=()) -> str:
    parts = [instance_id, commit, approx.analyser_digest(), manifest_digest, analyzer_identity()]
    if mode != DEFAULT_MODE:
        parts.append(mode)
    if seeds:
        parts.append(','.join(sorted(seeds)))
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()[:24]

def jvm_flags(memory_gb: int, cpus: int) -> list[str]:
    return [f'-Xmx{memory_gb}g', f'-XX:ActiveProcessorCount={cpus}', '-XX:+UseSerialGC', '-XX:+ExitOnOutOfMemoryError']

def _local_runner(memory_gb: int, cpus: int) -> list[str] | None:
    cp = os.environ.get('LACUNA_ANALYZER_CP')
    java = os.environ.get('LACUNA_JAVA', 'java')
    if not cp:
        return None
    return [java, *jvm_flags(memory_gb, cpus), '-cp', cp, 'lacuna.analyzer.Main']

def _run_in_container(paths: Paths, dest: Path, libs: str, approx: Approximation, *, cpus: int, memory_gb: int, timeout: int, mode: str=DEFAULT_MODE, seeds: Sequence[str]=()) -> tuple[int, str]:
    work = '/work/in'
    out = '/work/out'
    inner_args = ['--app-classes', f'{work}/classes', '--test-classes', f'{work}/test-classes', '--lib-jars', ':'.join((f'{work}/libs/{Path(j).name}' for j in libs.split(':') if j)), '--source-roots', f'{work}/sources', '--entry-points', approx.entry_points.value, '--cg', approx.cg.value, '--cha-augmented', str(approx.cha_augmented).lower(), '--field-sens', str(approx.field_sens).lower(), '--heap-model', approx.heap_model.value, '--data-dep', approx.data_dep.value, '--control-dep', approx.control_dep.value, '--depth-cap', str(approx.depth_cap), '--timeout-seconds', str(approx.analysis_timeout_s), '--mode', mode, *(['--seeds', ','.join(seeds)] if seeds else []), '--out', f'{out}/edg.jsonl', '--index-out', f'{out}/index.json', '--escapes-out', f'{out}/escapes.json']
    name = 'lacuna-an-' + hashlib.sha256(str(dest).encode()).hexdigest()[:16]
    subprocess.run(['docker', 'rm', '-f', name], capture_output=True, check=False)
    create = subprocess.run(['docker', 'create', '--name', name, *owner_label_args(), f'--cpus={cpus}', f'--memory={memory_gb}g', f'--memory-swap={memory_gb}g', '-e', f"JAVA_TOOL_OPTIONS={' '.join(jvm_flags(memory_gb, cpus))}", '--network', 'none', ANALYZER_IMAGE, *inner_args], capture_output=True, text=True, check=False)
    if create.returncode != 0:
        return (6, f'docker create failed: {create.stderr[-500:]}')
    try:
        subprocess.run(['docker', 'exec', name, 'true'], capture_output=True, check=False)
        for src, dst in [(paths.classes, f'{name}:{work}/classes'), (paths.test_classes, f'{name}:{work}/test-classes'), (paths.libs, f'{name}:{work}/libs'), (paths.sources, f'{name}:{work}/sources')]:
            cp = subprocess.run(['docker', 'cp', f'{src}/.', dst], capture_output=True, text=True, check=False)
            if cp.returncode != 0:
                return (6, f'docker cp {src} failed: {cp.stderr[-400:]}')
        started = subprocess.run(['docker', 'start', '-a', name], capture_output=True, text=True, timeout=timeout, check=False)
        code, stderr = (started.returncode, (started.stderr or '')[-1500:])
        for fname in ('edg.jsonl', 'index.json', 'escapes.json'):
            subprocess.run(['docker', 'cp', f'{name}:{out}/{fname}', str(dest / fname)], capture_output=True, check=False)
        return (code, stderr)
    except subprocess.TimeoutExpired:
        return (4, f'analyser exceeded {timeout}s')
    finally:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, check=False)

def analyse_instance(instance_id: str, commit: str, prepared: Path, manifest: ClasspathManifest, approx: Approximation, out_dir: Path, *, cpus: int=4, memory_gb: int=8, timeout: int=1800, force: bool=False, mode: str=DEFAULT_MODE, seeds: Sequence[str]=()) -> AnalysisResult:
    paths = Paths(prepared)
    if mode != DEFAULT_MODE and (not seeds):
        raise ValueError(f'analyser mode {mode!r} runs the SDG cross-check, which needs at least one seed method; none was given. Pass the resolved target as `seeds`.')
    key = cache_key(instance_id, commit, approx, manifest.digest(), mode, seeds)
    dest = out_dir / key
    result_file = dest / 'result.json'
    if result_file.exists() and (not force):
        cached = AnalysisResult.model_validate_json(result_file.read_text())
        if cached.status in ANALYSABLE:
            return cached
    dest.mkdir(parents=True, exist_ok=True)
    edg = dest / 'edg.jsonl'
    index = dest / 'index.json'
    escapes = dest / 'escapes.json'
    selection = cp_select.select(paths.classes, paths.test_classes, paths.libs)
    libs = selection.lib_argument
    args = ['--app-classes', str(paths.classes), '--test-classes', str(paths.test_classes), '--lib-jars', libs, '--source-roots', str(paths.sources), '--entry-points', approx.entry_points.value, '--cg', approx.cg.value, '--cha-augmented', str(approx.cha_augmented).lower(), '--field-sens', str(approx.field_sens).lower(), '--heap-model', approx.heap_model.value, '--data-dep', approx.data_dep.value, '--control-dep', approx.control_dep.value, '--depth-cap', str(approx.depth_cap), '--timeout-seconds', str(approx.analysis_timeout_s), '--mode', mode, *(['--seeds', ','.join(seeds)] if seeds else []), '--out', str(edg), '--index-out', str(index), '--escapes-out', str(escapes)]
    local = _local_runner(memory_gb, cpus)
    t0 = time.time()
    if local is not None:
        cmd = ['taskset', '-c', f'0-{cpus - 1}'] if shutil.which('taskset') else []
        cmd = cmd + local + args
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
            code, stderr = (res.returncode, (res.stderr or '')[-1500:])
        except subprocess.TimeoutExpired:
            code, stderr = (4, f'analyser exceeded {timeout}s')
    else:
        code, stderr = _run_in_container(paths, dest, libs, approx, cpus=cpus, memory_gb=memory_gb, timeout=timeout, mode=mode, seeds=seeds)
    elapsed = time.time() - t0
    status = EXIT_STATUS.get(code, Status.ANALYSIS_FAILED)
    stats: dict = {}
    fired: dict[str, int] = {}
    magnitude: dict[str, int] = {}
    reasons: dict[str, list[str]] = {}
    if escapes.exists():
        payload = json.loads(escapes.read_text())
        stats = payload.get('stats', {})
        fired = payload.get('escapes_fired', {})
        magnitude = payload.get('escapes_magnitude', {})
        reasons = payload.get('element_reasons', {})
    dropped = int(stats.get('skipped_lib_jars', 0)) + int(stats.get('poison_lib_jars', 0))
    if status is Status.OK and dropped:
        status = Status.PARTIAL_CLASSPATH
    if status in (Status.OK, Status.PARTIAL_CLASSPATH) and (not edg.exists()):
        status = Status.ANALYSIS_FAILED
    result = AnalysisResult(instance_id=instance_id, approximation_digest=approx.digest(), status=status, edg_path=edg if edg.exists() else None, index_path=index if index.exists() else None, escapes_path=escapes if escapes.exists() else None, stats=stats, escapes_fired=fired, escapes_magnitude=magnitude, element_reasons=reasons, seconds=round(elapsed, 2), stderr_tail=stderr, shadowing_jars_dropped=selection.shadowing_jars)
    result_file.write_text(result.model_dump_json(indent=2))
    return result

class NoCachedAnalysis(FileNotFoundError):
    pass

def resolve_cached_analysis(instance_id: str, commit: str, prepared: Path, approx: Approximation, analysis_dir: Path, *, mode: str=DEFAULT_MODE, seeds: Sequence[str]=()) -> Path:
    paths = Paths(prepared)
    if not paths.manifest.exists():
        raise NoCachedAnalysis(f'{instance_id}: no classpath manifest at {paths.manifest}; the instance has not been prepared')
    manifest = ClasspathManifest.model_validate_json(paths.manifest.read_text())
    key = cache_key(instance_id, commit, approx, manifest.digest(), mode, seeds)
    dest = Path(analysis_dir) / key
    if not (dest / 'result.json').exists():
        raise NoCachedAnalysis(f'{instance_id}: no cached analysis at {dest} for A={approx.analyser_digest()} (analyser {analyzer_identity()[:19]}). Run the criterion arm first; do not substitute another generation.')
    return dest
