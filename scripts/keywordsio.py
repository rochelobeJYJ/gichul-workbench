# -*- coding: utf-8 -*-
"""`subjects/<slug>/keywords.json` 을 읽고 쓰는 **유일한** 자리.

## 왜 이 모듈이 생겼나 — 조용히 섞인 두 교육과정

성취기준 코드는 개정마다 새로 매겨지지 않는다. 사회탐구 여러 과목이
2015 개정과 2022 개정에서 **같은 접두사**를 쓴다(실측 9개 과목, 그중 레지스트리에
등록된 것 5개: 경제 `12경제`, 윤리와 사상 `12윤사`, 사회·문화 `12사문`,
세계지리 `12세지`, 세계사 `12세사`).

예전 `keywords.json` 은 성취기준 코드만으로 키를 잡았다. 그래서 2015 의
`12윤사01-01` 과 2022 의 `12윤사01-01` 이 **같은 칸을 덮어썼다.**
실측: 윤리와 사상 22(2015) + 15(2022) = 37개가 파일에는 23개만 남았다.
`classify` 는 접두사로 개정을 가르는데 두 개정의 접두사가 같으니 가를 수도 없어,
결국 2015 문항을 2022 키워드로 채점하게 된다. **에러 없이 틀린다.**

그래서 파일 형태를 개정 한 겹 아래로 내린다.

```json
{
  "_meta":  {"schema": "...", "learned_from": {"2015": ["qid", ...]}},
  "2015":   {"12윤사01-01": {"curriculum": ["..."], "learned": [{"term": "...", "weight": 0.6}]}},
  "2022":   {"12윤사01-01": {"curriculum": ["..."], "learned": []}}
}
```

## 층 이름을 믿지 않는다 — 모양으로 판정한다

`curriculum/standards/<개정>.json` 의 스키마를 **추측**했다가 조용히 실패한 전례가 있다
(PITFALLS 참조 — 최상위 키 `revision`/`sources`/`subjects` 를 성취기준 코드로 착각해
unit 라벨이 전부 None 이 됐는데 아무도 몰랐다). 같은 실수를 되풀이하지 않으려고
여기서는 `_meta.schema` 같은 **자기 신고 문자열을 읽지 않는다.** 개정 층인지 코드 층인지는
키의 모양으로만 판정한다 — 네 자리 연도(`^\\d{4}$`)면 개정, 아니면 성취기준 코드다.
성취기준 코드는 `12윤사01-01` 꼴이라 네 자리 숫자와 절대 겹치지 않는다.

## 옛 형태를 계속 읽는다 (하위호환)

디스크에는 세 형태가 공존한다. 셋 다 읽어야 파이프라인이 안 끊긴다.

  (a) 평면형   {"12윤사01-01": ["용어", ...]}
  (b) 구조형   {"12윤사01-01": {"curriculum": [...], "learned": [...]}}
  (c) 개정형   {"2015": {"12윤사01-01": {...}}, "2022": {...}}      ← 지금 쓰는 형태

(a)·(b) 에는 개정 정보가 없으므로 그 과목의 `standard_prefixes` 로 **역추정**한다.
양쪽 개정에 다 걸리면 아래 순서로 좁히고, 끝내 못 가르면 경고를 남긴다.
추측한 것을 조용히 확정하지 않는 것이 이 저장소의 규칙이다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# 개정 층의 키 모양. 성취기준 코드(`12윤사01-01`)와 겹칠 수 없는 형태여야 한다.
REVISION_RE = re.compile(r"^\d{4}$")
META_KEY = "_meta"

# 개정을 하나도 못 고를 때 마지막으로 기대는 순서. 데이터(subject.json)에 아무것도
# 없을 때만 쓰이며, 과목 이름으로 분기하지 않는다(CONTRACT 0절).
FALLBACK_REVISIONS = ("2015", "2022")

SCHEMA = "revision -> code -> {curriculum: [term], learned: [{term, weight, df, lor}]}"


# ---------------------------------------------------------------- 한 칸의 모양
def normalize_entry(value) -> dict:
    """어떤 형태로 적혀 있든 `{"curriculum": [...], "learned": [...]}` 로 만든다.

    평면형(리스트)은 전부 교육과정 유래로 본다 — 학습분은 가중치를 달고 태어나므로
    가중치 없는 리스트가 학습분일 수 없다.
    """
    if isinstance(value, list):
        return {"curriculum": [str(v) for v in value if str(v).strip()], "learned": []}
    if isinstance(value, dict):
        cur = [str(v) for v in (value.get("curriculum") or []) if str(v).strip()]
        learned: list[dict] = []
        for row in (value.get("learned") or []):
            if isinstance(row, dict) and row.get("term"):
                learned.append(dict(row))
            elif isinstance(row, str) and row.strip():
                # 가중치 없이 손으로 적어 넣은 경우. 학습분과 같은 취급을 하되
                # 가중치는 지어내지 않는다(점수 계산기가 IDF 로 폴백한다).
                learned.append({"term": row.strip()})
        out = {"curriculum": cur, "learned": learned}
        # 계약에 없는 칸(사람이 적어둔 메모 등)을 왕복 중에 잃지 않는다.
        for k, v in value.items():
            if k not in ("curriculum", "learned"):
                out[k] = v
        return out
    return {"curriculum": [], "learned": []}


def entry_terms(entry: dict) -> list[str]:
    """한 칸의 모든 용어(교육과정 + 학습분). 순서는 교육과정 먼저 — 점수 계산기가
    같은 용어를 두 번 세지 않도록 여기서 한 번만 합친다."""
    terms = list(entry.get("curriculum") or [])
    for row in entry.get("learned") or []:
        term = str(row.get("term") or "").strip()
        if term and term not in terms:
            terms.append(term)
    return terms


# ---------------------------------------------- 성취기준 코드 → 그 코드를 가진 개정
_CODE_OWNERS: dict[str, dict[str, set[str]]] = {}


def _walk_codes(node, out: set[str]) -> None:
    """트리 전체에서 `{"code": ...}` 레코드를 줍는다.

    층 이름(`subjects`/`units`/`standards`)을 믿지 않는 이유는 모듈 docstring 참조.
    """
    if isinstance(node, dict):
        code = node.get("code")
        if isinstance(code, str) and code.strip():
            out.add(code.strip())
        for v in node.values():
            _walk_codes(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_codes(v, out)


def code_owners(revisions: tuple[str, ...] | list[str]) -> dict[str, set[str]]:
    """{성취기준코드: {그 코드를 실제로 선언한 개정, ...}}.

    `curriculum/standards/<개정>.json` 이 없으면 빈 dict 다 — 없어도 동작해야 하므로
    이것은 접두사 판정을 **보조**하는 증거일 뿐 필수 입력이 아니다.
    """
    key = ",".join(sorted(revisions))
    if key in _CODE_OWNERS:
        return _CODE_OWNERS[key]
    # 지연 임포트: common 패키지 초기화 중에 이 모듈이 불릴 수 있어(순환 방지)
    # 모듈 최상단에서 common 을 끌어오지 않는다.
    from common.paths import CURRICULUM_STANDARDS

    owners: dict[str, set[str]] = {}
    for rev in revisions:
        path = CURRICULUM_STANDARDS / f"{rev}.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        found: set[str] = set()
        _walk_codes(data, found)
        for code in found:
            owners.setdefault(code, set()).add(rev)
    _CODE_OWNERS[key] = owners
    return owners


# ---------------------------------------------------------------- KeywordBook
@dataclass
class KeywordBook:
    """개정 → 성취기준 코드 → 키워드 칸. `keywords.json` 의 메모리 표현."""

    revisions: dict[str, dict[str, dict]] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    # (id, why, severity) — 부르는 쪽이 그대로 Report.note() 에 넘긴다.
    # 여기서 print 하지 않는다: LLM 이 읽는 유일한 출력은 리포트다(CONTRACT 2절).
    notes: list[tuple[str, str, str]] = field(default_factory=list)
    # "revision" | "legacy" | "mixed" | "empty" — 마이그레이션이 필요한지 부르는 쪽이 안다.
    source_shape: str = "empty"

    # --- 읽기 ---
    def revision(self, rev: str) -> dict[str, dict]:
        """그 개정의 {코드: 칸}. 없으면 빈 dict (파일을 만들지 않는다)."""
        return self.revisions.get(rev) or {}

    def entry(self, rev: str, code: str) -> dict:
        return self.revision(rev).get(code) or {"curriculum": [], "learned": []}

    def codes(self, rev: str) -> list[str]:
        return sorted(self.revision(rev))

    def known_revisions(self) -> list[str]:
        return sorted(self.revisions)

    def is_empty(self) -> bool:
        return not any(self.revisions.values())

    def learned_count(self, rev: str | None = None) -> int:
        revs = [rev] if rev else list(self.revisions)
        return sum(len(e.get("learned") or [])
                   for r in revs for e in self.revision(r).values())

    def term_count(self, rev: str | None = None) -> int:
        revs = [rev] if rev else list(self.revisions)
        return sum(len(e.get("curriculum") or [])
                   for r in revs for e in self.revision(r).values())

    def stats(self, rev: str) -> dict:
        entries = self.revision(rev)
        return {"codes": len(entries),
                "curriculum_terms": sum(len(e.get("curriculum") or []) for e in entries.values()),
                "learned_terms": sum(len(e.get("learned") or []) for e in entries.values())}

    # --- 쓰기 ---
    def set_entry(self, rev: str, code: str, entry: dict) -> None:
        self.revisions.setdefault(rev, {})[code] = normalize_entry(entry)

    def set_revision(self, rev: str, entries: dict[str, dict]) -> None:
        self.revisions[rev] = {c: normalize_entry(v) for c, v in entries.items()}

    def to_dict(self) -> dict:
        """디스크에 쓸 모양. `_meta` 를 맨 앞에 둔다(사람이 먼저 보는 칸이라)."""
        out: dict = {}
        if self.meta:
            out[META_KEY] = self.meta
        for rev in sorted(self.revisions):
            entries = self.revisions[rev]
            if not entries:
                # 빈 개정 층은 쓰지 않는다. `{}` 만 남으면 "이 개정은 사전이 있다"고
                # 읽힐 여지가 있는데, 실제로는 아무 근거도 없는 상태다.
                continue
            out[rev] = {code: entries[code] for code in sorted(entries)}
        return out


# ---------------------------------------------------------------- 개정 역추정
def infer_revision(code: str, prefixes_by_rev: dict[str, list[str]],
                   owners: dict[str, set[str]] | None = None) -> tuple[str, str | None]:
    """옛 형태의 코드 하나를 어느 개정에 넣을지 정한다. → (개정, 경고 사유|None)

    순서에 이유가 있다.
      1) 접두사가 한 개정에만 걸리면 그 개정이다 (지구과학·생활과 윤리 등 대부분).
      2) 여러 개정에 걸리면 **접두사가 겹치지 않는 쪽** — 즉 더 좁은(긴) 접두사로
         걸린 쪽을 고른다. 한쪽이 `12지과` 이고 다른 쪽이 `12지과Ⅱ` 면 `12지과Ⅱ01-01`
         은 양쪽에 다 걸리지만, 그 코드를 자기 것이라고 더 구체적으로 말한 쪽이 주인이다.
         (PITFALLS 5-6 이 바로 이 모양의 사고다 — `12지구` 라고 적힌 접두사가
          다른 개정의 실재하는 코드와 겹쳐 대조를 통과할 뻔했다.)
      3) 길이도 같으면(윤리와 사상처럼 두 개정의 접두사가 **글자 그대로 같으면**)
         `curriculum/standards/<개정>.json` 이 그 코드를 실제로 선언하는지 본다.
         한쪽만 선언하면 그쪽이다.
      4) 여기까지 와도 못 가르면 **가장 이른 개정**에 넣고 경고를 돌려준다.
         옛 파일은 `standards --draft-keywords` 가 만든 것이고, 그 명령은 충돌 시
         앞 개정(기출 문항이 있는 쪽)을 남겼다. 즉 이 폴백은 옛 동작의 재현이다.
         추측이라는 사실을 리포트로 올려서 사람이 볼 수 있게 한다.
    """
    revs = sorted(prefixes_by_rev) or list(FALLBACK_REVISIONS)
    hits = {rev: [p for p in (prefixes_by_rev.get(rev) or []) if code.startswith(p)]
            for rev in revs}
    hits = {rev: ps for rev, ps in hits.items() if ps}

    if len(hits) == 1:
        return next(iter(hits)), None

    if len(hits) > 1:
        best = {rev: max(len(p) for p in ps) for rev, ps in hits.items()}
        longest = max(best.values())
        narrowest = [rev for rev, n in best.items() if n == longest]
        if len(narrowest) == 1:
            return narrowest[0], None
        cand = sorted(narrowest)
        if owners:
            owned = [rev for rev in cand if rev in (owners.get(code) or set())]
            if len(owned) == 1:
                return owned[0], None
        return cand[0], (f"접두사 {sorted({p for ps in hits.values() for p in ps})} 가 "
                         f"{', '.join(cand)} 개정에 모두 걸린다 — {cand[0]} 로 넣었다(추정)")

    # 접두사가 아무것도 안 걸린다. subject.json 의 standard_prefixes 가 비었거나 틀린 것이다.
    if owners:
        owned = sorted(owners.get(code) or set())
        if len(owned) == 1:
            return owned[0], (f"standard_prefixes 로는 개정을 못 정했다 — "
                              f"curriculum/standards 가 이 코드를 {owned[0]} 에서만 "
                              f"선언하므로 그쪽에 넣었다")
    fallback = revs[0]
    return fallback, (f"standard_prefixes 어느 쪽에도 안 걸린다 — {fallback} 로 넣었다(추정). "
                      f"subjects/<slug>/subject.json 의 standard_prefixes 를 확인해라")


# ---------------------------------------------------------------- load / save
def load(path: Path | str, prefixes_by_rev: dict[str, list[str]] | None = None) -> KeywordBook:
    """`keywords.json` → KeywordBook. 세 형태를 모두 읽는다.

    파일이 없거나 깨져 있어도 예외를 던지지 않는다. 사전이 없는 것은 정상 상태이고
    (보정 전 과목은 어차피 전부 큐로 간다), 읽기 실패는 `notes` 로 올린다.
    """
    path = Path(path)
    prefixes_by_rev = {k: list(v or []) for k, v in (prefixes_by_rev or {}).items()}
    book = KeywordBook(meta={}, revisions={}, notes=[], source_shape="empty")
    if not path.exists():
        return book
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        book.notes.append((path.name, f"읽을 수 없다: {exc}", "error"))
        return book
    if not isinstance(raw, dict):
        book.notes.append((path.name, "최상위가 객체가 아니다 — 빈 사전으로 취급한다", "error"))
        return book

    if isinstance(raw.get(META_KEY), dict):
        book.meta = dict(raw[META_KEY])

    # ── 모양으로 층을 가른다. `_meta.schema` 문자열은 읽지 않는다(docstring 참조). ──
    legacy_codes: dict[str, object] = {}
    for key, value in raw.items():
        if key == META_KEY:
            continue
        if REVISION_RE.match(key) and isinstance(value, dict):
            book.revisions.setdefault(key, {})
            for code, entry in value.items():
                book.revisions[key][code] = normalize_entry(entry)
        else:
            legacy_codes[key] = value
    had_revision_layer = bool(book.revisions)

    if legacy_codes:
        revs = sorted(prefixes_by_rev) or list(FALLBACK_REVISIONS)
        owners = code_owners(tuple(revs))
        guessed: list[str] = []
        why_seen: dict[str, str] = {}
        for code, value in legacy_codes.items():
            rev, why = infer_revision(code, prefixes_by_rev, owners)
            entry = normalize_entry(value)
            existing = book.revisions.setdefault(rev, {}).get(code)
            if existing:
                # 개정 층과 옛 코드 층이 같은 코드를 함께 들고 있는 뒤섞인 파일.
                # 개정 층이 더 최신이므로 그쪽을 남기고 학습분만 잃지 않게 합친다.
                if not existing.get("learned") and entry.get("learned"):
                    existing["learned"] = entry["learned"]
            else:
                book.revisions[rev][code] = entry
            if why:
                guessed.append(code)
                why_seen[why] = why
        book.source_shape = "mixed" if had_revision_layer else "legacy"
        if guessed:
            # 몇 개를 어떤 이유로 추정했는지 한 줄로 올린다. 코드마다 한 줄씩 올리면
            # attention 30건 상한(CONTRACT 5절)을 이것만으로 다 써버린다.
            head = "; ".join(sorted(why_seen))
            book.notes.append((path.name,
                               f"옛 형태(개정 층 없음)를 읽어 개정을 역추정했다 — "
                               f"{len(guessed)}개 코드가 불확실하다({', '.join(sorted(guessed)[:4])}"
                               f"{'…' if len(guessed) > 4 else ''}). {head}", "warn"))
    elif had_revision_layer:
        book.source_shape = "revision"
    return book


def save(path: Path | str, book: KeywordBook, dry_run: bool = False) -> Path:
    """항상 개정형으로 쓴다. 옛 형태로 되돌려 쓰는 경로는 두지 않는다 —
    두 형태를 동시에 쓰기 시작하면 어느 쪽이 진실인지 다시 알 수 없게 된다."""
    path = Path(path)
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(book.to_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return path
