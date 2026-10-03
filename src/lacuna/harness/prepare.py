from __future__ import annotations
import hashlib
import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field
from lacuna.corpora.base import Instance, Status
from lacuna.harness.resources import owner_label_args

class DockerError(RuntimeError):
    pass

@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def classes(self) -> Path:
        return self.root / 'classes'

    @property
    def test_classes(self) -> Path:
        return self.root / 'test-classes'

    @property
    def libs(self) -> Path:
        return self.root / 'libs'

    @property
    def sources(self) -> Path:
        return self.root / 'sources'

    @property
    def manifest(self) -> Path:
        return self.root / 'manifest.json'

class ClasspathManifest(BaseModel):
    model_config = ConfigDict(frozen=True)
    instance_id: str
    image: str
    image_digest: str = ''
    build_system: str
    build_command: str
    build_ok: bool
    build_seconds: float = 0.0
    app_class_files: int = Field(default=0, ge=0)
    test_class_files: int = Field(default=0, ge=0)
    lib_jars: int = Field(default=0, ge=0)
    source_files: int = Field(default=0, ge=0)
    classpath_method: str = 'glob-local-repository'
    build_command_version: int = 1
    debug_info_level: str = 'unknown'
    recompiled_for_debug: bool = False
    bytecode_major_versions: list[int] = []
    modules_missing: list[str] = []
    status: Status = Status.OK
    note: str = ''

    def digest(self) -> str:
        payload = json.dumps(self.model_dump(mode='json', exclude={'build_seconds', 'note'}), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

def _run(cmd: list[str], timeout: int=1800) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)

def image_available(image: str) -> bool:
    return _run(['docker', 'image', 'inspect', image], timeout=120).returncode == 0

def pull(image: str, timeout: int=1800) -> None:
    if image_available(image):
        return
    res = _run(['docker', 'pull', image], timeout=timeout)
    if res.returncode != 0:
        raise DockerError(f'docker pull {image} failed: {res.stderr.strip()[:400]}')

def image_digest(image: str) -> str:
    res = _run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image], timeout=120)
    return res.stdout.strip() if res.returncode == 0 else ''
