# -*- coding: utf-8 -*-
"""리포트 계약. LLM 이 읽는 유일한 출력이라 형태가 고정되어 있다.

docs/CONTRACT.md 5절 참조. attention 은 30건에서 자른다 — LLM 컨텍스트 방어.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

ATTENTION_LIMIT = 30
SEVERITIES = ("info", "warn", "error")


class Report:
    def __init__(self, step: str, slug: str, space=None):
        self.step = step
        self.slug = slug
        self.space = space
        self.counts: dict[str, int] = {}
        self.artifacts: list[str] = []
        self.next: str | None = None
        self._attention: list[dict] = []
        self._started = time.time()
        self.extra: dict = {}

    def count(self, **kwargs) -> None:
        self.counts.update(kwargs)

    def bump(self, key: str, by: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + by

    def note(self, ident: str, why: str, severity: str = "warn") -> None:
        if severity not in SEVERITIES:
            raise ValueError(f"severity 는 {SEVERITIES} 중 하나여야 한다: {severity!r}")
        self._attention.append({"id": ident, "why": why, "severity": severity})

    def artifact(self, path) -> None:
        self.artifacts.append(str(path))

    @property
    def has_error(self) -> bool:
        return any(a["severity"] == "error" for a in self._attention)

    def to_dict(self, ok: bool | None = None) -> dict:
        # 심각한 것부터 보여준다. 30건에서 잘리더라도 error 는 살아남아야 한다.
        rank = {"error": 0, "warn": 1, "info": 2}
        ordered = sorted(self._attention, key=lambda a: rank[a["severity"]])
        payload = {
            "step": self.step,
            "slug": self.slug,
            "ok": (not self.has_error) if ok is None else bool(ok),
            "counts": self.counts,
            "attention": ordered[:ATTENTION_LIMIT],
            "artifacts": self.artifacts,
            "next": self.next,
            "elapsed_sec": round(time.time() - self._started, 1),
        }
        if len(ordered) > ATTENTION_LIMIT:
            payload["attention_truncated"] = len(ordered) - ATTENTION_LIMIT
        payload.update(self.extra)
        return payload

    def write(self, path: Path | None = None, ok: bool | None = None) -> Path:
        if path is None:
            if self.space is None:
                raise ValueError("space 또는 path 가 필요하다")
            path = self.space.report(self.step)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.to_dict(ok=ok)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def finish(self, path: Path | None = None, ok: bool | None = None) -> int:
        """리포트를 쓰고 한 줄 요약만 출력한 뒤 종료코드를 돌려준다."""
        p = self.write(path, ok=ok)
        data = self.to_dict(ok=ok)
        counts = " ".join(f"{k}={v}" for k, v in data["counts"].items())
        flag = "OK " if data["ok"] else "FAIL"
        n_att = len(data["attention"]) + data.get("attention_truncated", 0)
        print(f"[{flag}] {self.step} {counts} attention={n_att}")
        print(f"       report: {p}")
        if data["next"]:
            print(f"       next:   {data['next']}")
        return 0 if data["ok"] else 1
