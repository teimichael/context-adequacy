from __future__ import annotations
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

def class_names(directory: Path) -> set[str]:
    out: set[str] = set()
    for p in directory.rglob('*.class'):
        rel = p.relative_to(directory).with_suffix('')
        out.add(str(rel).replace('/', '.').replace('\\', '.'))
    return out

def jar_class_names(jar: Path) -> set[str]:
    try:
        with zipfile.ZipFile(jar) as zf:
            return {n[:-6].replace('/', '.') for n in zf.namelist() if n.endswith('.class') and (not n.startswith('META-INF/'))}
    except (zipfile.BadZipFile, OSError):
        return set()

@dataclass
class ClasspathSelection:
    lib_jars: list[Path] = field(default_factory=list)
    shadowing_jars: list[str] = field(default_factory=list)
    unreadable_jars: list[str] = field(default_factory=list)

    @property
    def lib_argument(self) -> str:
        return ':'.join((str(p) for p in self.lib_jars))

def select(classes: Path, test_classes: Path, libs: Path, *, collision_threshold: int=1) -> ClasspathSelection:
    app = class_names(classes) | class_names(test_classes)
    sel = ClasspathSelection()
    for jar in sorted(libs.glob('*.jar')):
        names = jar_class_names(jar)
        if not names:
            sel.unreadable_jars.append(jar.name)
            continue
        overlap = len(names & app)
        if overlap >= collision_threshold:
            sel.shadowing_jars.append(f'{jar.name} ({overlap} shadowed classes)')
            continue
        sel.lib_jars.append(jar)
    return sel
