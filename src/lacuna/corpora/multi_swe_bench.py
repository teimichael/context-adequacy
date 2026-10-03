from __future__ import annotations
import json
from collections.abc import Iterator
import requests
from lacuna.corpora.base import Corpus, Instance
HF_BASE = 'https://huggingface.co/datasets/ByteDance-Seed/Multi-SWE-bench/resolve/main/java'
REPOS: dict[str, str] = {'alibaba__fastjson2': 'maven', 'apache__dubbo': 'maven', 'elastic__logstash': 'gradle', 'fasterxml__jackson-core': 'maven', 'fasterxml__jackson-databind': 'maven', 'fasterxml__jackson-dataformat-xml': 'maven', 'google__gson': 'maven', 'googlecontainertools__jib': 'gradle', 'mockito__mockito': 'gradle'}

class MultiSweBenchJava(Corpus):
    name = 'multi-swe-bench'
    primary = True

    def download(self) -> None:
        for slug in REPOS:
            dest = self.cache_dir / f'{slug}.jsonl'
            if dest.exists() and dest.stat().st_size > 0:
                continue
            url = f'{HF_BASE}/{slug}_dataset.jsonl'
            resp = requests.get(url, timeout=300)
            resp.raise_for_status()
            dest.write_bytes(resp.content)

    def iter_raw(self) -> Iterator[dict]:
        missing = [s for s in REPOS if not (self.cache_dir / f'{s}.jsonl').exists()]
        if missing:
            raise FileNotFoundError(f'{self.name}: missing released files for {missing}; run `lacuna corpus download`')
        for slug in REPOS:
            with (self.cache_dir / f'{slug}.jsonl').open() as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        yield json.loads(line)

    def to_instance(self, raw: dict) -> Instance | None:
        f2p = raw.get('f2p_tests') or {}
        if not f2p:
            return None
        org, repo = (raw['org'], raw['repo'])
        slug = f'{org}__{repo}'
        issues = raw.get('resolved_issues') or []
        problem = '\n\n'.join((f"{i.get('title', '')}\n{i.get('body', '')}".strip() for i in issues)).strip()
        if not problem:
            problem = f"{raw.get('title', '')}\n\n{raw.get('body', '')}".strip()
        return Instance(instance_id=raw['instance_id'], corpus=self.name, org=org, repo=repo, base_commit=raw['base']['sha'], problem_statement=problem, fix_patch=raw['fix_patch'], test_patch=raw.get('test_patch', ''), fail_to_pass=tuple(sorted(f2p)), pass_to_pass=tuple(sorted(raw.get('p2p_tests') or {})), image=f"mswebench/{org}_m_{repo}:pr-{raw['number']}", build_system=REPOS.get(slug))
