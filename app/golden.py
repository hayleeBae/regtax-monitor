"""골든 테스트 검증 — patch 초안이 '계산이 맞는지' 확인한다 (효율 개선 로드맵 3번).

초안 diff를 repo의 스크래치 사본에 적용하고, 설정된 골든 테스트 명령
(GOLDEN_TEST_CMD — 국세청 모의계산 사례 기대값 대조 등)을 실행한다.
"그럴듯해 보임"을 "계산이 맞음"으로 바꾸는 단계이며, 실제 repo는 절대 건드리지
않는다 (승인 게이트의 자동 적용 금지 원칙 유지).

기대값 관리: 개정이 계산 결과를 바꾸는 경우, patch에 골든 케이스 기대값 갱신을
포함해야 통과한다 — 기대값 갱신 자체도 담당자가 승인 게이트에서 검토하게 된다.
"""
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

# 스크래치 복사에서 제외 — 무겁거나 테스트와 무관한 산출물.
# 디렉토리 이름 어휘는 CodebaseAdapter.EXCLUDED_DIRS 와 맞춘다(CLAUDE.md CRITICAL) —
# "classes" 는 eHR exploded WAR(1.7GB·4중 중첩, 2026-08-14 실측) 제외용.
_IGNORE = shutil.ignore_patterns(
    ".git", ".svn", "target", "build", "out", "dist", "node_modules",
    ".venv", "venv", "__pycache__", "classes",
    "chroma_data", "*.db", ".DS_Store",
)
_MAX_OUTPUT = 4000  # DB·응답에 남길 출력 상한 (마지막 부분 우선)


def _strip_comment_header(diff_text: str) -> str:
    """경고 헤더(# 줄)를 제거하고 순수 diff만 남긴다 — git apply 입력용."""
    return "\n".join(
        line for line in diff_text.splitlines() if not line.startswith("#")
    ).strip()


def run_golden_tests(
    repo_root: str, diff_text: str, cmd: str, timeout: int = 300
) -> dict:
    """repo 스크래치 사본에 diff 적용 → cmd 실행 → 결과 반환.
    반환: {status, output, duration_s}
      - passed       : 테스트 통과 (exit 0)
      - failed       : 테스트 실패 — 초안이 골든 기대값과 계산 불일치
      - apply_failed : diff가 스크래치에 적용되지 않음
      - skipped      : GOLDEN_TEST_CMD 미설정 또는 적용할 diff 없음
      - error        : 타임아웃 등 실행 환경 문제
    """
    start = time.time()

    def done(status: str, output: str) -> dict:
        return {
            "status": status,
            "output": output[-_MAX_OUTPUT:],
            "duration_s": round(time.time() - start, 1),
        }

    if not cmd:
        return done("skipped", "GOLDEN_TEST_CMD 미설정 — 골든 테스트 생략")
    clean_diff = _strip_comment_header(diff_text or "")
    if not clean_diff:
        return done("skipped", "적용할 diff가 없음 — 골든 테스트 생략")

    tmp = tempfile.mkdtemp(prefix="regtax_golden_")
    try:
        scratch = Path(tmp) / "repo"
        shutil.copytree(repo_root, scratch, ignore=_IGNORE)

        patch_file = Path(tmp) / "proposal.patch"
        patch_file.write_text(clean_diff + "\n", encoding="utf-8")
        r = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", str(patch_file)],
            cwd=scratch, capture_output=True, text=True,
        )
        if r.returncode != 0:
            return done("apply_failed", (r.stderr or r.stdout or "git apply 실패").strip())

        try:
            r = subprocess.run(
                cmd, shell=True, cwd=scratch,
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return done("error", f"골든 테스트 타임아웃 ({timeout}s): {cmd}")
        output = ((r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")).strip()
        return done("passed" if r.returncode == 0 else "failed",
                    output or f"(출력 없음, exit {r.returncode})")
    except OSError as e:
        return done("error", f"스크래치 준비 실패: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
