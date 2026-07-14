"""VersionedComponent — 컴포넌트 버전 식별 계약.

normalizer_version, classifier_version, prompt_version, scoring_version 등
"어떤 버전이 이 결과를 만들었는가" 를 명시하기 위한 불변 값 객체.
(ARCHITECTURE_V2 §5.9 prompt_versions, §5.3 scoring version, §16 버전 구분 원칙)

버전 문자열 예시: "analysis-v2", "rule-classifier-v1", "v1".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 영숫자 토큰을 . _ - 로 연결한 형태만 허용한다.
# 공백, 슬래시, 빈 문자열 등은 잘못된 version 값으로 거부한다.
_VERSION_RE = re.compile(r"^[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*$")


@dataclass(frozen=True)
class VersionedComponent:
    """이름과 버전을 함께 관리하는 불변 식별자.

    잘못된 version 값(빈 문자열/공백/허용되지 않는 문자)은 생성 시 ValueError.
    """

    name: str
    version: str

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("VersionedComponent.name must be non-empty")
        if not isinstance(self.version, str) or not _VERSION_RE.match(self.version):
            raise ValueError(f"invalid version value: {self.version!r}")

    @property
    def label(self) -> str:
        """`name-version` 형태의 표시 문자열."""
        return f"{self.name}-{self.version}"

    def to_dict(self) -> dict:
        return {"name": self.name, "version": self.version}

    def __str__(self) -> str:
        return self.label
