"""replay 지표 산출과 privacy 게이팅된 저장 — HISTORICAL_REPLAY_SPEC §7·§8, ADR-011.

이 모듈의 책임은 둘이다.

1. **채점** — `answer_diff.py` 가 정의한 정답 집합과 초안 실행 결과를 맞대 스펙 §7 의
   지표 9종을 낸다. 계산부(`compute_metrics` 이하)는 전부 순수 함수다: git 도 파일도
   읽지 않으므로 회사 repo 없이 집에서 결정적으로 검증된다.
2. **저장** — replay 산출물이 디스크로 나가는 **유일한 지점**이다(ARCHITECTURE 레이어
   규칙). 저장 직전에 `allowed_artifacts(privacy_mode)`(#0017)를 물어 허용된
   `ArtifactKind` 만 payload 에 **넣는다**.

## 왜 화이트리스트로 담는가

"전부 만든 뒤 위험한 것을 지우는" 방식이면 새 필드를 추가하면서 삭제 목록에 넣는 것을
잊었을 때 그대로 회사 코드가 리포트에 남는다. 반대로 허용 집합을 보고 넣을 것만 넣으면,
같은 실수가 "필드가 빠진 리포트"로 끝난다 — 누락이 안전한 방향으로 실패한다
(fixture.py `_ALLOWED_ARTIFACTS` 주석과 같은 이유, CLAUDE.md 코드 반출 금지).
모드별 판정 자체는 `allowed_artifacts()` 한 곳에만 있고 여기서 복제하지 않는다.

## normalized_diff_similarity 를 판정에 쓰지 않는 이유

스펙 §7 이 명시했듯 같은 개정을 다른 구현으로 만들 수 있어 유사도가 낮다는 것이
초안이 틀렸다는 뜻이 아니다. 값은 리포트에 남기되 `_verdict()` 는 이 값을 보지 않는다.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

from app.evaluation.case import ExpectedReplacement
from app.evaluation.metrics import (
    file_coverage,
    file_recall_at_k,
    patch_replacement_rate,
    reciprocal_rank,
    unnecessary_file_rate,
)
from app.evaluation.replay.answer_diff import (
    AnswerDiff,
    ChangedFile,
    ReplacementCheck,
    normalize_path,
)
from app.evaluation.replay.answer_diff import _normalize_text as normalize_match_text
from app.evaluation.replay.fixture import ArtifactKind, PrivacyMode, allowed_artifacts

REPLAY_REPORT_VERSION = "replay-report-v1"
"""리포트 스키마 버전 — 필드 구성이 바뀌면 올린다."""

RECALL_K: tuple[int, ...] = (1, 3, 5, 10)
"""relevant path Recall@K 의 K 목록 (스펙 §7)."""

GOLDEN_OK_STATUSES: frozenset = frozenset({"passed", "skipped"})
"""판정에서 실패로 보지 않는 골든 상태 — `skipped` 는 골든 명령이 없는 케이스다."""

_HASH_PREFIX_LEN = 16
"""경로 해시 표시 길이 — 리포트 가독성용. 충돌 회피가 아니라 식별자 대체가 목적이다."""


# ---------------------------------------------------------------------------
# 입력 (runner 가 채운다 — Step 5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GeneratedReplacement:
    """초안이 실제로 수행한 before → after 교체 하나."""

    path: str
    before: str
    after: str


@dataclass(frozen=True)
class ReplayOutcome:
    """한 케이스 실행의 원자료.

    `golden_output`·`generated_diff`·`answer_diff_text` 는 코드 본문이라 **기본값이
    없음(None)** 이고, 리포트에는 허용 모드에서만 실린다. 특히 두 diff 본문은 참고용
    유사도를 계산하기 위한 입력일 뿐이며 `DIFF_BODY` 가 허용되지 않으면 저장되지
    않는다 — 계산에 쓰는 것과 저장하는 것은 별개다.
    """

    case_id: str
    answer: AnswerDiff
    replacement_checks: tuple[ReplacementCheck, ...] = ()
    expected_replacements: tuple[ExpectedReplacement, ...] = ()
    generated_files: tuple[str, ...] = ()
    generated_replacements: tuple[GeneratedReplacement, ...] = ()
    retrieved_paths: tuple[str, ...] = ()
    git_apply_ok: Optional[bool] = None
    golden_status: Optional[str] = None
    golden_output: Optional[str] = None
    generated_diff: Optional[str] = None
    answer_diff_text: Optional[str] = None
    duration_ms: int = 0


@dataclass(frozen=True)
class ReplacementOutcome:
    """기대 교체 한 건이 초안에서 수행되었는가 — 리포트 표시용 상세."""

    path: str
    match_mode: str
    matched: bool


@dataclass(frozen=True)
class ReplayMetrics:
    """스펙 §7 지표 한 케이스분.

    `normalized_diff_similarity` 는 참고값이다 — `passed` 계산에 관여하지 않는다.
    """

    case_id: str
    relevant_path_recall_at_k: Mapping[int, float]
    primary_rank: Optional[int]
    expected_replacement_accuracy: float
    file_coverage: float
    unnecessary_file_rate: float
    changed_file_jaccard: float
    git_apply: Optional[bool]
    golden_result: Optional[str]
    normalized_diff_similarity: float
    fixture_replacements_ok: bool
    passed: bool
    duration_ms: int
    replacements: tuple[ReplacementOutcome, ...] = ()


# ---------------------------------------------------------------------------
# 지표 계산 (순수 함수 — 파일시스템·git 접근 없음)
# ---------------------------------------------------------------------------


def _paths(changes: Sequence[ChangedFile]) -> tuple[str, ...]:
    return tuple(normalize_path(changed.path) for changed in changes)


def _normalized(paths: Sequence[str]) -> tuple[str, ...]:
    return tuple(normalize_path(path) for path in paths)


def changed_file_jaccard(
    generated_files: Sequence[str],
    relevant_files: Sequence[str],
) -> float:
    """|초안 ∩ 정답| / |초안 ∪ 정답| (스펙 §7).

    둘 다 비어 있으면 1.0 이다 — "고칠 것이 없었고 아무것도 고치지 않았다"는 어긋남이
    아니다. `file_coverage`(정답 기준)·`unnecessary_file_rate`(초안 기준)가 한쪽만
    보는 데 비해 이 값은 양쪽 어긋남을 하나로 요약한다.
    """
    generated = set(_normalized(generated_files))
    relevant = set(_normalized(relevant_files))
    union = generated | relevant
    if not union:
        return 1.0
    return len(generated & relevant) / len(union)


def primary_rank(
    relevant_files: Sequence[str],
    retrieved_paths: Sequence[str],
) -> Optional[int]:
    """검색 결과에서 정답이 처음 나온 순위(1-base). 없으면 None.

    순위 탐색은 `metrics.reciprocal_rank` 가 이미 하므로 역수를 되돌린다 — 같은 스캔을
    두 벌 두면 "첫 정답"의 정의가 두 곳이 된다.
    """
    rank_score = reciprocal_rank(_normalized(relevant_files), _normalized(retrieved_paths))
    if rank_score <= 0:
        return None
    return int(round(1.0 / rank_score))


def _replacement_keys(
    path: str, before: str, after: str, match_mode: str
) -> tuple[str, str]:
    """교체 한 건을 비교 키로 만든다 — 경로까지 포함해야 다른 파일의 같은 값 교체를
    맞다고 세지 않는다. `NUL` 로 이어붙이는 이유는 경로·코드에 나오지 않는 구분자라서다.
    """
    if match_mode == "normalized_text":
        before, after = normalize_match_text(before), normalize_match_text(after)
    return (f"{normalize_path(path)}\0{before}", after)


def _generated_keys(
    generated: Sequence[GeneratedReplacement],
) -> list[tuple[str, str]]:
    """초안 교체를 exact·normalized 두 형태로 모두 펼친다.

    기대 교체가 어느 `match_mode` 로 오든 같은 집합에서 찾을 수 있게 하기 위해서다.
    """
    keys: list[tuple[str, str]] = []
    for item in generated:
        keys.append(_replacement_keys(item.path, item.before, item.after, "exact"))
        keys.append(
            _replacement_keys(item.path, item.before, item.after, "normalized_text")
        )
    return keys


def normalized_diff_similarity(
    generated_diff: Optional[str], answer_diff_text: Optional[str]
) -> float:
    """두 diff 의 공백 정규화 후 줄 단위 유사도 — **참고값**(스펙 §7).

    한쪽이 없으면 0.0 이다. 낮은 값이 실패를 뜻하지 않으므로 판정에 쓰지 않는다.
    """
    if not generated_diff or not answer_diff_text:
        return 0.0
    left = [normalize_match_text(line) for line in generated_diff.splitlines()]
    right = [normalize_match_text(line) for line in answer_diff_text.splitlines()]
    left = [line for line in left if line]
    right = [line for line in right if line]
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def _verdict(
    accuracy: float,
    coverage: float,
    git_apply: Optional[bool],
    golden_status: Optional[str],
    fixture_ok: bool,
) -> bool:
    """케이스 합격 판정.

    합격 조건: fixture 가 answer commit 과 일관되고(그렇지 않으면 채점 자체가 무의미),
    기대 교체를 모두 수행했고, in-scope 파일을 모두 건드렸고, `git apply --check` 가
    실패하지 않았고, 골든이 실패/에러가 아니다. **유사도는 보지 않는다**(스펙 §7).
    `git_apply`/`golden_status` 의 `None` 은 "실행하지 않음"이므로 실패로 세지 않는다.
    """
    if not fixture_ok:
        return False
    if accuracy < 1.0 or coverage < 1.0:
        return False
    if git_apply is False:
        return False
    if golden_status is not None and golden_status not in GOLDEN_OK_STATUSES:
        return False
    return True


def compute_metrics(outcome: ReplayOutcome) -> ReplayMetrics:
    """한 케이스의 스펙 §7 지표를 낸다 — 순수 함수(외부 I/O 없음).

    Recall·coverage·unnecessary·replacement rate 는 `app.evaluation.metrics` 의 기존
    함수를 그대로 쓴다. 같은 지표를 replay 전용으로 다시 구현하면 두 리포트의 숫자가
    같은 이름으로 다른 뜻을 갖게 된다.
    """
    in_scope = _paths(outcome.answer.in_scope)
    excluded = _paths(outcome.answer.excluded)
    generated = _normalized(outcome.generated_files)
    retrieved = _normalized(outcome.retrieved_paths)

    expected_keys = [
        _replacement_keys(item.path, item.before, item.after, item.match_mode)
        for item in outcome.expected_replacements
    ]
    generated_key_set = set(_generated_keys(outcome.generated_replacements))
    replacements = tuple(
        ReplacementOutcome(
            path=normalize_path(item.path),
            match_mode=item.match_mode,
            matched=key in generated_key_set,
        )
        for item, key in zip(outcome.expected_replacements, expected_keys)
    )

    accuracy = patch_replacement_rate(expected_keys, sorted(generated_key_set))
    coverage = file_coverage(in_scope, generated)
    # "불필요"의 기준은 in-scope ∪ excluded 다 — fixture 가 명시적으로 뺀 파일까지
    # 오답으로 세면 scope 지정이 곧 감점이 되어 사람이 scope 를 좁히기를 꺼리게 된다.
    unnecessary = unnecessary_file_rate(generated, [*in_scope, *excluded])
    fixture_ok = all(check.ok for check in outcome.replacement_checks)

    return ReplayMetrics(
        case_id=outcome.case_id,
        relevant_path_recall_at_k={
            k: file_recall_at_k(in_scope, retrieved, k) for k in RECALL_K
        },
        primary_rank=primary_rank(in_scope, retrieved),
        expected_replacement_accuracy=accuracy,
        file_coverage=coverage,
        unnecessary_file_rate=unnecessary,
        changed_file_jaccard=changed_file_jaccard(generated, in_scope),
        git_apply=outcome.git_apply_ok,
        golden_result=outcome.golden_status,
        normalized_diff_similarity=normalized_diff_similarity(
            outcome.generated_diff, outcome.answer_diff_text
        ),
        fixture_replacements_ok=fixture_ok,
        passed=_verdict(
            accuracy, coverage, outcome.git_apply_ok, outcome.golden_status, fixture_ok
        ),
        replacements=replacements,
        duration_ms=outcome.duration_ms,
    )


def summarize(metrics: Sequence[ReplayMetrics]) -> dict:
    """케이스 지표를 집계한다 — 케이스가 없으면 0 으로 채운 빈 요약."""
    count = len(metrics)
    if not count:
        return {
            "case_count": 0,
            "passed_count": 0,
            "pass_rate": 0.0,
            "recall_at_k": {str(k): 0.0 for k in RECALL_K},
            "expected_replacement_accuracy": 0.0,
            "file_coverage": 0.0,
            "unnecessary_file_rate": 0.0,
            "changed_file_jaccard": 0.0,
            "normalized_diff_similarity": 0.0,
            "git_apply_ok_count": 0,
            "golden_passed_count": 0,
            "average_duration_ms": 0.0,
        }
    passed = sum(1 for item in metrics if item.passed)
    return {
        "case_count": count,
        "passed_count": passed,
        "pass_rate": passed / count,
        "recall_at_k": {
            str(k): sum(item.relevant_path_recall_at_k[k] for item in metrics) / count
            for k in RECALL_K
        },
        "expected_replacement_accuracy": sum(
            item.expected_replacement_accuracy for item in metrics
        )
        / count,
        "file_coverage": sum(item.file_coverage for item in metrics) / count,
        "unnecessary_file_rate": sum(item.unnecessary_file_rate for item in metrics)
        / count,
        "changed_file_jaccard": sum(item.changed_file_jaccard for item in metrics)
        / count,
        # 참고값이므로 집계도 남기지만 pass_rate 와 무관하다(스펙 §7).
        "normalized_diff_similarity": sum(
            item.normalized_diff_similarity for item in metrics
        )
        / count,
        "git_apply_ok_count": sum(1 for item in metrics if item.git_apply is True),
        "golden_passed_count": sum(1 for item in metrics if item.golden_result == "passed"),
        "average_duration_ms": sum(item.duration_ms for item in metrics) / count,
    }


# ---------------------------------------------------------------------------
# privacy 게이팅 저장 (스펙 §8)
# ---------------------------------------------------------------------------


def path_hash(path: str) -> str:
    """경로를 식별자로 대체한다 — `FILE_PATH` 가 허용되지 않는 모드에서 쓴다.

    같은 경로는 실행이 달라도 같은 값이라, 경로를 모른 채로도 "지난번과 같은 파일에서
    또 틀렸다"를 볼 수 있다.
    """
    digest = hashlib.sha256(normalize_path(path).encode("utf-8")).hexdigest()
    return digest[:_HASH_PREFIX_LEN]


def _path_fields(path: str, allowed: frozenset) -> dict:
    """경로 자리 — 해시는 항상, 실제 경로는 `FILE_PATH` 허용 시에만."""
    fields = {"path_hash": path_hash(path)}
    if ArtifactKind.FILE_PATH in allowed:
        fields["path"] = normalize_path(path)
    return fields


def _file_entry(changed: ChangedFile, allowed: frozenset) -> dict:
    """변경 파일 한 건 — 호출 자체가 `DIFF_STRUCTURE` 허용 시에만 일어난다."""
    entry = _path_fields(changed.path, allowed)
    entry["status"] = changed.status
    entry["added_lines"] = changed.added_lines
    entry["removed_lines"] = changed.removed_lines
    return entry


def _replacement_entry(
    outcome: ReplacementOutcome,
    expected: Optional[ExpectedReplacement],
    allowed: frozenset,
) -> dict:
    """기대 교체 한 건 — 일치 여부는 항상, `before`/`after` 는 `CODE_SNIPPET` 허용 시에만."""
    entry = _path_fields(outcome.path, allowed)
    entry["match_mode"] = outcome.match_mode
    entry["matched"] = outcome.matched
    if ArtifactKind.CODE_SNIPPET in allowed and expected is not None:
        entry["before"] = expected.before
        entry["after"] = expected.after
    return entry


def _check_entry(check: ReplacementCheck, allowed: frozenset) -> dict:
    entry = _path_fields(check.path, allowed)
    entry["match_mode"] = check.match_mode
    entry["path_exists"] = check.path_exists
    entry["found_after"] = check.found_after
    entry["found_before"] = check.found_before
    entry["ok"] = check.ok
    return entry


def _metrics_payload(metrics: ReplayMetrics) -> dict:
    """수치 지표 — `ArtifactKind.METRIC`/`COUNT` 라 모든 모드에서 허용된다."""
    return {
        "relevant_path_recall_at_k": {
            str(k): metrics.relevant_path_recall_at_k[k] for k in RECALL_K
        },
        "primary_rank": metrics.primary_rank,
        "expected_replacement_accuracy": metrics.expected_replacement_accuracy,
        "file_coverage": metrics.file_coverage,
        "unnecessary_file_rate": metrics.unnecessary_file_rate,
        "changed_file_jaccard": metrics.changed_file_jaccard,
        # 참고값 — 판정(`passed`)에 쓰이지 않는다(스펙 §7).
        "normalized_diff_similarity": metrics.normalized_diff_similarity,
    }


def _case_payload(
    outcome: ReplayOutcome, metrics: ReplayMetrics, allowed: frozenset
) -> dict:
    """허용된 artifact 종류만 **넣어서** 케이스 payload 를 만든다(화이트리스트).

    분기는 전부 `if <kind> in allowed:` 형태다 — 만들어 놓고 지우는 자리가 없어야
    새 필드를 추가할 때 실수로 코드 본문이 남지 않는다.
    """
    answer = outcome.answer
    payload: dict = {
        # 케이스 식별자·판정·수치는 코드가 아니다(METRIC/COUNT).
        "case_id": outcome.case_id,
        "passed": metrics.passed,
        "git_apply": metrics.git_apply,
        # GOLDEN_OUTPUT 미허용이어도 상태 문자열은 남는다 — 본문만 코드다.
        "golden_result": metrics.golden_result,
        "fixture_replacements_ok": metrics.fixture_replacements_ok,
        "duration_ms": outcome.duration_ms,
        "metrics": _metrics_payload(metrics),
        "counts": {
            "answer_in_scope_files": len(answer.in_scope),
            "answer_out_of_scope_files": len(answer.out_of_scope),
            "answer_excluded_files": len(answer.excluded),
            "generated_files": len(outcome.generated_files),
            "retrieved_paths": len(outcome.retrieved_paths),
            "expected_replacements": len(outcome.expected_replacements),
            "generated_replacements": len(outcome.generated_replacements),
            "matched_replacements": sum(
                1 for item in metrics.replacements if item.matched
            ),
        },
    }

    expected = outcome.expected_replacements
    payload["expected_replacements"] = [
        _replacement_entry(
            item, expected[index] if index < len(expected) else None, allowed
        )
        for index, item in enumerate(metrics.replacements)
    ]
    payload["fixture_checks"] = [
        _check_entry(check, allowed) for check in outcome.replacement_checks
    ]

    # 검색 후보는 diff 가 아니라 경로 목록이다 — 해시로는 모든 모드에서 남긴다.
    payload["retrieved"] = [
        _path_fields(path, allowed) for path in outcome.retrieved_paths
    ]

    if ArtifactKind.DIFF_STRUCTURE in allowed:
        # `DIFF_STRUCTURE` 는 "파일 목록·hunk 헤더·변경 줄 수"다(fixture.py) — 파일
        # 목록 자체가 여기 포함되므로 metadata_only 에서는 위의 counts 만 남는다.
        payload["answer_files"] = {
            "in_scope": [_file_entry(item, allowed) for item in answer.in_scope],
            "out_of_scope": [
                _file_entry(item, allowed) for item in answer.out_of_scope
            ],
            "excluded": [_file_entry(item, allowed) for item in answer.excluded],
        }
        payload["generated_files"] = [
            _path_fields(path, allowed) for path in outcome.generated_files
        ]

    if ArtifactKind.DIFF_BODY in allowed:
        payload["generated_diff"] = outcome.generated_diff
        payload["answer_diff"] = outcome.answer_diff_text

    if ArtifactKind.GOLDEN_OUTPUT in allowed and outcome.golden_output is not None:
        payload["golden_output"] = outcome.golden_output

    return payload


def build_report(
    outcomes: Sequence[ReplayOutcome], privacy_mode: PrivacyMode
) -> dict:
    """저장 payload 를 만든다 — 파일은 쓰지 않는다(테스트가 내용만 볼 수 있게).

    허용 집합은 `allowed_artifacts()` 에게만 묻는다(#0017 단일 출처).
    """
    mode = PrivacyMode(privacy_mode)
    allowed = allowed_artifacts(mode)
    metrics = [compute_metrics(outcome) for outcome in outcomes]
    return {
        "schema_version": REPLAY_REPORT_VERSION,
        "privacy_mode": mode.value,
        "allowed_artifacts": sorted(kind.value for kind in allowed),
        "summary": summarize(metrics),
        "cases": [
            _case_payload(outcome, item, allowed)
            for outcome, item in zip(outcomes, metrics)
        ],
    }


def _render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Historical Replay Report",
        "",
        f"- privacy_mode: `{report['privacy_mode']}`",
        f"- allowed artifacts: {', '.join(report['allowed_artifacts'])}",
        f"- cases: {summary['case_count']} (passed {summary['passed_count']})",
        "",
        "> normalized diff similarity 는 참고값이다 — 합격 판정에 쓰지 않는다"
        " (HISTORICAL_REPLAY_SPEC §7).",
        "",
        "| Case | Passed | Recall@5 | Primary rank | Replacement | Coverage |"
        " Unnecessary | Jaccard | Apply | Golden |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for case in report["cases"]:
        metrics = case["metrics"]
        rank = metrics["primary_rank"]
        lines.append(
            f"| {case['case_id']} | {'yes' if case['passed'] else 'no'} "
            f"| {metrics['relevant_path_recall_at_k']['5']:.3f} "
            f"| {'-' if rank is None else rank} "
            f"| {metrics['expected_replacement_accuracy']:.3f} "
            f"| {metrics['file_coverage']:.3f} "
            f"| {metrics['unnecessary_file_rate']:.3f} "
            f"| {metrics['changed_file_jaccard']:.3f} "
            f"| {_display(case['git_apply'])} | {_display(case['golden_result'])} |"
        )
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- pass rate: {summary['pass_rate']:.3f}",
            f"- mean file coverage: {summary['file_coverage']:.3f}",
            f"- mean unnecessary file rate: {summary['unnecessary_file_rate']:.3f}",
            f"- mean changed file Jaccard: {summary['changed_file_jaccard']:.3f}",
            "- mean normalized diff similarity (참고): "
            f"{summary['normalized_diff_similarity']:.3f}",
            "",
        ]
    )
    return "\n".join(lines)


def _display(value: object) -> str:
    if value is None:
        return "-"
    if value is True:
        return "ok"
    if value is False:
        return "fail"
    return str(value)


def _write_json(path: Path, value: object) -> None:
    """결정적 직렬화 — 정렬된 키·고정 들여쓰기(`retrieval_benchmark.py` 와 같은 규칙)."""
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_report(
    outcomes: Sequence[ReplayOutcome],
    output_dir: Path,
    privacy_mode: PrivacyMode,
    environment: Optional[Mapping[str, object]] = None,
) -> Path:
    """replay 리포트를 `output_dir` 에 쓰고 그 디렉토리를 돌려준다.

    replay 산출물이 디스크로 나가는 **유일한 경로**다(ARCHITECTURE 레이어 규칙) —
    다른 모듈이 각자 저장하면 privacy 모드를 우회하는 구멍이 생긴다.

    출력: `replay_report.json`(기계 판독) · `replay_report.md`(사람 판독) ·
    `environment.json`(재현 정보). 세 파일 모두 정렬된 키로 직렬화해 같은 입력이면
    바이트 단위로 같은 결과가 나온다.
    """
    mode = PrivacyMode(privacy_mode)
    report = build_report(outcomes, mode)

    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    _write_json(target / "replay_report.json", report)
    (target / "replay_report.md").write_text(
        _render_markdown(report), encoding="utf-8"
    )
    _write_json(
        target / "environment.json",
        {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "report_schema_version": REPLAY_REPORT_VERSION,
            "privacy_mode": mode.value,
            "allowed_artifacts": report["allowed_artifacts"],
            "recall_k": list(RECALL_K),
            **dict(environment or {}),
        },
    )
    return target
