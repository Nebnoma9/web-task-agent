"""Per-episode JSONL trace. Every record passes through the redactor before hitting disk."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, TextIO

from wta.redact import Redactor


class Tracer:
    def __init__(self, path: Path, redactor: Redactor) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.redactor = redactor
        self._fh: TextIO = path.open("w", encoding="utf-8")

    def log(self, event: str, **fields: Any) -> dict[str, Any]:
        raw: dict[str, Any] = {"ts": round(time.time(), 3), "event": event, **fields}
        record: dict[str, Any] = self.redactor.obj(raw)
        self._fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()
        return record

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> Tracer:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def read_trace(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
