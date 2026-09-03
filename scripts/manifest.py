# -*- coding: utf-8 -*-
"""회차 manifest(`sources/<exam_id>/manifest.json`) 의 읽기·쓰기·검증 한 곳.

## 왜 이 파일이 생겼나

`detect` 가 쓰고 `crop` 과 `extract` 가 읽는데, **읽는 쪽 둘이 서로 다른 코드로
각자 관대하게 추측**하고 있었다. 통합 검증에서 확인한 실제 상태:

- `crop._problem_pdf`  : `data["problem_pdf"] or data["files"]["problem"]` 만 본다.
  값이 dict 인 형태(`{"path": ...}`)를 모르므로 download 가 만든 manifest 에서는
  `files.problem` 이 dict 라 `(d / name)` 이 TypeError 로 터진다 — try/except 로
  통째로 삼키고 있어서 조용히 `problem.pdf` 규약 탐색으로 흘렀다.
- `extractlib.sources.find_sources` : dict 형태는 알지만 `problem_pdf` 는 모른다.

즉 **두 읽기가 커버하는 형태의 교집합이 아니라 합집합이 실제 데이터**였고,
지금 동작하는 이유는 파일 이름이 우연히 규약(`problem.pdf`)과 같아서 폴백이 받아준
것뿐이다. 파일명이 규약과 다른 회차가 하나만 들어와도 두 명령의 판단이 갈린다.

## 실제로 존재하는 세 가지 형태 (실측)

`workspace/*/sources/*/manifest.json` 21개를 전수 조사한 결과다. **새 형태를 만들지
않고 이 셋을 전부 받아들인다.**

1. `detect` 가 쓰는 형태 — `files` 값이 파일명 문자열. 이 모듈의 정규형(canonical)이다.
   `{"exam_id","slug","label","files"{역할:파일명},"detected"{year,exam,grade,by},
     "pages"{역할:쪽수},"provider","verified"}`
2. `download` 가 쓰는 형태 (`schema_version: 2`, 3건) — `files` 값이 dict 이고
   `path`/`sha256`/`source_url` 등 출처 정보를 담는다. 학년도·시험은 `detected` 가
   아니라 최상위 `year`/`exam`/`grade` 에, 과목명은 `label` 이 아니라 `subject_label`
   에 있다. **이 형태는 download 가 소유하므로 여기서는 읽기만 한다.**
3. 손으로 만든 옛 임시 형태 (18건) — `{"exam_id","slug","problem_pdf","source_dir","note"}`.
   `files` 가 아예 없고 문제지 경로만 `problem_pdf` 에 있다.

## 규칙

- **읽기는 셋 다 받는다.** 형태 판별은 `read()` 한 곳에서만 한다.
- **쓰기는 1번 형태만 만든다.** download 가 만든 2번을 detect 가 덮어써서 sha256·
  source_url 을 날려버리면 안 되므로, `merge_preserving()` 이 우리가 모르는 키를
  그대로 통과시킨다.
- 검증(`problems()`)은 "무엇이 없어서 다음 단계가 곤란해지는가"만 돌려준다.
  여기서 죽지 않는다 — 깨진 manifest 는 파일명 규약 폴백이 받아주는 게 계약이다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# 회차 폴더 안의 역할. docs/CONTRACT.md 1절.
# extractlib.sources.ROLES 와 같은 값이지만 그쪽은 "파일을 찾을 때 훑는 순서"라는
# 별도 의미가 있어 통합하지 않았다 — 우연히 같은 값일 뿐 같은 개념이 아니다.
ROLES = ("problem", "answer", "solution")

MANIFEST_NAME = "manifest.json"


@dataclass
class Manifest:
    """한 회차의 manifest. 어떤 형태로 저장돼 있었든 이 모양으로 정규화된다."""

    exam_id: str = ""
    slug: str = ""
    label: str = ""
    # 역할 → 파일명(폴더 기준 상대). 값이 dict 였던 형태는 path 만 뽑아 여기 담는다.
    files: dict[str, str] = field(default_factory=dict)
    detected: dict = field(default_factory=dict)
    pages: dict[str, int] = field(default_factory=dict)
    provider: str = ""
    verified: bool = False
    # 정규화 이전 원본. 우리가 모르는 키(sha256, source_url…)를 보존해 되돌려 쓰기 위함.
    raw: dict = field(default_factory=dict)
    # 어느 형태에서 읽었는지. 리포트에 "왜 이렇게 읽혔나"를 적을 때 쓴다.
    shape: str = "none"
    path: Path | None = None

    def __bool__(self) -> bool:
        return bool(self.raw)

    def name(self, role: str) -> str | None:
        """역할에 해당하는 파일 '이름'. 없으면 None."""
        return self.files.get(role) or None

    def file(self, directory: Path | str, role: str) -> Path | None:
        """역할에 해당하는 파일의 실제 경로. **존재하는 파일만** 돌려준다.

        manifest 가 가리키는 파일이 사라진 경우(사람이 지웠거나 다른 작업공간의
        manifest 를 복사해 온 경우) None 을 주어, 부르는 쪽이 파일명 규약 폴백으로
        내려가게 한다. 예전 crop 은 여기서 존재 검사를 했고 extract 는 안 했다 —
        그 차이 때문에 두 명령이 같은 회차를 다르게 판단할 수 있었다.
        """
        name = self.name(role)
        if not name:
            return None
        candidate = Path(directory) / str(name)
        return candidate if candidate.exists() else None


def _files_from(data: dict) -> tuple[dict[str, str], str]:
    """세 형태 중 무엇이든 {역할: 파일명} 으로 편다. (files, shape) 을 돌려준다."""
    files: dict[str, str] = {}
    raw_files = data.get("files")
    if isinstance(raw_files, dict) and raw_files:
        dictish = False
        for role, entry in raw_files.items():
            if isinstance(entry, dict):
                dictish = True
                name = entry.get("path")
            else:
                name = entry
            if name:
                files[str(role)] = str(name)
        return files, ("download-v2" if dictish else "detect")

    # 옛 임시 형태: 문제지 경로만 problem_pdf 에 있다.
    legacy = data.get("problem_pdf")
    if legacy:
        return {"problem": str(legacy)}, "legacy-problem_pdf"
    return {}, "unknown"


def parse(data: dict, path: Path | None = None) -> Manifest:
    """이미 읽어 둔 dict 를 정규화한다. 파일 입출력을 하지 않아 테스트하기 쉽다."""
    if not isinstance(data, dict):
        return Manifest(path=path)

    files, shape = _files_from(data)

    # 학년도·시험 정보의 위치가 형태마다 다르다.
    #   detect     : detected {year, exam, grade, by}
    #   download v2: 최상위 year/exam/grade (by 없음 — 목록에서 받은 값이라 '출처'가 공급자다)
    detected = data.get("detected")
    if not isinstance(detected, dict):
        detected = {}
        if any(k in data for k in ("year", "exam", "grade")):
            detected = {"year": data.get("year"), "exam": data.get("exam"),
                        "grade": data.get("grade"), "by": data.get("provider") or "manifest"}

    pages = data.get("pages")
    pages = {str(k): int(v) for k, v in pages.items()
             if isinstance(v, int)} if isinstance(pages, dict) else {}

    # verified 의 의미가 형태마다 다르다. download 는 bool 이 아니라 status 문자열로 남긴다.
    verified = data.get("verified")
    if not isinstance(verified, bool):
        verified = data.get("status") == "verified" or data.get("ok") is True

    return Manifest(
        exam_id=str(data.get("exam_id") or (path.parent.name if path else "")),
        slug=str(data.get("slug") or ""),
        label=str(data.get("label") or data.get("subject_label") or ""),
        files=files,
        detected=detected,
        pages=pages,
        provider=str(data.get("provider") or ""),
        verified=bool(verified),
        raw=data,
        shape=shape,
        path=path,
    )


def read(path: Path | str) -> Manifest:
    """manifest.json 하나를 읽어 정규화한다. 없거나 깨졌으면 빈 Manifest.

    **여기서 예외를 던지지 않는다.** manifest 는 편의이지 필수가 아니고
    (extract 는 manifest 없이도 파일명 규약으로 돈다), 깨진 JSON 하나 때문에
    회차 전체가 멈추면 안 된다. 대신 shape 에 왜 비었는지 남긴다.
    """
    path = Path(path)
    if not path.exists():
        return Manifest(path=path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return Manifest(path=path, shape="broken")
    return parse(data, path)


def load(space, exam_id: str) -> Manifest:
    """Space 를 통해 읽는 편의 함수. 경로 규약을 부르는 쪽에 흩뿌리지 않는다."""
    return read(space.manifest(exam_id))


def resolve(space, exam_id: str, role: str) -> Path | None:
    """manifest 를 먼저 믿고, 없으면 파일명 규약(`<역할>.<확장자>`)으로 찾는다.

    crop 과 extract 가 문제지를 찾는 방식이 서로 달랐던 것을 한 함수로 합친 것이다.
    확장자 순서는 extractlib.sources 의 탐색 순서와 같다(PDF 우선, 그다음 이미지).
    정답이 스캔 이미지로만 오는 회차가 흔해 계약이 answer.png 를 허용한다.
    """
    directory = space.source_dir(exam_id)
    found = load(space, exam_id).file(directory, role)
    if found is not None:
        return found
    for suffix in (".pdf", ".png", ".jpg", ".jpeg", ".webp"):
        candidate = directory / f"{role}{suffix}"
        if candidate.exists():
            return candidate
    return None


def problems(m: Manifest, directory: Path | str | None = None) -> list[str]:
    """다음 단계가 곤란해질 지점만 문장으로 돌려준다. 형식 검사가 아니다.

    "스키마에 맞는가"가 아니라 "이걸 들고 crop/extract 를 돌리면 무엇이 안 되는가"를
    묻는다. 그래야 리포트의 attention 한 줄이 곧 사람이 할 일이 된다.
    """
    out: list[str] = []
    if not m:
        out.append("manifest.json 이 없거나 읽을 수 없다 — 파일명 규약으로만 찾게 된다")
        return out
    if m.shape == "broken":
        out.append("manifest.json 이 JSON 으로 파싱되지 않는다")
        return out
    if not m.exam_id:
        out.append("exam_id 가 없다")
    if not m.slug:
        out.append("slug 가 없다 — 어느 과목의 회차인지 알 수 없다")
    if "problem" not in m.files:
        out.append("files.problem 이 없다 — crop 이 문제지를 찾지 못한다")
    if directory is not None:
        for role, name in m.files.items():
            if not (Path(directory) / name).exists():
                out.append(f"files.{role} 이 가리키는 {name} 이(가) 실제로 없다")
    return out


def merge_preserving(previous: Manifest, payload: dict) -> dict:
    """새 payload 를 쓰되, 이전 manifest 의 **우리가 모르는 키는 남긴다.**

    download 가 남긴 sha256·source_url·record_title 은 나중에 원본을 재현할 유일한
    단서다. detect 를 다시 돌렸다는 이유로 그게 사라지면 복구할 방법이 없다.
    files 도 역할별로 병합한다 — 이전 값이 dict(출처 정보 포함)면 path 만 갱신해 유지한다.
    """
    if not previous:
        return payload

    merged = dict(previous.raw)
    prev_files = previous.raw.get("files")
    merged.update(payload)

    if isinstance(prev_files, dict) and isinstance(payload.get("files"), dict):
        new_files: dict = {}
        for role, name in payload["files"].items():
            old = prev_files.get(role)
            if isinstance(old, dict):
                keep = dict(old)
                keep["path"] = name
                new_files[role] = keep
            else:
                new_files[role] = name
        merged["files"] = new_files
    return merged


def build(exam_id: str, slug: str, label: str, files: dict[str, str],
          detected: dict, pages: dict[str, int], provider: str,
          verified: bool) -> dict:
    """정규형(detect 형태) payload 를 만든다. 키 순서·이름이 여기 한 곳에만 있다."""
    return {
        "exam_id": exam_id,
        "slug": slug,
        "label": label,
        "files": dict(files),
        "detected": dict(detected),
        "pages": dict(pages),
        "provider": provider,
        # 3역할이 다 모였을 때만 자동으로 켠다. 사람이 눈으로 본 건 아니라서
        # crop/extract 단계의 최종 검수를 대체하지 않는다.
        "verified": bool(verified),
    }


def write(space, exam_id: str, payload: dict, *, preserve: bool = True) -> Path:
    """정규형으로 저장한다. preserve 면 기존 파일의 모르는 키를 남긴다."""
    path = space.manifest(exam_id)
    data = merge_preserving(read(path), payload) if preserve else payload
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
