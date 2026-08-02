"""#0016 ablation 용 파일 기반 결정 이력 — DB 없이 rerank on/off 를 비교한다.

벤치마크(`app/evaluation/retrieval_benchmark.py`)는 SQLite 없이 실행되므로
`app/mappings/reranking_lookup.py` 의 SQLAlchemy lookup 을 쓸 수 없다. 여기서는
같은 `CandidateReranker` 계약을 YAML fixture 로 구현한다.

문맥 타입은 `app/domain/mappings/reranking.py` 의 `DecisionContext` 를 그대로
만든다 — 평행 구조를 새로 정의하면 도메인 규칙과 조용히 어긋난다. 이 모듈에도
boost/penalty 수치는 없다(ADR-009 — 점수의 단일 출처는 도메인 모듈).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from app.domain.mappings.decisions import MappingDecisionType
from app.domain.mappings.reranking import RERANK_VERSION, DecisionContext
from app.domain.retrieval import CandidateLocation


@dataclass(frozen=True)
class DecisionFixtureEntry:
    """후보 위치 매칭 규칙 + 그 위치에 붙는 결정 이력 문맥 1건."""

    path: str
    symbol: str | None
    context: DecisionContext

    def matches(self, location: CandidateLocation) -> bool:
        """symbol 이 비면 파일 전체, 있으면 같은 symbol 후보에만 적용한다.

        provider 마다 후보 symbol 이 다르고(RAG chunk / 상수명 / 용어) 줄 버킷도
        달라, fixture 에 dedup_key 를 직접 적으면 매칭이 전부 빗나간다.
        """
        if location.path != self.path:
            return False
        return self.symbol is None or location.symbol == self.symbol


class FixtureDecisionReranker:
    """fixture 항목을 `candidate.dedup_key` 별 문맥으로 묶는 `CandidateReranker`."""

    version = RERANK_VERSION

    def __init__(self, entries: Sequence[DecisionFixtureEntry]) -> None:
        self.entries = tuple(entries)

    def contexts_for(
        self, query, candidates: Sequence
    ) -> dict[str, tuple[DecisionContext, ...]]:
        """키는 `candidate.dedup_key` 다 — orchestrator 가 그 키로 조회한다.

        게이팅(조문·변경유형 대조)은 도메인 `rerank_delta` 가 하므로 여기서는
        위치 매칭만 한다. 매칭이 없는 후보는 키를 만들지 않는다.
        """
        grouped: dict[str, list[DecisionContext]] = {}
        for candidate in candidates:
            matched = [
                entry.context
                for entry in self.entries
                if entry.matches(candidate.location)
            ]
            if matched:
                grouped.setdefault(candidate.dedup_key, []).extend(matched)
        return {key: tuple(value) for key, value in grouped.items()}


def load_decision_fixtures(path: Path | str) -> tuple[DecisionFixtureEntry, ...]:
    """YAML 결정 이력 fixture 를 `DecisionContext` 목록으로 읽는다.

    파일이 없으면 `FileNotFoundError` 다 — `--decisions` 오타를 "이력 없음"으로
    삼키면 rerank 차이가 0 으로 나와 ablation 을 잘못 해석하게 된다. 빈 파일과
    `decisions:` 가 비어 있는 파일은 빈 목록으로 취급한다.
    """
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"decision fixture not found: {source}")
    document = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(document, dict):
        raise ValueError("decision fixture root must be a mapping")
    rows = document.get("decisions") or ()
    if not isinstance(rows, (list, tuple)):
        raise ValueError("decisions must be a list")
    return tuple(_build_entry(index, row) for index, row in enumerate(rows))


def _build_entry(index: int, row: Any) -> DecisionFixtureEntry:
    if not isinstance(row, Mapping):
        raise ValueError(f"decisions[{index}] must be a mapping")
    symbol = _optional_str(row.get("symbol"))
    # 경로 정규화·검증은 후보와 같은 규칙을 재사용한다(계산식 복제 금지).
    location = CandidateLocation(str(row.get("path") or ""), symbol)
    context = DecisionContext(
        article_id=_optional_str(row.get("article_id")),
        change_type=_optional_str(row.get("change_type")),
        state=_parse_state(row.get("state")),
        reason_code=_optional_str(row.get("reason_code")),
        rejection_count=int(row.get("rejection_count") or 0),
        golden_confirmed=bool(row.get("golden_confirmed", False)),
        historical_match=bool(row.get("historical_match", False)),
        legacy=bool(row.get("legacy", False)),
    )
    return DecisionFixtureEntry(location.path, symbol, context)


def _parse_state(value: Any) -> MappingDecisionType | None:
    if value is None or str(value).strip() == "":
        return None
    return MappingDecisionType(str(value).strip())


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