_CAPTURE = "{ echo '--- lacuna: first errors ---';   grep -E '^\\[ERROR\\]|error:|FAILURE|Could not resolve|Cannot resolve'        /tmp/lacuna-build.log | head -20;   echo '--- lacuna: tail ---'; tail -10 /tmp/lacuna-build.log; } 2>/dev/null || true; "
_MAVEN = 'if command -v mvn >/dev/null; then MVN=mvn; elif [ -x ./mvnw ]; then MVN=./mvnw; else echo LACUNA_NO_BUILD_TOOL; exit 0; fi; $MVN -o -B -q -fae -DskipTests test-compile >/tmp/lacuna-build.log 2>&1 || true; ' + _CAPTURE + 'if ! find . -path \'*/target/classes/*\' -name \'*.class\' -print -quit | grep -q .; then   if [ -x ./mvnw ] && [ "$MVN" != ./mvnw ]; then     ./mvnw -o -B -q -fae -DskipTests test-compile >/tmp/lacuna-build.log 2>&1 || true; ' + _CAPTURE + 'fi; fi'
_GRADLE = 'if [ -x ./gradlew ]; then G=./gradlew; elif command -v gradle >/dev/null; then G=gradle; else echo LACUNA_NO_BUILD_TOOL; exit 0; fi; $G --offline --no-daemon -q testClasses >/tmp/lacuna-build.log 2>&1 || true; ' + _CAPTURE
CURRENT_BUILD_COMMAND_VERSION = 2
BUILD_COMMANDS: dict[str, str] = {'maven': 'set -e; cd $(ls -d /home/*/ | head -1); ' + _MAVEN, 'gradle': 'set -e; cd $(ls -d /home/*/ | head -1); ' + _GRADLE}
STAGE = '\ncd $(ls -d /home/*/ | head -1)\nmkdir -p /stage/c /stage/t /stage/l /stage/s\nfind . -type d -name classes -path \'*/target/*\' \\\n     -exec cp -r {}/. /stage/c/ \\; 2>/dev/null || true\nfind . -type d -name test-classes -path \'*/target/*\' \\\n     -exec cp -r {}/. /stage/t/ \\; 2>/dev/null || true\nfind . -type d -path \'*/build/classes/java/main\' \\\n     -exec cp -r {}/. /stage/c/ \\; 2>/dev/null || true\nfind . -type d -path \'*/build/classes/java/test\' \\\n     -exec cp -r {}/. /stage/t/ \\; 2>/dev/null || true\nfind /root/.m2 /root/.gradle "$HOME"/.gradle -name \'*.jar\' \\\n     ! -name \'*sources*\' ! -name \'*javadoc*\' -exec cp {} /stage/l/ \\; 2>/dev/null || true\nfind . -type d -path \'*/src/main/java\' -exec cp -r {}/. /stage/s/ \\; 2>/dev/null || true\nfind . -type d -path \'*/src/test/java\' -exec cp -r {}/. /stage/s/ \\; 2>/dev/null || true\nfind . -type d -path \'*generated-sources*\' -exec cp -r {}/. /stage/s/ \\; 2>/dev/null || true\n'
PROBE = '\nLINES=0; VARS=0; MAJ=0\nfor C in $(find /stage/c -name \'*.class\' 2>/dev/null | head -25); do\n  L=$(javap -l -p -c "$C" 2>/dev/null | grep -c LineNumberTable || true)\n  V=$(javap -l -p "$C" 2>/dev/null | grep -c LocalVariableTable || true)\n  M=$(od -An -tu1 -j7 -N1 "$C" 2>/dev/null | tr -d \' \')\n  LINES=$((LINES + L)); VARS=$((VARS + V))\n  if [ -n "$M" ] && [ "$M" -gt "$MAJ" ] 2>/dev/null; then MAJ=$M; fi\ndone\nif [ "$LINES" -gt 0 ] && [ "$VARS" -gt 0 ]; then L="lines+vars";\nelif [ "$LINES" -gt 0 ]; then L="lines"; else L="none"; fi\necho "PROBE $L $MAJ"\n'
_NOISE = ('To see the full stack trace', 'Re-run Maven', 'For more information about the errors', '[Help 1]', 'http://cwiki.apache.org', 'BUILD FAILURE', 'Total time:', 'Finished at:')

def _diagnostic_lines(output: str, limit: int=4) -> str:
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]

    def substantive(ln: str) -> bool:
        if any((n in ln for n in _NOISE)):
            return False
        body = ln
        for prefix in ('[ERROR]', '[WARNING]', '[FATAL]', 'ERROR:', 'FAILURE:'):
            if body.startswith(prefix):
                body = body[len(prefix):].strip()
        if len(body) < 8:
            return False
        return any((k in ln for k in ('ERROR', 'FAIL', 'Could not', 'not found', 'Cannot', 'Unable', 'No such', 'Non-resolvable', 'Unknown')))
    interesting = [ln for ln in lines if substantive(ln)]
    chosen = interesting[:limit] or [ln for ln in lines if len(ln) > 8][-limit:] or lines[-limit:]
    return ' | '.join(chosen)

