"""법령 before/after 문구를 분류·검색용 구조로 정규화한다."""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass
from itertools import zip_longest
from typing import Optional, Sequence

from app.embedding.const_inventory import parse_typed_values


NORMALIZER_VERSION = "change-normalizer-v1"

_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9_,.%]+")
_KOREAN_DATE_RE = re.compile(
    r"(?P<year>\d{4})\s*년(?:\s*(?P<month>\d{1,2})\s*월"
    r"(?:\s*(?P<day>\d{1,2})\s*일)?)?"
)
_ISO_DATE_RE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_COMPARISON_TERMS = ("이상", "이하", "초과", "미만", "포함", "제외", "그리고", "또는")
_STRUCTURAL_TERMS = (
    "신설",
    "삭제",
    "전부개정",
    "전문개정",
    "별표",
    "별지",
    "호를 신설",
    "항을 신설",
)


@dataclass(frozen=True)
class NormalizedValue:
    raw: str
    value: str
    unit: str
    precision: Optional[str] = None


@dataclass(frozen=True)
class ValueDelta:
    before: Optional[NormalizedValue]
    after: Optional[NormalizedValue]


@dataclass(frozen=True)
class NormalizedChange:
    before_text: str
    after_text: str
    added_text: tuple[str, ...]
    removed_text: tuple[str, ...]
    money_changes: tuple[ValueDelta, ...]
    rate_changes: tuple[ValueDelta, ...]
    date_changes: tuple[ValueDelta, ...]
    duration_changes: tuple[ValueDelta, ...]
    age_changes: tuple[ValueDelta, ...]
    comparison_signals: tuple[str, ...]
    structural_signals: tuple[str, ...]
    source_hash: str
    normalizer_version: str


class ChangeNormalizer:
    version = NORMALIZER_VERSION

    def normalize(self, before_text: str, after_text: str) -> NormalizedChange:
        before_values = parse_typed_values(before_text)
        after_values = parse_typed_values(after_text)
        added, removed = _text_delta(before_text, after_text)
        all_text = f"{before_text}\n{after_text}"
        return NormalizedChange(
            before_text=before_text,
            after_text=after_text,
            added_text=added,
            removed_text=removed,
            money_changes=_value_deltas(
                before_values["money"], after_values["money"]
            ),
            rate_changes=_value_deltas(
                before_values["rate"], after_values["rate"]
            ),
            date_changes=_value_deltas(
                _parse_dates(before_text), _parse_dates(after_text)
            ),
            duration_changes=_value_deltas(
                before_values["duration"], after_values["duration"]
            ),
            age_changes=_value_deltas(before_values["age"], after_values["age"]),
            comparison_signals=tuple(
                term for term in _COMPARISON_TERMS if term in all_text
            ),
            structural_signals=tuple(
                term for term in _STRUCTURAL_TERMS if term in all_text
            ),
            source_hash=_source_hash(before_text, after_text),
            normalizer_version=self.version,
        )


def _value_deltas(
    before: Sequence[dict[str, str]],
    after: Sequence[dict[str, str]],
) -> tuple[ValueDelta, ...]:
    deltas: list[ValueDelta] = []
    for old, new in zip_longest(before, after):
        before_value = _normalized_value(old) if old is not None else None
        after_value = _normalized_value(new) if new is not None else None
        if before_value == after_value:
            continue
        deltas.append(ValueDelta(before=before_value, after=after_value))
    return tuple(deltas)


def _normalized_value(value: dict[str, str]) -> NormalizedValue:
    return NormalizedValue(
        raw=value["raw"],
        value=value["value"],
        unit=value["unit"],
        precision=value.get("precision"),
    )


def _parse_dates(text: str) -> list[dict[str, str]]:
    values: list[tuple[int, dict[str, str]]] = []
    occupied: list[tuple[int, int]] = []
    for match in _ISO_DATE_RE.finditer(text):
        values.append(
            (
                match.start(),
                {
                    "raw": match.group(0),
                    "value": match.group(0),
                    "unit": "date",
                    "precision": "day",
                },
            )
        )
        occupied.append(match.span())
    for match in _KOREAN_DATE_RE.finditer(text):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        year = int(match.group("year"))
        month = match.group("month")
        day = match.group("day")
        if day is not None:
            normalized = f"{year:04d}-{int(month):02d}-{int(day):02d}"
            precision = "day"
        elif month is not None:
            normalized = f"{year:04d}-{int(month):02d}"
            precision = "month"
        else:
            normalized = f"{year:04d}"
            precision = "year"
        values.append(
            (
                match.start(),
                {
                    "raw": match.group(0),
                    "value": normalized,
                    "unit": "date",
                    "precision": precision,
                },
            )
        )
    return [value for _position, value in sorted(values, key=lambda item: item[0])]


def _text_delta(before_text: str, after_text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    before_tokens = _TOKEN_RE.findall(before_text)
    after_tokens = _TOKEN_RE.findall(after_text)
    added: list[str] = []
    removed: list[str] = []
    matcher = difflib.SequenceMatcher(a=before_tokens, b=after_tokens, autojunk=False)
    for operation, before_start, before_end, after_start, after_end in matcher.get_opcodes():
        if operation in {"replace", "delete"}:
            removed.extend(before_tokens[before_start:before_end])
        if operation in {"replace", "insert"}:
            added.extend(after_tokens[after_start:after_end])
    return tuple(added), tuple(removed)


def _source_hash(before_text: str, after_text: str) -> str:
    payload = f"before\0{before_text}\0after\0{after_text}".encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"

