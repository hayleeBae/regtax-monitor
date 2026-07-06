#!/usr/bin/env python3
"""연말정산 골든 테스트 (mock) — 국세청 모의계산 사례 기대값 대조.

config/tax_rates.xml의 세율표로 종합소득 산출세액을 계산해 golden_cases.json의
기대값과 비교한다. exit 0 = 통과. patch 초안이 세율표를 바꾸면 이 테스트가
계산 정합성을 판정한다 — 개정으로 기대값이 달라지는 경우, patch에
golden_cases.json 갱신을 함께 포함해야 통과한다.

사용: python3 tests/golden_income_tax.py   (repo 루트에서)
"""
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_brackets() -> list[tuple[float, float, int]]:
    tree = ET.parse(ROOT / "config" / "tax_rates.xml")
    brackets = []
    for b in tree.getroot().find("incomeTax").findall("bracket"):
        upper = b.get("upperLimit")
        brackets.append((
            float("inf") if upper == "unlimited" else int(upper),
            float(b.get("rate")),
            int(b.get("deduction") or 0),
        ))
    return sorted(brackets, key=lambda x: x[0])


def calc_income_tax(base: int, brackets) -> int:
    """누진공제 방식: 산출세액 = 과세표준 × 세율 − 누진공제액"""
    for upper, rate, deduction in brackets:
        if base <= upper:
            return int(base * rate - deduction)
    raise ValueError(f"세율 구간을 찾지 못함: {base}")


def main() -> int:
    cases = json.loads((ROOT / "tests" / "golden_cases.json").read_text(encoding="utf-8"))
    brackets = load_brackets()
    failures = []
    for c in cases:
        got = calc_income_tax(c["과세표준"], brackets)
        if got != c["산출세액"]:
            failures.append(
                f"[{c['설명']}] 과세표준 {c['과세표준']:,}: "
                f"기대 {c['산출세액']:,} ≠ 계산 {got:,}"
            )
    if failures:
        print(f"골든 테스트 실패 {len(failures)}/{len(cases)}건:")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print(f"골든 테스트 통과: {len(cases)}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