def prepare(instance: Instance, dest: Path, *, cpus: int=4, memory_gb: int=6, build_timeout: int=1800, force: bool=False, apply_gold_patch: bool=False) -> ClasspathManifest:
    paths = Paths(dest)
    if paths.manifest.exists() and (not force):
        cached = ClasspathManifest.model_validate_json(paths.manifest.read_text())
        current = cached.build_command_version == CURRENT_BUILD_COMMAND_VERSION
        if cached.status is Status.OK and current:
            return cached
    if instance.image is None:
        raise ValueError(f'{instance.instance_id}: corpus {instance.corpus} ships no image; this corpus needs a built image before it can be prepared')
    dest.mkdir(parents=True, exist_ok=True)
    build_system = instance.build_system or 'maven'
    command = BUILD_COMMANDS[build_system]
    if apply_gold_patch:
        command = 'set -e; cd $(ls -d /home/*/ | head -1); git apply --whitespace=nowarn /home/fix.patch 2>/dev/null || git apply -3 --whitespace=nowarn /home/fix.patch 2>/dev/null || true; ' + (_MAVEN if build_system == 'maven' else _GRADLE)

    def fail(status: Status, note: str) -> ClasspathManifest:
        man = ClasspathManifest(instance_id=instance.instance_id, image=instance.image or '', build_system=build_system, build_command=command, build_command_version=CURRENT_BUILD_COMMAND_VERSION, build_ok=False, status=status, note=note[:500])
        paths.manifest.write_text(man.model_dump_json(indent=2))
        return man
    try:
        pull(instance.image)
    except (DockerError, subprocess.TimeoutExpired) as exc:
        return fail(Status.IMAGE_UNAVAILABLE, str(exc))
    digest = image_digest(instance.image)
    name = 'lacuna-' + hashlib.sha256(instance.instance_id.encode()).hexdigest()[:16]
    _run(['docker', 'rm', '-f', name], timeout=120)
    t0 = time.time()
    try:
        res = _run(['docker', 'run', '--name', name, *owner_label_args(), f'--cpus={cpus}', f'--memory={memory_gb}g', f'--memory-swap={memory_gb}g', '-e', f'MAVEN_OPTS=-Xmx{max(2, memory_gb - 2)}g', '-e', f'GRADLE_OPTS=-Xmx{max(2, memory_gb - 2)}g', '--network', 'none', '--entrypoint', 'bash', instance.image, '-c', command + '\n' + STAGE + '\n' + PROBE], timeout=build_timeout)
        build_seconds = time.time() - t0
        debug_level = 'unknown'
        major = 0
        for line in (res.stdout or '').splitlines():
            if line.startswith('PROBE '):
                parts = line.split()
                debug_level = parts[1]
                major = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        if res.returncode != 0 and debug_level == 'unknown':
            return fail(Status.BUILD_FAILED, (res.stderr or res.stdout or '')[-500:])
        for target in (paths.classes, paths.test_classes, paths.libs, paths.sources):
            shutil.rmtree(target, ignore_errors=True)
            target.mkdir(parents=True, exist_ok=True)
        for src, dst in [('/stage/c', paths.classes), ('/stage/t', paths.test_classes), ('/stage/l', paths.libs), ('/stage/s', paths.sources)]:
            _run(['docker', 'cp', f'{name}:{src}/.', str(dst)], timeout=900)
    finally:
        _run(['docker', 'rm', '-f', name], timeout=120)
    app_n = sum((1 for _ in paths.classes.rglob('*.class')))
    test_n = sum((1 for _ in paths.test_classes.rglob('*.class')))
    lib_n = sum((1 for _ in paths.libs.glob('*.jar')))
    src_n = sum((1 for _ in paths.sources.rglob('*.java')))
    build_tail_text = _diagnostic_lines((res.stdout or '') + (res.stderr or ''))
    if src_n == 0:
        return fail(Status.EXPORT_INCOMPLETE, f'staged {app_n} class files but 0 source files; the mapping layer cannot run without sources. Build output: {build_tail_text[:300]}')
    if app_n == 0:
        note = 'no build tool in the image (neither wrapper nor system launcher)' if 'LACUNA_NO_BUILD_TOOL' in (res.stdout or '') else 'build produced no application class files'
        return fail(Status.BUILD_FAILED, f'{note}. Build output: {build_tail_text[:400]}')
    status = Status.OK
    note = ''
    if debug_level == 'none':
        status = Status.NO_DEBUG_INFO
        note = 'bytecode carries no LineNumberTable; source mapping is not possible'
    elif major and major > 68:
        status = Status.BYTECODE_UNSUPPORTED
        note = f"class-file major version {major} exceeds WALA 1.6.10's maximum of 68"
    man = ClasspathManifest(instance_id=instance.instance_id, image=instance.image, image_digest=digest, build_system=build_system, build_command=command, build_command_version=CURRENT_BUILD_COMMAND_VERSION, build_ok=True, build_seconds=round(build_seconds, 1), app_class_files=app_n, test_class_files=test_n, lib_jars=lib_n, source_files=src_n, debug_info_level=debug_level, bytecode_major_versions=[major] if major else [], status=status, note=note)
    paths.manifest.write_text(man.model_dump_json(indent=2))
    return man
