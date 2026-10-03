from __future__ import annotations
import json
from collections.abc import Iterator
from pathlib import Path
import requests
from pydantic import BaseModel, ConfigDict
URL = 'https://huggingface.co/datasets/Contextbench/ContextBench/resolve/main/data/full.parquet'

class GoldBlock(BaseModel):
    model_config = ConfigDict(frozen=True)
    file: str
    start_line: int
    end_line: int

    def lines(self) -> list[int]:
        return list(range(self.start_line, self.end_line + 1))

class GoldContext(BaseModel):
    model_config = ConfigDict(frozen=True)
    instance_id: str
    original_instance_id: str = ''
    repo: str
    base_commit: str
    language: str
    blocks: tuple[GoldBlock, ...] = ()
    source: str = ''

class ContextBench:
    name = 'contextbench'

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.cache_dir / 'full.parquet'

    def download(self, url: str=URL) -> None:
        if self.path.exists() and self.path.stat().st_size > 0:
            return
        with requests.get(url, timeout=900, stream=True) as resp:
            resp.raise_for_status()
            with self.path.open('wb') as fh:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    fh.write(chunk)

    def java(self) -> Iterator[GoldContext]:
        if not self.path.exists():
            raise FileNotFoundError(f'{self.path} missing. RQ4 needs the ContextBench artefact; ')
        import pyarrow.parquet as pq
        table = pq.read_table(self.path).to_pydict()
        n = len(table['instance_id'])
        for i in range(n):
            if (table['language'][i] or '').lower() != 'java':
                continue
            blocks: list[GoldBlock] = []
            raw = table['gold_context'][i]
            if raw:
                try:
                    for b in json.loads(raw):
                        blocks.append(GoldBlock(file=b['file'], start_line=int(b['start_line']), end_line=int(b['end_line'])))
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue
            yield GoldContext(instance_id=table['instance_id'][i], original_instance_id=table.get('original_inst_id', [''] * n)[i] or '', repo=table['repo'][i], base_commit=table['base_commit'][i], language='java', blocks=tuple(blocks), source=table.get('source', [''] * n)[i] or '')

    def stats(self) -> dict[str, object]:
        rows = list(self.java())
        return {'java_rows': len(rows), 'rows_with_gold': sum((1 for r in rows if r.blocks)), 'blocks_total': sum((len(r.blocks) for r in rows)), 'repos': sorted({r.repo for r in rows})}
