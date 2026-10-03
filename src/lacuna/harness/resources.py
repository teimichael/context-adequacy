from __future__ import annotations
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
LACUNA_CONTAINER_PREFIXES = ('lacuna-', 'lacuna-an-', 'lacuna-val-')
LACUNA_PROBE_CONTAINERS = ('mk', 'jc174', 'walaprobe', 'jdkgrab')
LOCK_NAME = '.lacuna.lock'
OWNER_LABEL = 'lacuna.owner-pid'

class ResourcesUnavailable(RuntimeError):
    pass

class RunLocked(RuntimeError):
    pass

@dataclass(frozen=True)
class HostState:
    total_gb: float
    available_gb: float
    free_disk_gb: float
    cpus: int
    docker_ok: bool
    analyzer_image: bool
    stale_containers: tuple[str, ...]

    def render(self) -> str:
        lines = [f'  memory        {self.available_gb:.1f} GB available of {self.total_gb:.1f} GB', f'  cpus          {self.cpus}', f'  disk          {self.free_disk_gb:.0f} GB free', f"  docker        {('reachable' if self.docker_ok else 'UNREACHABLE')}", f"  analyser img  {('present' if self.analyzer_image else 'MISSING')}"]
        if self.stale_containers:
            lines.append(f'  stale         {len(self.stale_containers)} leftover container(s): ' + ', '.join(self.stale_containers[:6]))
        else:
            lines.append('  stale         none')
        return '\n'.join(lines)

def _meminfo_gb(key: str) -> float:
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith(key):
                return int(line.split()[1]) / 1024 ** 2
    except OSError:
        pass
    return 0.0

def _run(cmd: list[str], timeout: int=60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)

def stale_containers() -> list[str]:
    if shutil.which('docker') is None:
        return []
    res = _run(['docker', 'ps', '-a', '--format', '{{.Names}}\t{{.Status}}'])
    if res.returncode != 0:
        return []
    out: list[str] = []
    for line in res.stdout.splitlines():
        name, _, status = line.partition('\t')
        if not status.startswith('Exited'):
            continue
        if name.startswith(LACUNA_CONTAINER_PREFIXES) or name in LACUNA_PROBE_CONTAINERS:
            out.append(name)
    return sorted(out)

def inspect_host(analyzer_image: str='lacuna-analyzer:1.6.10') -> HostState:
    docker_ok = False
    image_ok = False
    if shutil.which('docker') is not None:
        docker_ok = _run(['docker', 'version', '--format', '{{.Server.Os}}']).returncode == 0
        if docker_ok:
            image_ok = _run(['docker', 'image', 'inspect', analyzer_image]).returncode == 0
    usage = shutil.disk_usage(Path.cwd())
    return HostState(total_gb=_meminfo_gb('MemTotal:'), available_gb=_meminfo_gb('MemAvailable:'), free_disk_gb=usage.free / 1024 ** 3, cpus=os.cpu_count() or 1, docker_ok=docker_ok, analyzer_image=image_ok, stale_containers=tuple(stale_containers()))

def clean_containers() -> list[str]:
    removed: list[str] = []
    for name in stale_containers():
        if _run(['docker', 'rm', '-f', name], timeout=120).returncode == 0:
            removed.append(name)
    return removed

def preflight(*, required_memory_gb: int, required_disk_gb: int=50, analyzer_image: str='lacuna-analyzer:1.6.10', require_image: bool=True) -> HostState:
    state = inspect_host(analyzer_image)
    problems: list[str] = []
    if not state.docker_ok:
        problems.append('docker is not reachable')
    if require_image and state.docker_ok and (not state.analyzer_image):
        problems.append(f'analyser image {analyzer_image} is missing; run ./scripts/build_analyzer.sh')
    if state.available_gb and state.available_gb < required_memory_gb:
        problems.append(f'only {state.available_gb:.1f} GB memory available, the configured budget needs {required_memory_gb} GB')
    if state.free_disk_gb < required_disk_gb:
        problems.append(f'only {state.free_disk_gb:.0f} GB disk free, need {required_disk_gb} GB')
    if problems:
        raise ResourcesUnavailable('host preflight failed:\n  - ' + '\n  - '.join(problems) + '\n' + state.render())
    return state

class RunLock:

    def __init__(self, directory: Path, owner: str='experiment') -> None:
        self.path = Path(directory) / LOCK_NAME
        self.owner = owner

    def _stale(self) -> bool:
        try:
            pid = int(self.path.read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return False

    def acquire(self) -> RunLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            if self._stale():
                self.path.unlink(missing_ok=True)
            else:
                raise RunLocked(f'another Lacuna run holds {self.path}:\n  {self.path.read_text().strip()}\nA lock whose owner is dead is reclaimed automatically, so if this one is held, that run is alive. Wait for it, do not delete the lock of a live owner by hand.')
        self.path.write_text(f"{os.getpid()} {self.owner} {time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
        return self

    def release(self) -> None:
        try:
            if self.path.exists() and self.path.read_text().startswith(f'{os.getpid()} '):
                self.path.unlink()
        except OSError:
            pass

    def __enter__(self) -> RunLock:
        return self.acquire()

    def __exit__(self, *exc: object) -> None:
        self.release()

def owner_label_args() -> list[str]:
    return ['--label', f'{OWNER_LABEL}={os.getpid()}']

def running_experiments() -> list[tuple[int, str]]:
    res = _run(['ps', '-eo', 'pid,args'])
    out: list[tuple[int, str]] = []
    mine = os.getpid()
    for line in res.stdout.splitlines()[1:]:
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid_s, args = parts
        if not pid_s.isdigit() or int(pid_s) == mine:
            continue
        if 'shell-snapshots' in args or args.startswith(('/bin/bash', '/bin/sh', 'bash ')):
            continue
        if 'ps -eo' in args or 'doctor' in args:
            continue
        is_cli = '/bin/lacuna' in args or ('python' in args and 'lacuna' in args)
        if is_cli and 'experiment' in args:
            out.append((int(pid_s), args[:120]))
    return out

def orphaned_containers() -> list[str]:
    if shutil.which('docker') is None:
        return []
    res = _run(['docker', 'ps', '--format', '{{.Names}}\t{{.Label "' + OWNER_LABEL + '"}}'])
    if res.returncode != 0:
        return []
    out: list[str] = []
    for line in res.stdout.splitlines():
        name, _, owner = line.partition('\t')
        if not name.startswith(LACUNA_CONTAINER_PREFIXES):
            continue
        if not owner.strip().isdigit():
            out.append(name)
            continue
        try:
            os.kill(int(owner), 0)
        except ProcessLookupError:
            out.append(name)
        except PermissionError:
            pass
    return out

def reap_orphans() -> list[str]:
    removed: list[str] = []
    for name in orphaned_containers():
        if _run(['docker', 'rm', '-f', name], timeout=180).returncode == 0:
            removed.append(name)
    return removed
