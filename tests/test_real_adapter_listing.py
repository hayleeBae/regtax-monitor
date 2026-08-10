"""RealCodebaseAdapter.list_files() 의 인덱싱 대상 선별 규칙.

2026-08-05 회사 실측에서 인덱싱 대상 8,100개 중 상당수가
`out/artifacts/eHR_war_exploded/...` 하위였다 — exploded WAR(빌드 산출물)이다.
빌드 산출물을 인덱싱하면 매핑이 재생성되는 경로를 가리켜 patch 가 무의미해지고,
같은 코드가 중복 인덱싱돼 검색 상위를 산출물이 차지한다.

기존 동작(확장자 필터·`.git` 제외·`REPO_INDEX_PATHS` 서브디렉토리 필터)은 그대로
유지되어야 하므로 함께 고정한다.
"""

from __future__ import annotations

import pytest

from app.codebase.real_adapter import RealCodebaseAdapter


def _touch(root, relative: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("// synthetic\n", encoding="utf-8")


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """확장자·디렉토리 조합을 섞은 합성 repo."""
    monkeypatch.setattr("app.codebase.real_adapter.settings.repo_index_paths", "")
    _touch(tmp_path, "src/main/java/com/example/TaxService.java")
    _touch(tmp_path, "src/main/resources/mapper/TaxMapper.xml")
    _touch(tmp_path, "src/main/sql/tax.sql")
    _touch(tmp_path, "README.md")  # 확장자 미대상
    return tmp_path


# ---------------------------------------------------------------------------
# 기존 동작 고정
# ---------------------------------------------------------------------------


def test_lists_source_extensions_only(repo):
    files = RealCodebaseAdapter(str(repo)).list_files()
    assert "src/main/java/com/example/TaxService.java" in files
    assert "src/main/resources/mapper/TaxMapper.xml" in files
    assert "src/main/sql/tax.sql" in files
    assert "README.md" not in files


def test_paths_are_relative_to_repo_root(repo):
    files = RealCodebaseAdapter(str(repo)).list_files()
    assert all(not f.startswith("/") for f in files)


def test_repo_index_paths_limits_roots(repo, monkeypatch):
    _touch(repo, "other/module/Ignored.java")
    monkeypatch.setattr(
        "app.codebase.real_adapter.settings.repo_index_paths", "src"
    )
    files = RealCodebaseAdapter(str(repo)).list_files()
    assert "src/main/java/com/example/TaxService.java" in files
    assert "other/module/Ignored.java" not in files


# ---------------------------------------------------------------------------
# 빌드 산출물 제외 (이번 수정)
# ---------------------------------------------------------------------------


def test_excludes_exploded_war_under_out(repo):
    """회사에서 실제로 관측된 경로 형태 — exploded WAR."""
    _touch(
        repo,
        "out/artifacts/eHR_war_exploded/eHR/solution/pay/pay/com/PayPayCom026.xfdl.js",
    )
    _touch(repo, "out/artifacts/eHR_war_exploded/WEB-INF/classes/TaxService.java")
    files = RealCodebaseAdapter(str(repo)).list_files()
    assert not [f for f in files if f.startswith("out/")]


@pytest.mark.parametrize(
    "relative",
    [
        "target/classes/com/example/TaxService.java",
        "build/generated/Gen.java",
        "out/production/App.java",
        "dist/bundle.js",
        "node_modules/pkg/index.js",
        ".venv/lib/site.py",
        "venv/lib/site.py",
        "__pycache__/mod.py",
        ".svn/tmp/entry.xml",
    ],
)
def test_excludes_build_output_directories(repo, relative):
    _touch(repo, relative)
    files = RealCodebaseAdapter(str(repo)).list_files()
    assert relative not in files


def test_excludes_nested_build_directory(repo):
    """제외 디렉토리가 중간 경로에 있어도 걸러진다."""
    _touch(repo, "modules/payroll/target/classes/Generated.java")
    files = RealCodebaseAdapter(str(repo)).list_files()
    assert "modules/payroll/target/classes/Generated.java" not in files


def test_keeps_source_when_name_is_only_a_substring(repo):
    """`build`/`out` 이 **경로 구성요소**일 때만 제외한다.

    부분일치로 판정하면 `outbound`·`buildings` 같은 정상 디렉토리가 통째로 사라진다.
    """
    _touch(repo, "src/outbound/OutboundService.java")
    _touch(repo, "src/buildings/BuildingService.java")
    _touch(repo, "src/main/java/com/example/BuildHelper.java")
    files = RealCodebaseAdapter(str(repo)).list_files()
    assert "src/outbound/OutboundService.java" in files
    assert "src/buildings/BuildingService.java" in files
    assert "src/main/java/com/example/BuildHelper.java" in files


def test_git_directory_still_excluded(repo):
    """기존에도 제외되던 `.git` 이 계속 제외되는지(회귀 고정)."""
    _touch(repo, ".git/hooks/sample.py")
    files = RealCodebaseAdapter(str(repo)).list_files()
    assert not [f for f in files if f.startswith(".git/")]


def test_excluded_dirs_matches_golden_ignore_vocabulary():
    """`app/golden.py::_IGNORE` 와 같은 어휘를 쓰는지 — 한쪽만 바뀌는 것을 막는다."""
    shared = {
        ".git",
        ".svn",
        "target",
        "build",
        "out",
        "dist",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
    }
    assert shared <= RealCodebaseAdapter.EXCLUDED_DIRS
