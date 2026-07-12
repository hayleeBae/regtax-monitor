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
#
# 프로젝트 셋업 시 profiles/<스택>.md의 각 블록을 복사해 채운다.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${1:-full}"

quick() {
  # --- 프로파일의 quick 블록으로 교체 (예: npx tsc --noEmit) -----------------
  echo "ERROR: verify.sh quick이 아직 설정되지 않았습니다. profiles/ 참조." >&2
  exit 1
  # --------------------------------------------------------------------------
}

full() {
  quick
  # --- 프로파일의 full 블록으로 교체 (예: npm test -- --watchAll=false) ------
  echo "ERROR: verify.sh full이 아직 설정되지 않았습니다. profiles/ 참조." >&2
  exit 1
  # --------------------------------------------------------------------------
}

security() {
  # --- 프로파일의 security 블록으로 교체 -------------------------------------
  # 공통 최소선 (도구 없이도 동작하는 시크릿 패턴 스캔):
  if git grep -nEI '(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*["'"'"'][^"'"'"']{8,}' \
      -- ':!*.md' ':!scripts/verify.sh' ':!*.lock' ':!package-lock.json' 2>/dev/null; then
    echo "ERROR: 하드코딩된 시크릿 의심 항목이 발견되었습니다." >&2
    exit 1
  fi
  # 스택별 의존성 취약점 점검을 아래에 추가:
  #   [node] npm audit --audit-level=high
  #   [python] pip-audit
  #   [java] ./gradlew dependencyCheckAnalyze (OWASP Dependency-Check 플러그인)
  # --------------------------------------------------------------------------
}

case "$MODE" in
  quick)    quick ;;
  full)     full ;;
  security) security ;;
  *) echo "Usage: verify.sh [quick|full|security]" >&2; exit 1 ;;
esac
