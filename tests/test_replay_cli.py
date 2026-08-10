"""Issue #0022 step 2 — replay CLI (`--pipeline stub|real`) 테스트.

`--pipeline real` 분기는 **배선만** 검증한다: `build_real_pipeline` 을 가짜로 바꿔
임베딩·ChromaDB·추론 백엔드를 한 번도 태우지 않는다(CLAUDE.md — 테스트에서 무거운
의존성 금지). 여기서 지키려는 것은 두 가지다.

1. 기존 `--stub perfect` 사용법이 그대로 동작한다(#0018 런북·테스트가 쓰는 형태).
2. `LLM_BACKEND` 가 local 이 아니면 **명시적 opt-in 없이는 시작하지 않는다** —
   replay 는 케이스를 연속 실행하므로 실수로 대량 전송될 여지가 크다(CLAUDE.md
   CRITICAL — 코드는 외부로 나가지 않는다).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import build_replay_repos as builder  # noqa: E402

from app.evaluation.replay import real_pipeline as real_pipeline_module  # noqa: E402
from app.evaluation.replay import runner as rn  # noqa: E402
from config import settings  # noqa: E402

MOCK_FIXTURE_PATH = PROJECT_ROOT / "evaluation" / "fixtures" / "replay" / "mock_cases.yaml"

REAL_PIPELINE_MODULE = "app.evaluation.replay.real_pipeline"
DRAFT_MODULE = "app.application.replay_draft"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git 실행 파일이 없는 환경"
)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def project_root(tmp_path_factory) -> Path:
    """mock repo 3개를 tmp 프로젝트 루트 아래 fixture 상대 경로에 빌드한다."""
    root = tmp_path_factory.mktemp("replay_cli_project")
    builder.build_replay_repos(
        source_root=builder.SOURCE_ROOT,
        output_root=root / "evaluation" / "fixtures" / "replay_repos",
    )
    return root


@pytest.fixture
def captured_run(monkeypatch) -> dict:
    """`run_fixtures` 를 가로채 실행 없이 인자만 붙잡는다.

    `--pipeline real` 게이트 검증에는 실제 케이스 실행이 필요 없다 — 오히려 실행하면
    가짜 파이프라인이 worktree 를 만들어 테스트가 무거워진다.
    """
    calls: dict = {}

    def fake_run_fixtures(fixtures, pipeline, project_root, output_dir, **kwargs):
        calls["fixtures"] = fixtures
        calls["pipeline"] = pipeline
        calls["output_dir"] = Path(output_dir)
        return Path(output_dir)

    monkeypatch.setattr(rn, "run_fixtures", fake_run_fixtures)
    return calls


@pytest.fixture
def captured_build(monkeypatch) -> dict:
    """`build_real_pipeline` 을 가짜로 바꾼다 — 인덱싱·검색·생성은 일어나지 않는다."""
    calls: dict = {}

    def fake_build_real_pipeline(**kwargs):
        calls["kwargs"] = kwargs
        return lambda context: None

    monkeypatch.setattr(
        real_pipeline_module, "build_real_pipeline", fake_build_real_pipeline
    )
    return calls


def _real_argv(tmp_path: Path, *extra: str) -> list:
    return [
        "--fixtures",
        str(MOCK_FIXTURE_PATH),
        "--output-dir",
        str(tmp_path / "out"),
        "--pipeline",
        "real",
        *extra,
    ]


# ---------------------------------------------------------------------------
# 기존 사용법 회귀 고정
# ---------------------------------------------------------------------------


def test_stub_only_usage_still_runs_end_to_end(monkeypatch, project_root, tmp_path):
    """`--pipeline` 없이 `--stub perfect` 만 준 형태 — #0018 런북이 쓰는 명령이다."""
    monkeypatch.setattr(rn, "PROJECT_ROOT", project_root)
    output_dir = tmp_path / "stub-out"

    exit_code = rn.main(
        [
            "--fixtures",
            str(MOCK_FIXTURE_PATH),
            "--output-dir",
            str(output_dir),
            "--stub",
            "perfect",
        ]
    )

    assert exit_code == 0
    report = json.loads((output_dir / "replay_report.json").read_text(encoding="utf-8"))
    assert report["summary"]["case_count"] == 3
    assert report["summary"]["pass_rate"] == 1.0


def test_pipeline_stub_with_stub_name_runs(captured_run, tmp_path):
    """새 형태(`--pipeline stub --stub perfect`)도 같은 stub 을 고른다."""
    exit_code = rn.main(
        [
            "--fixtures",
            str(MOCK_FIXTURE_PATH),
            "--output-dir",
            str(tmp_path / "out"),
            "--pipeline",
            "stub",
            "--stub",
            "perfect",
        ]
    )

    assert exit_code == 0
    assert callable(captured_run["pipeline"])


