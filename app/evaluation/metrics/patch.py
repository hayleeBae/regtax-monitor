"""Patch 지표 — EVALUATION_SPEC.md §8.

모든 함수는 순수 함수다 (외부 I/O 없음).
"""

from __future__ import annotations

from typing import Sequence


def patch_replacement_rate(
    expected_replacements: Sequence[tuple[str, str]],
    actual_replacements: Sequence[tuple[str, str]],
) -> float:
    """예상 replacement 중 실제로 적용된 비율.

    expected_replacements / actual_replacements: [(before, after), ...]
    expected 가 없으면 1.0 (기준 없음).
    """
    if not expected_replacements:
        return 1.0
    actual_set = set(actual_replacements)
    matched = sum(1 for r in expected_replacements if r in actual_set)
    return matched / len(expected_replacements)


def file_coverage(
    relevant_files: Sequence[str],
    patched_files: Sequence[str],
) -> float:
    """관련 파일 중 실제로 패치된 비율.

    relevant_files 가 없으면 1.0 (기준 없음).
    """
    if not relevant_files:
        return 1.0
    patched_set = set(patched_files)
    covered = sum(1 for f in relevant_files if f in patched_set)
    return covered / len(relevant_files)


def unnecessary_file_rate(
    patched_files: Sequence[str],
    relevant_files: Sequence[str],
) -> float:
    """불필요한 파일 수정률 = 관련 없는 파일 수 / 전체 수정 파일 수.

    patched_files 가 없으면 0.0 (불필요 파일 없음).
    """
    if not patched_files:
        return 0.0
    relevant_set = set(relevant_files)
    unnecessary = sum(1 for f in patched_files if f not in relevant_set)
    return unnecessary / len(patched_files)


def forbidden_file_touched(
    patched_files: Sequence[str],
    forbidden_files: Sequence[str],
) -> list[str]:
    """금지 파일 중 실제로 수정된 파일 목록."""
    forbidden_set = set(forbidden_files)
    return [f for f in patched_files if f in forbidden_set]
