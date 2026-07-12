#!/usr/bin/env bash
# =============================================================================
# 통합 검증 진입점 — 세 가지 모드. 로컬과 CI가 동일하게 이 파일을 호출한다.
#
#   bash scripts/verify.sh quick     # 빠른 검증. Stop hook이 매 응답마다 호출.
#                                    # 수 초 내에 끝나야 한다 (lint, 컴파일/타입체크)
#   bash scripts/verify.sh full      # 전체 검증. step AC / /review / CI가 호출.
#                                    # quick + 테스트/빌드 전부
#   bash scripts/verify.sh security  # 보안 자동 점검. /secscan과 CI가 호출.
#                                    # 의존성 취약점 + 시크릿 스캔
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

# 프로젝트 venv 사용 (시스템 파이썬에 설치되는 사고 방지 — profiles/python.md)
if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

MODE="${1:-full}"

quick() {
  ruff check .
}

full() {
  quick
  # 테스트 대상: tests/(앱 테스트) + scripts/(하네스 자체 테스트) — pyproject.toml testpaths
  pytest
}

security() {
  # 공통 최소선 (도구 없이도 동작하는 시크릿 패턴 스캔):
  if git grep -nEI '(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*["'"'"'][^"'"'"']{8,}' \
      -- ':!*.md' ':!scripts/verify.sh' ':!*.lock' ':!package-lock.json' 2>/dev/null; then
    echo "ERROR: 하드코딩된 시크릿 의심 항목이 발견되었습니다." >&2
    exit 1
  fi
  # 의존성 취약점 점검 (설치된 venv 패키지 대상)
  # PYSEC-2026-311(chromadb): 2026-07-12 기준 수정판 미출시 (1.5.9가 최신).
  #   수정판이 나오면 chromadb 업그레이드 후 이 ignore를 제거할 것.
  pip-audit --skip-editable --ignore-vuln PYSEC-2026-311
}

case "$MODE" in
  quick)    quick ;;
  full)     full ;;
  security) security ;;
  *) echo "Usage: verify.sh [quick|full|security]" >&2; exit 1 ;;
esac