def test_pipeline_stub_requires_a_stub_name(capsys, tmp_path):
    exit_code = rn.main(
        ["--output-dir", str(tmp_path / "out"), "--pipeline", "stub"]
    )

    assert exit_code == 2
    assert "--stub" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_missing_pipeline_and_stub_exits_two(capsys, tmp_path):
    """실제 파이프라인을 기본값으로 붙이지 않는다 (ADR-011)."""
    exit_code = rn.main(["--output-dir", str(tmp_path / "out")])

    assert exit_code == 2
    assert "ERROR" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


# ---------------------------------------------------------------------------
# 외부 전송 게이트 (CLAUDE.md CRITICAL)
# ---------------------------------------------------------------------------


def test_non_local_backend_without_flag_refuses_to_start(
    monkeypatch, capsys, captured_run, captured_build, tmp_path
):
    """`LLM_BACKEND=claude` + 플래그 없음 → 경고 후 exit 2, 파이프라인도 만들지 않는다."""
    monkeypatch.setattr(settings, "llm_backend", "claude")

    exit_code = rn.main(_real_argv(tmp_path))

    assert exit_code == 2
    assert "kwargs" not in captured_build, "게이트를 통과하기 전에 파이프라인을 만들면 안 된다"
    assert "pipeline" not in captured_run, "실행까지 갔으면 게이트가 무의미하다"

    err = capsys.readouterr().err
    assert "claude" in err  # 어느 백엔드인지
    assert "3" in err  # 몇 건이 전송되는지 (mock fixture 3건)
    assert "외부" in err
    assert "--allow-external-llm" in err


def test_non_local_backend_with_flag_proceeds_but_still_warns(
    monkeypatch, capsys, captured_run, captured_build, tmp_path
):
    monkeypatch.setattr(settings, "llm_backend", "claude")

    exit_code = rn.main(_real_argv(tmp_path, "--allow-external-llm"))

    assert exit_code == 0
    assert "kwargs" in captured_build
    assert captured_run["pipeline"] is not None

    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "ERROR" not in err


def test_local_backend_needs_no_flag_and_prints_no_warning(
    monkeypatch, capsys, captured_run, captured_build, tmp_path
):
    monkeypatch.setattr(settings, "llm_backend", "local")

    exit_code = rn.main(_real_argv(tmp_path))

    assert exit_code == 0
    assert "kwargs" in captured_build
    assert "WARNING" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 배선 — draft_fn 주입·index_root·지연 import
# ---------------------------------------------------------------------------


def test_real_branch_injects_a_draft_fn(
    monkeypatch, captured_run, captured_build, tmp_path
):
    """`real_pipeline` 은 초안 생성을 직접 하지 않는다 — CLI 가 주입한다(ADR-012 보강)."""
    monkeypatch.setattr(settings, "llm_backend", "local")

    assert rn.main(_real_argv(tmp_path)) == 0

    kwargs = captured_build["kwargs"]
    assert callable(kwargs["draft_fn"])
    assert kwargs["index_root"] is None  # 생략 시 Step 0 의 기본 루트


def test_index_root_option_is_passed_through(
    monkeypatch, captured_run, captured_build, tmp_path
):
    monkeypatch.setattr(settings, "llm_backend", "local")
    index_root = tmp_path / "replay-index"

    assert rn.main(_real_argv(tmp_path, "--index-root", str(index_root))) == 0

    assert captured_build["kwargs"]["index_root"] == index_root


def test_stub_run_does_not_import_the_real_pipeline(monkeypatch, captured_run, tmp_path):
    """지연 import 확인 — stub 실행은 임베딩·LLM 스택을 끌고 오지 않는다(ADR-011)."""
    monkeypatch.delitem(sys.modules, REAL_PIPELINE_MODULE, raising=False)
    monkeypatch.delitem(sys.modules, DRAFT_MODULE, raising=False)

    exit_code = rn.main(
        [
            "--fixtures",
            str(MOCK_FIXTURE_PATH),
            "--output-dir",
            str(tmp_path / "out"),
            "--pipeline",
            "stub",
            "--stub",
            "empty",
        ]
    )

    assert exit_code == 0
    assert REAL_PIPELINE_MODULE not in sys.modules
    assert DRAFT_MODULE not in sys.modules


def test_runner_module_has_no_heavy_imports_at_top():
    """`runner.py` 상단이 가벼운지 소스로 고정한다 — 지연 import 가 되돌아가는 것을 막는다."""
    source = (PROJECT_ROOT / "app" / "evaluation" / "replay" / "runner.py").read_text(
        encoding="utf-8"
    )
    top = source.split("def main(", 1)[0]
    for token in ("real_pipeline", "app.application", "chromadb", "sentence_transformers"):
        assert f"import {token}" not in top
        assert f"from {token}" not in top
